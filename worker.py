import multiprocessing
import os
import time

from config import DATA_DIR, OUTPUT_DIR
from deck_generator import run_generation
from jobs import (
    cancellation_requested,
    claim_next_job,
    connect,
    init_db,
    recover_interrupted_jobs,
    update_job,
)
from logging_config import configure_logging


logger = configure_logging().getChild("worker")


def _run_job(job_id: str) -> None:
    from sources import collect_source_text

    with connect() as connection:
        row = connection.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
        job = dict(row) if row else None
    if job is None or cancellation_requested(job_id):
        return
    try:
        source_text = collect_source_text(job["input_text"], job["source_url"], job_id)
        if cancellation_requested(job_id):
            return
        run_generation(job_id, source_text, job["source_url"])
    except Exception as error:
        logger.exception("Job %s failed during source collection or generation", job_id)
        if not cancellation_requested(job_id):
            update_job(
                job_id,
                status="failed",
                progress="Generation failed",
                error=f"{type(error).__name__}: {str(error)[:400]}",
            )


def _cleanup_job_files(job_id: str) -> None:
    if OUTPUT_DIR.exists():
        for path in OUTPUT_DIR.glob(f"*-{job_id[:8]}.pptx"):
            path.unlink(missing_ok=True)


def _finish_cancel(job_id: str) -> None:
    _cleanup_job_files(job_id)
    update_job(job_id, status="cancelled", progress="Cancelled", error="", output_path="")


def _acquire_worker_lock():
    lock_path = DATA_DIR / "worker.lock"
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    handle = lock_path.open("a+b")
    handle.seek(0, os.SEEK_END)
    if handle.tell() == 0:
        handle.write(b"\0")
        handle.flush()
    handle.seek(0)
    try:
        if os.name == "nt":
            import msvcrt

            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl

            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        handle.close()
        return None
    return handle


def _supervise(job_id: str) -> None:
    context = multiprocessing.get_context("spawn")
    process = context.Process(target=_run_job, args=(job_id,), name=f"deck-{job_id[:8]}")
    process.start()
    while process.is_alive():
        process.join(timeout=0.4)
        if cancellation_requested(job_id):
            process.terminate()
            process.join(timeout=5)
            _finish_cancel(job_id)
            return
    process.join()
    with connect() as connection:
        row = connection.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
        job = dict(row) if row else None
    if job and job["status"] == "cancel_requested":
        _finish_cancel(job_id)
    elif job and job["status"] == "running":
        error = "The worker stopped unexpectedly." if process.exitcode else "The agent finished without creating a deck."
        update_job(job_id, status="failed", progress="Generation failed", error=error)


def main() -> None:
    init_db()
    lock = _acquire_worker_lock()
    if lock is None:
        logger.warning("Another Slide Generator worker is already running")
        return
    recover_interrupted_jobs()
    logger.info("Worker started; watching the persistent job queue")
    while True:
        job = claim_next_job()
        if job is None:
            time.sleep(0.7)
            continue
        logger.info("Claimed job %s", job["id"])
        _supervise(job["id"])


if __name__ == "__main__":
    multiprocessing.freeze_support()
    main()