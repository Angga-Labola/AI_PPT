# Fieldnotes Slide Studio

FastAPI application that synchronously turns notes, multiple URLs, and documents into a downloadable PowerPoint with DeepAgents and Gemini. It does not use a database, background worker, queue, or object-storage service.

## Run Locally

Install Python 3.11+ and [uv](https://docs.astral.sh/uv/getting-started/installation/), then install dependencies and set `GOOGLE_API_KEY` in `.env` (copy `.env.example` as a starting point):

```sh
uv sync --locked
uv run --locked python app.py
```

Open `http://127.0.0.1:8000`. In VS Code, use **FastAPI: app.py** under Run and Debug. Set `LOG_LEVEL=DEBUG` to include successful HTTP timing details.

Each request stays open while sources are read, Gemini creates the outline, and the PPTX is written. The API returns the generated file in that same response and saves a copy under `app_data/presentations/`. The browser downloads the response and refreshes the library by scanning the folder. Existing files can be downloaded again or deleted from the library.

## Code Structure

```text
Browser: templates/index.html + static/app.js + static/app.css
    | multipart request
    v
FastAPI routes: app.py
    | validate and extract inputs
    v
Source readers: sources.py
    | combined source text
    v
DeepAgents + Gemini: deck_generator.py ----> rate_limit.py
    |
    v
PPTX renderer: pptx_builder.py
    |
    +---- save file in app_data/presentations/
    +---- return file directly to browser
```

### Modules

- `config.py` loads `.env` and centralizes the API key, model, data/output paths, input limits, slide limit, and rate-budget values. Vercel requests use a smaller upload cap to fit the serverless request-body limit.
- `app.py` creates the FastAPI application, initializes the presentation directory, serves the UI/static files, exposes `/health`, and implements generation/library routes. `POST /api/generate` validates inputs, extracts uploaded documents, reads every URL, accepts optional audience and presentation-goal preferences, calls generation, and returns a `FileResponse`. `GET /api/decks` scans `.pptx` files; download and delete routes operate on a filename after checking that it stays inside the output folder. The `__main__` block starts Uvicorn for `python app.py`.
- `sources.py` fetches public HTTP(S) webpages and validates redirects against private/local IP addresses. It extracts text from web pages and TXT/Markdown, PDF, DOCX, and PPTX documents, labeling each source so slides can cite it. PPTX extraction reads slide text shapes; images and speaker notes are not OCR-processed.
- `deck_generator.py` defines Pydantic slide/deck schemas and the system prompt, creates the DeepAgents graph with Gemini, applies the per-call quota callback, validates model output and source references, and returns the saved deck path.
- `rate_limit.py` is a thread-safe, in-memory rolling RPM/TPM reservation gate used before every Gemini model call. It requires no database. Accounting resets on process restart and is not coordinated across separate server instances.
- `pptx_builder.py` renders a validated outline into a styled `.pptx` containing one cover slide plus the generated content slides, with source citations on each slide and clickable citations for web sources.
- `logging_config.py` configures console logs. `app.py` logs request timings at DEBUG and unhandled request exceptions with tracebacks. `sources.py` and `deck_generator.py` log source/model progress and output paths.
- `templates/index.html` provides the interface structure. `static/app.js` submits the form, starts the returned PPTX download, and renders the folder-backed library. `static/app.css` contains responsive visual styles.
- `pyproject.toml` and `uv.lock` define the Python dependencies. `setup.bat`, `run_app.bat`, and `.vscode/launch.json` support local setup, launch, and debugging.

## Request Flow

1. The browser posts notes, optional audience and presentation goal, up to 10 URLs (one per line), and up to 10 files as multipart form data.
2. FastAPI validates URL and file limits and extracts document text.
3. `sources.py` fetches each URL and combines the text within the configured source budget.
4. `deck_generator.py` calls DeepAgents/Gemini. `rate_limit.py` reserves estimated capacity before each model request.
5. Pydantic validates a 5-25 slide outline and the tool checks that each slide cites known source IDs; `pptx_builder.py` creates the output file with citations.
6. FastAPI returns the PowerPoint in the response. The browser downloads it, while the saved copy remains in the presentations folder.

## Limits And Files

- Up to 10 public HTTP(S) URLs, one per line and 2,048 characters each. Local/private IP addresses are rejected.
- Up to 10 files: PPTX, PDF, DOCX, TXT, or Markdown. Locally, each file is limited to 10 MB and the combined upload to 15 MB. Vercel requests are capped at 4 MB to respect its function body limit.
- Audience and presentation-goal preferences are optional; they are limited to 120 and 300 characters.
- Up to 25 content slides plus one cover slide.
- Output directory: `SLIDES_DATA_DIR/presentations/`; defaults to `app_data/presentations/` locally.

Uploaded source text is sent to Google AI Studio. The in-memory limiter reserves at most 14 calls per rolling minute and 200,000 estimated tokens per minute, below the stated 15 RPM / 250,000 TPM quota. Output is capped at 8,000 tokens. Token accounting is approximate and does not include other users sharing the Google project.

## Vercel Limitation

Vercel can run this synchronous app without Postgres, Queues, Blob, or a worker, provided generation completes before the function timeout. Its filesystem is temporary and instance-local: the PPTX is returned directly to the browser, but saved files and the library are not guaranteed to persist across function restarts or appear on other instances. For durable folder-based storage and a persistent library, deploy to a server/host that provides a persistent writable volume mounted as `SLIDES_DATA_DIR`. Keep secrets in the hosting provider's environment settings and do not commit `.env`.