import asyncio
import ipaddress
import os
import socket
import time
import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urlparse

from fastapi import FastAPI, File, Form, HTTPException, Query, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from langgraph.errors import GraphRecursionError
from vercel import blob
from vercel.queue import send

from config import (
    API_KEY,
    BLOB_READ_WRITE_TOKEN,
    BLOB_STORE_ID,
    DATABASE_URL,
    IS_VERCEL,
    MAX_SOURCE_CHARS,
    MAX_UPLOAD_BYTES,
    MAX_UPLOADS,
    MAX_UPLOAD_TOTAL_BYTES,
    MAX_URLS,
    MAX_URL_LENGTH,
    OUTPUT_DIR,
    TAVILY_API_KEY,
    WEB_RESEARCH_LIMITS,
)
import job_store
from deck_generator import generate_presentation
from deck_queue import DeckJobMessage, deck_jobs
from logging_config import configure_logging
from sources import SourceFetchError, collect_source_text, extract_document
from web_search import WebSearchError


BASE_DIR = Path(__file__).resolve().parent
logger = configure_logging().getChild("web")


@asynccontextmanager
async def lifespan(_: FastAPI):
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    if IS_VERCEL:
        if not DATABASE_URL:
            raise RuntimeError("DATABASE_URL is required for Vercel background jobs.")
        await asyncio.to_thread(job_store.initialize_job_store)
    logger.info(
        "Application started; PowerPoints are stored in %s",
        "Vercel Blob" if IS_VERCEL else OUTPUT_DIR,
    )
    yield


app = FastAPI(title="Fieldnotes Slide Studio", lifespan=lifespan)
app.mount("/static", StaticFiles(directory=BASE_DIR / "static"), name="static")
templates = Jinja2Templates(directory=BASE_DIR / "templates")


@app.middleware("http")
async def log_requests(request: Request, call_next):
    started_at = time.perf_counter()
    try:
        response = await call_next(request)
    except Exception:
        logger.exception("Unhandled request error: %s %s", request.method, request.url.path)
        raise
    elapsed = time.perf_counter() - started_at
    log_method = logger.warning if response.status_code >= 500 else logger.debug
    log_method(
        "Request %s %s returned %s in %.3fs",
        request.method,
        request.url.path,
        response.status_code,
        elapsed,
    )
    return response


@app.get("/health")
async def health():
    return {"status": "ok"}


def _validate_url(url: str) -> None:
    if len(url) > MAX_URL_LENGTH:
        raise HTTPException(400, "Each URL must be 2,048 characters or fewer.")
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise HTTPException(400, "Enter a valid public http(s) URL.")
    if parsed.username or parsed.password:
        raise HTTPException(400, "URLs containing credentials are not supported.")
    try:
        addresses = socket.getaddrinfo(parsed.hostname, parsed.port or (443 if parsed.scheme == "https" else 80))
        if any(not ipaddress.ip_address(item[4][0].split("%", 1)[0]).is_global for item in addresses):
            raise HTTPException(400, "Private or local network URLs are not supported.")
    except socket.gaierror as error:
        raise HTTPException(400, "The source URL could not be resolved.") from error


@app.get("/")
async def home(request: Request):
    return templates.TemplateResponse(request, "index.html")


@app.get("/api/decks")
async def list_decks():
    if IS_VERCEL:
        decks = await asyncio.to_thread(job_store.list_completed_jobs)
        return {
            "decks": [
                {
                    "id": deck["id"],
                    "title": (deck["metadata"] or {}).get("title", deck["filename"]),
                    "filename": deck["filename"],
                    "updated_at": deck["updated_at"].isoformat(),
                }
                for deck in decks
            ]
        }
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    decks = sorted(OUTPUT_DIR.glob("*.pptx"), key=lambda path: path.stat().st_mtime, reverse=True)
    return {
        "decks": [
            {
                "id": deck.name,
                "title": deck.stem.rsplit("-", 1)[0].replace("-", " ").title(),
                "filename": deck.name,
                "updated_at": datetime.fromtimestamp(deck.stat().st_mtime, timezone.utc).isoformat(),
            }
            for deck in decks
        ]
    }


@app.post("/api/generate")
async def generate_deck(
    text: str = Form(default=""),
    url: str = Form(default=""),
    audience: str = Form(default="", max_length=120),
    goal: str = Form(default="", max_length=300),
    research_depth: str = Form(default="off"),
    include_metadata_form: bool = Form(default=False),
    include_metadata: bool = Query(default=False),
    files: list[UploadFile] = File(default=[]),
):
    text = text.strip()
    audience = audience.strip()
    goal = goal.strip()
    if research_depth not in WEB_RESEARCH_LIMITS:
        raise HTTPException(400, "Choose a valid web research depth.")
    if research_depth != "off" and not TAVILY_API_KEY:
        raise HTTPException(
            503,
            "Web research is not configured. Set TAVILY_API_KEY, or choose Off.",
        )
    urls = [line.strip() for line in url.splitlines() if line.strip()]
    if not text and not urls and not files:
        raise HTTPException(400, "Add text, a document, or a URL to start.")
    if len(files) > MAX_UPLOADS:
        raise HTTPException(400, f"Upload up to {MAX_UPLOADS} documents at a time.")
    if len(urls) > MAX_URLS:
        raise HTTPException(400, f"Add up to {MAX_URLS} URLs at a time.")
    for source_url in urls:
        _validate_url(source_url)

    if not API_KEY:
        raise HTTPException(503, "GOOGLE_API_KEY is not configured.")
    if IS_VERCEL and not (BLOB_STORE_ID or BLOB_READ_WRITE_TOKEN):
        raise HTTPException(
            503,
            "Vercel Blob is not configured. Set BLOB_STORE_ID or BLOB_READ_WRITE_TOKEN.",
        )
    parts = []
    source_catalog = {}
    if text:
        source_id = f"S{len(source_catalog) + 1}"
        source_catalog[source_id] = "Pasted notes"
        parts.append(f"[{source_id}] Pasted notes:\n{text}")
    total_bytes = 0
    if len(text) > MAX_SOURCE_CHARS:
        raise HTTPException(413, f"Pasted text must be {MAX_SOURCE_CHARS:,} characters or fewer.")
    for upload in files:
        content = await upload.read(MAX_UPLOAD_BYTES + 1)
        total_bytes += len(content)
        if len(content) > MAX_UPLOAD_BYTES:
            raise HTTPException(413, f"{upload.filename} exceeds the 10 MB file limit.")
        try:
            extracted = await asyncio.to_thread(extract_document, upload.filename or "document", content)
        except Exception as error:
            raise HTTPException(400, str(error)) from error
        source_id = f"S{len(source_catalog) + 1}"
        source_catalog[source_id] = f"Document: {upload.filename}"
        parts.append(f"[{source_id}] Document: {upload.filename}\n{extracted}")
    if total_bytes > MAX_UPLOAD_TOTAL_BYTES:
        raise HTTPException(413, "Combined document uploads must be 15 MB or smaller.")
    url_source_start = len(source_catalog)
    request_id = uuid.uuid4().hex
    relevant_links: list[dict[str, str]] = []
    search_results: list[dict[str, str]] = []
    source_warnings: list[str] = []
    research_stats: dict[str, int] = {}
    try:
        source_text, readable_url_indexes, source_warnings = await asyncio.to_thread(
            collect_source_text,
            "\n\n".join(parts),
            "\n".join(urls),
            request_id,
            url_source_start,
        )
        for index, source_url in enumerate(urls, start=1):
            if index in readable_url_indexes:
                source_catalog[f"S{url_source_start + index}"] = source_url
        if IS_VERCEL:
            payload = {
                "source_text": source_text,
                "source_urls": urls,
                "audience": audience,
                "goal": goal,
                "source_catalog": source_catalog,
                "research_depth": research_depth,
                "source_warnings": source_warnings,
            }
            await asyncio.to_thread(job_store.create_job, request_id, payload)
            try:
                await send(
                    deck_jobs,
                    DeckJobMessage(job_id=request_id),
                    idempotency_key=f"deck-job-{request_id}",
                    retention=timedelta(days=7),
                )
            except Exception as error:
                await asyncio.to_thread(
                    job_store.fail_queued_job,
                    request_id,
                    f"Queue publish failed: {type(error).__name__}",
                )
                logger.exception("Could not enqueue generation job %s", request_id)
                raise HTTPException(503, "The generation queue is unavailable. Please retry.") from error
            return JSONResponse(
                {"job_id": request_id, "status": "queued"},
                status_code=202,
            )
        output_path = await asyncio.to_thread(
            generate_presentation,
            request_id,
            source_text,
            urls,
            audience,
            goal,
            source_catalog,
            research_depth,
            relevant_links,
            research_stats,
            search_results,
        )
    except GraphRecursionError as error:
        logger.warning("Generation request %s exceeded the agent step limit", request_id)
        raise HTTPException(
            502,
            "Deck generation took too many agent steps. Try again with fewer sources or a shorter request.",
        ) from error
    except SourceFetchError as error:
        logger.warning("Generation request %s could not read source URLs: %s", request_id, error)
        raise HTTPException(502, str(error)) from error
    except WebSearchError as error:
        logger.warning("Generation request %s web research failed: %s", request_id, error)
        raise HTTPException(502, str(error)) from error
    except ValueError as error:
        logger.warning("Generation request %s rejected: %s", request_id, error)
        raise HTTPException(400, str(error)) from error
    except Exception as error:
        logger.exception("Generation request %s failed", request_id)
        raise HTTPException(502, "PowerPoint generation failed. Check server logs.") from error

    if include_metadata or include_metadata_form:
        return JSONResponse(
            {
                "title": output_path.stem.rsplit("-", 1)[0].replace("-", " ").title(),
                "filename": output_path.name,
                "download_url": f"/api/decks/{output_path.name}/download",
                "relevant_links": relevant_links,
                "search_results": search_results,
                "research_depth": research_depth,
                "research_stats": research_stats,
                "source_warnings": source_warnings,
            }
        )
    return FileResponse(
        output_path,
        filename=output_path.name,
        media_type="application/vnd.openxmlformats-officedocument.presentationml.presentation",
    )


def _public_job(job: dict) -> dict:
    return {
        "job_id": job["id"],
        "status": job["status"],
        "created_at": job["created_at"].isoformat(),
        "updated_at": job["updated_at"].isoformat(),
        "error": job["error"],
        "filename": job["filename"],
        "metadata": job["metadata"],
    }


@app.get("/api/jobs/{job_id}")
async def get_job_status(job_id: str):
    if not IS_VERCEL:
        raise HTTPException(404, "Background jobs are available on Vercel.")
    job = await asyncio.to_thread(job_store.get_job, job_id)
    if not job:
        raise HTTPException(404, "Generation job not found.")
    return _public_job(job)


@app.post("/api/jobs/{job_id}/cancel")
async def cancel_job(job_id: str):
    if not IS_VERCEL:
        raise HTTPException(404, "Background jobs are available on Vercel.")
    job = await asyncio.to_thread(job_store.request_job_cancellation, job_id)
    if not job:
        raise HTTPException(404, "Generation job not found.")
    return {"job_id": job["id"], "status": job["status"]}


@app.get("/api/decks/{filename}/download")
async def download_deck(filename: str):
    if Path(filename).name != filename or not filename.lower().endswith(".pptx"):
        raise HTTPException(404, "PowerPoint not found.")
    if IS_VERCEL:
        deck = await asyncio.to_thread(job_store.get_completed_job_by_filename, filename)
        if not deck:
            raise HTTPException(404, "PowerPoint not found.")
        result = await blob.get_blob(deck["blob_pathname"], access="private")
        return Response(
            content=result.body,
            media_type="application/vnd.openxmlformats-officedocument.presentationml.presentation",
            headers={"Content-Disposition": f'attachment; filename="{filename}"'},
        )
    output_path = (OUTPUT_DIR / filename).resolve()
    if output_path.parent != OUTPUT_DIR.resolve() or not output_path.is_file():
        raise HTTPException(404, "PowerPoint not found.")
    return FileResponse(
        output_path,
        filename=output_path.name,
        media_type="application/vnd.openxmlformats-officedocument.presentationml.presentation",
    )


@app.delete("/api/decks/{filename}")
async def remove_deck(filename: str):
    if Path(filename).name != filename or not filename.lower().endswith(".pptx"):
        raise HTTPException(404, "PowerPoint not found.")
    if IS_VERCEL:
        deck = await asyncio.to_thread(job_store.get_completed_job_by_filename, filename)
        if not deck:
            raise HTTPException(404, "PowerPoint not found.")
        await blob.delete_blob(deck["blob_pathname"])
        await asyncio.to_thread(job_store.delete_completed_job, filename)
        logger.info("Deleted presentation %s from Blob", filename)
        return {"deleted": True}
    output_path = (OUTPUT_DIR / filename).resolve()
    if output_path.parent != OUTPUT_DIR.resolve() or not output_path.is_file():
        raise HTTPException(404, "PowerPoint not found.")
    output_path.unlink()
    logger.info("Deleted presentation %s", filename)
    return {"deleted": True}


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        "app:app",
        host=os.getenv("HOST", "127.0.0.1"),
        port=int(os.getenv("PORT", "8000")),
        reload=os.getenv("APP_RELOAD", "false").lower() in {"1", "true", "yes"},
    )