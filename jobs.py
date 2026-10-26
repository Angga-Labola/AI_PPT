import sqlite3
import time
import uuid
from datetime import datetime, timezone

from config import DATABASE_PATH, DATA_DIR


RPM_LIMIT = 14
TPM_BUDGET = 200_000
MIN_CALL_INTERVAL_SECONDS = 4.3


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def connect() -> sqlite3.Connection:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(DATABASE_PATH, timeout=30)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA journal_mode=WAL")
    connection.execute("PRAGMA busy_timeout=30000")
    return connection


def init_db() -> None:
    with connect() as connection:
        connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS jobs (
                id TEXT PRIMARY KEY,
                status TEXT NOT NULL,
                progress TEXT NOT NULL DEFAULT 'Waiting for the worker',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                input_text TEXT NOT NULL,
                source_url TEXT NOT NULL DEFAULT '',
                title TEXT NOT NULL DEFAULT '',
                output_path TEXT NOT NULL DEFAULT '',
                error TEXT NOT NULL DEFAULT ''
            );
            CREATE INDEX IF NOT EXISTS jobs_status_created
                ON jobs(status, created_at);
            CREATE TABLE IF NOT EXISTS gemini_calls (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                created_at REAL NOT NULL,
                reserved_tokens INTEGER NOT NULL,
                job_id TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS gemini_calls_created
                ON gemini_calls(created_at);
            """
        )


def create_job(input_text: str, source_url: str) -> dict:
    job_id = uuid.uuid4().hex
    now = utc_now()
    with connect() as connection:
        connection.execute("BEGIN IMMEDIATE")
        active = connection.execute(
            "SELECT id FROM jobs WHERE status IN ('queued', 'running', 'cancel_requested') LIMIT 1"
        ).fetchone()
        if active:
            raise RuntimeError("A presentation request is already in progress.")
        connection.execute(
            """INSERT INTO jobs (id, status, created_at, updated_at, input_text, source_url)
               VALUES (?, 'queued', ?, ?, ?, ?)""",
            (job_id, now, now, input_text, source_url),
        )
    return get_job(job_id)


def get_job(job_id: str) -> dict | None:
    with connect() as connection:
        row = connection.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
    return dict(row) if row else None


def list_jobs(limit: int = 50) -> list[dict]:
    with connect() as connection:
        rows = connection.execute(
            "SELECT * FROM jobs WHERE status = 'completed' ORDER BY updated_at DESC LIMIT ?",
            (limit,),
        ).fetchall()
    return [dict(row) for row in rows]


def active_job() -> dict | None:
    with connect() as connection:
        row = connection.execute(
            "SELECT * FROM jobs WHERE status IN ('queued', 'running', 'cancel_requested') "
            "ORDER BY created_at LIMIT 1"
        ).fetchone()
    return dict(row) if row else None


def update_job(job_id: str, **values) -> None:
    allowed = {"status", "progress", "updated_at", "title", "output_path", "error"}
    updates = {key: value for key, value in values.items() if key in allowed}
    updates["updated_at"] = utc_now()
    assignments = ", ".join(f"{key} = ?" for key in updates)
    with connect() as connection:
        connection.execute(
            f"UPDATE jobs SET {assignments} WHERE id = ?",
            (*updates.values(), job_id),
        )


def complete_job(job_id: str, *, title: str, output_path: str) -> bool:
    with connect() as connection:
        cursor = connection.execute(
            """UPDATE jobs SET status = 'completed', progress = ?, title = ?,
               output_path = ?, error = '', updated_at = ?
               WHERE id = ? AND status = 'running'""",
            ("Ready to download", title, output_path, utc_now(), job_id),
        )
    return cursor.rowcount == 1


def claim_next_job() -> dict | None:
    with connect() as connection:
        connection.execute("BEGIN IMMEDIATE")
        row = connection.execute(
            "SELECT id FROM jobs WHERE status = 'queued' ORDER BY created_at LIMIT 1"
        ).fetchone()
        if row is None:
            return None
        connection.execute(
            "UPDATE jobs SET status = 'running', progress = ?, updated_at = ? WHERE id = ?",
            ("Preparing your source material", utc_now(), row["id"]),
        )
    return get_job(row["id"])


def recover_interrupted_jobs() -> None:
    with connect() as connection:
        connection.execute("BEGIN IMMEDIATE")
        now = utc_now()
        connection.execute(
            """UPDATE jobs SET status = 'queued', progress = ?, updated_at = ?
               WHERE status = 'running'""",
            ("Resuming after worker restart", now),
        )
        connection.execute(
            """UPDATE jobs SET status = 'cancelled', progress = ?, updated_at = ?
               WHERE status = 'cancel_requested'""",
            ("Cancelled after worker stopped", now),
        )


def cancellation_requested(job_id: str) -> bool:
    job = get_job(job_id)
    return job is None or job["status"] == "cancel_requested"


def request_cancel(job_id: str) -> dict | None:
    with connect() as connection:
        connection.execute("BEGIN IMMEDIATE")
        row = connection.execute("SELECT status FROM jobs WHERE id = ?", (job_id,)).fetchone()
        if row is None:
            return None
        if row["status"] == "queued":
            connection.execute(
                "UPDATE jobs SET status = 'cancelled', progress = ?, updated_at = ? WHERE id = ?",
                ("Cancelled", utc_now(), job_id),
            )
        elif row["status"] == "running":
            connection.execute(
                "UPDATE jobs SET status = 'cancel_requested', progress = ?, updated_at = ? WHERE id = ?",
                ("Cancelling request", utc_now(), job_id),
            )
    return get_job(job_id)


def delete_job(job_id: str) -> bool:
    with connect() as connection:
        cursor = connection.execute(
            "DELETE FROM jobs WHERE id = ? AND status IN ('completed', 'failed', 'cancelled')",
            (job_id,),
        )
    return cursor.rowcount > 0


def reserve_gemini_capacity(job_id: str, tokens: int) -> bool:
    if tokens > TPM_BUDGET:
        raise ValueError("This source is too large to fit within the configured Gemini token budget.")
    while not cancellation_requested(job_id):
        now = time.time()
        with connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            recent = connection.execute(
                "SELECT created_at, reserved_tokens FROM gemini_calls WHERE created_at > ? ORDER BY created_at",
                (now - 60,),
            ).fetchall()
            last_call = connection.execute(
                "SELECT created_at FROM gemini_calls ORDER BY created_at DESC LIMIT 1"
            ).fetchone()
            under_rpm = len(recent) < RPM_LIMIT
            under_tpm = sum(row["reserved_tokens"] for row in recent) + tokens <= TPM_BUDGET
            interval_elapsed = (
                last_call is None
                or now - last_call["created_at"] >= MIN_CALL_INTERVAL_SECONDS
            )
            if under_rpm and under_tpm and interval_elapsed:
                connection.execute(
                    "INSERT INTO gemini_calls (created_at, reserved_tokens, job_id) VALUES (?, ?, ?)",
                    (now, tokens, job_id),
                )
                connection.execute("DELETE FROM gemini_calls WHERE created_at < ?", (now - 120,))
                return True
        if not cancellation_requested(job_id):
            time.sleep(0.5)
    return False