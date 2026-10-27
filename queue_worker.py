"""Process durable Vercel Queue messages and save finished decks to Blob."""

import asyncio

from vercel import blob
from vercel.queue import subscribe

import job_store
from config import OUTPUT_DIR
from deck_generator import GenerationCancelled, generate_presentation
from deck_queue import DeckJobMessage, deck_jobs
from logging_config import configure_logging
from notifications import send_deck_notification


logger = configure_logging().getChild("queue_worker")
PPTX_MEDIA_TYPE = "application/vnd.openxmlformats-officedocument.presentationml.presentation"


async def _keep_job_lease(job_id: str) -> None:
    """Refresh the job timestamp so another delivery knows the worker is alive."""
    while True:
        await asyncio.sleep(20)
        if await asyncio.to_thread(job_store.heartbeat_or_cancel, job_id):
            return


@subscribe(
    topic=deck_jobs,
    consumer_group="deck-generator",
    max_attempts=2,
    max_concurrency=1,
)
async def process_deck_job(message: DeckJobMessage) -> None:
    """Claim, generate, upload, and finalize one queued job."""
    await asyncio.to_thread(job_store.initialize_job_store)
    job = await asyncio.to_thread(job_store.claim_job, message.job_id)
    if job is None:
        logger.info("Queue delivery for %s is already claimed or terminal", message.job_id)
        return

    # Only the queue ID is sent through Vercel; source content stays in Neon.
    payload = job["payload"]
    # The heartbeat runs beside generation, allowing cancel requests to be seen.
    try:
        relevant_links: list[dict[str, str]] = []
        search_results: list[dict[str, str]] = []
        research_stats: dict[str, int] = {}
        model_call_counter = [0]
        lease_task = asyncio.create_task(_keep_job_lease(message.job_id))
        try:
            try:
                output_path = await asyncio.to_thread(
                    generate_presentation,
                    message.job_id,
                    payload["source_text"],
                    payload["source_urls"],
                    payload["audience"],
                    payload["goal"],
                    payload["source_catalog"],
                    payload["research_depth"],
                    relevant_links,
                    research_stats,
                    search_results,
                    lambda: job_store.heartbeat_or_cancel(message.job_id),
                    model_call_counter,
                )
            finally:
                await asyncio.to_thread(
                    job_store.add_model_api_calls,
                    message.job_id,
                    model_call_counter[0],
                )
        finally:
            lease_task.cancel()
            try:
                await lease_task
            except asyncio.CancelledError:
                pass
        if await asyncio.to_thread(job_store.heartbeat_or_cancel, message.job_id):
            await asyncio.to_thread(job_store.mark_job_cancelled, message.job_id)
            return
        blob_pathname = f"decks/{message.job_id}/{output_path.name}"
        # Private access means a deck can only be downloaded through our app routes.
        uploaded = await blob.put_blob(
            blob_pathname,
            output_path.read_bytes(),
            access="private",
            content_type=PPTX_MEDIA_TYPE,
            allow_overwrite=True,
        )
        metadata = {
            "title": output_path.stem.rsplit("-", 1)[0].replace("-", " ").title(),
            "relevant_links": relevant_links,
            "search_results": search_results,
            "research_stats": research_stats,
            "source_warnings": payload["source_warnings"],
            "research_depth": payload["research_depth"],
            "gemini_api_calls": await asyncio.to_thread(
                job_store.get_model_api_calls,
                message.job_id,
            ),
        }
        completed = await asyncio.to_thread(
            job_store.finish_job,
            message.job_id,
            output_path.name,
            uploaded.pathname,
            metadata,
        )
        if not completed:
            await blob.delete_blob(uploaded.pathname)
            await asyncio.to_thread(job_store.mark_job_cancelled, message.job_id)
        else:
            try:
                await send_deck_notification(
                    succeeded=True,
                    title=metadata["title"],
                    gemini_api_calls=metadata["gemini_api_calls"],
                    filename=output_path.name,
                )
            except Exception:
                logger.exception("Could not send completion email for job %s", message.job_id)
        logger.info("Queue job %s finished", message.job_id)
    except GenerationCancelled:
        await asyncio.to_thread(job_store.mark_job_cancelled, message.job_id)
        logger.info("Queue job %s was cancelled", message.job_id)
    except Exception as error:
        # Retry once, then retain a final failed record and notify the configured user.
        should_retry = await asyncio.to_thread(
            job_store.record_job_error,
            message.job_id,
            "Generation failed. Check server logs.",
        )
        if should_retry:
            logger.exception("Queue job %s failed; requesting a queue retry", message.job_id)
            raise
        logger.exception("Queue job %s failed permanently", message.job_id)
        try:
            job = await asyncio.to_thread(job_store.get_job, message.job_id)
            if not job or job["status"] != "failed":
                return
            gemini_api_calls = await asyncio.to_thread(
                job_store.get_model_api_calls,
                message.job_id,
            )
            await send_deck_notification(
                succeeded=False,
                title="Your presentation",
                gemini_api_calls=gemini_api_calls,
            )
        except Exception:
            logger.exception("Could not send failure email for job %s", message.job_id)
