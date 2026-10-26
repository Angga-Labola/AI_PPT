import asyncio
import ipaddress
import os
import socket
import time
from contextlib import asynccontextmanager
from pathlib import Path
from urllib.parse import urlparse

from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from config import (
    MAX_UPLOAD_BYTES,
    MAX_UPLOADS,
    MAX_UPLOAD_TOTAL_BYTES,
    MAX_URLS,
    MAX_URL_LENGTH,
    OUTPUT_DIR,
)
from jobs import active_job, create_job, delete_job, get_job, init_db, list_jobs, request_cancel
from logging_config import configure_logging


BASE_DIR = Path(__file__).resolve().parent
logger = configure_logging().getChild("web")


@asynccontextmanager
async def lifespan(_: FastAPI):
    init_db()
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    logger.info("Application started; presentation storage is %s", OUTPUT_DIR)
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


def _job_json(job: dict) -> dict:
    return {
        "id": job["id"],
        "status": job["status"],
        "progress": job["progress"],
        "created_at": job["created_at"],
        "updated_at": job["updated_at"],
        "title": job["title"],
        "error": job["error"],
        "filename": Path(job["output_path"]).name if job["output_path"] else "",
    }


@app.get("/")
async def home(request: Request):
    return templates.TemplateResponse(request, "index.html")


@app.get("/api/jobs/active")
async def get_active_job():
    job = active_job()
    return {"job": _job_json(job) if job else None}


@app.get("/api/jobs")
async def get_jobs():
    return {"jobs": [_job_json(job) for job in list_jobs()]}


@app.get("/api/jobs/{job_id}")
async def get_job_status(job_id: str):
    job = get_job(job_id)
    if not job:
        raise HTTPException(404, "Request not found.")
    return _job_json(job)


@app.post("/api/jobs")
async def submit_job(
    text: str = Form(default=""),
    url: str = Form(default=""),
    files: list[UploadFile] = File(default=[]),
):
    from sources import extract_document

    text = text.strip()
    urls = [line.strip() for line in url.splitlines() if line.strip()]
    if not text and not urls and not files:
        raise HTTPException(400, "Add text, a document, or a URL to start.")
    if len(files) > MAX_UPLOADS:
        raise HTTPException(400, f"Upload up to {MAX_UPLOADS} documents at a time.")
    if len(urls) > MAX_URLS:
        raise HTTPException(400, f"Add up to {MAX_URLS} URLs at a time.")
    for source_url in urls:
        _validate_url(source_url)

    parts = [text] if text else []
    total_bytes = 0
    for upload in files:
        content = await upload.read(MAX_UPLOAD_BYTES + 1)
        total_bytes += len(content)
        if len(content) > MAX_UPLOAD_BYTES:
            raise HTTPException(413, f"{upload.filename} exceeds the 10 MB file limit.")
        try:
            extracted = await asyncio.to_thread(extract_document, upload.filename or "document", content)
        except Exception as error:
            raise HTTPException(400, str(error)) from error
        parts.append(f"Document: {upload.filename}\n{extracted}")
    if total_bytes > MAX_UPLOAD_TOTAL_BYTES:
        raise HTTPException(413, "Combined document uploads must be 15 MB or smaller.")
    try:
        job = create_job("\n\n".join(parts), "\n".join(urls))
    except RuntimeError as error:
        logger.info("Rejected new request because another job is active")
        raise HTTPException(409, str(error)) from error
    logger.info(
        "Queued job %s with %d URLs, %d documents, and %d extracted text characters",
        job["id"],
        len(urls),
        len(files),
        sum(map(len, parts)),
    )
    return JSONResponse(_job_json(job), status_code=202)


@app.post("/api/jobs/{job_id}/cancel")
async def cancel_job(job_id: str):
    job = request_cancel(job_id)
    if not job:
        raise HTTPException(404, "Request not found.")
    if job["status"] in {"completed", "failed"}:
        raise HTTPException(409, "This request has already finished.")
    logger.info("Cancellation requested for job %s", job_id)
    return _job_json(job)


@app.get("/api/decks/{job_id}/download")
async def download_deck(job_id: str):
    job = get_job(job_id)
    if not job or job["status"] != "completed":
        raise HTTPException(404, "PowerPoint not found.")
    output_path = Path(job["output_path"]).resolve()
    if output_path.parent != OUTPUT_DIR.resolve() or not output_path.is_file():
        raise HTTPException(404, "PowerPoint file is missing.")
    return FileResponse(
        output_path,
        filename=output_path.name,
        media_type="application/vnd.openxmlformats-officedocument.presentationml.presentation",
    )


@app.delete("/api/decks/{job_id}")
async def remove_deck(job_id: str):
    job = get_job(job_id)
    if not job or job["status"] != "completed":
        raise HTTPException(404, "PowerPoint not found.")
    output_path = Path(job["output_path"]).resolve()
    if output_path.parent == OUTPUT_DIR.resolve():
        output_path.unlink(missing_ok=True)
    delete_job(job_id)
    logger.info("Deleted presentation for job %s", job_id)
    return {"deleted": True}


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        "app:app",
        host=os.getenv("HOST", "127.0.0.1"),
        port=int(os.getenv("PORT", "8000")),
        reload=os.getenv("APP_RELOAD", "false").lower() in {"1", "true", "yes"},
    )