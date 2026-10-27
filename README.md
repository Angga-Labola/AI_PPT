# Fieldnotes Slide Studio

FastAPI application that turns notes, multiple URLs, and documents into a downloadable PowerPoint with LangGraph and Gemini. On Vercel, generation runs as a durable background job using Vercel Queues, Neon Postgres for job state, and Vercel Blob for persistent PowerPoint storage.

## Local Debugging

Set `GENERATION_MODE=local` in `.env`, configure `GOOGLE_API_KEY`, and set the Mailjet values described below, including `NOTIFICATION_EMAIL`. Run `python app.py` from the repository folder and open `http://localhost:8000`. Local mode generates synchronously, saves the deck under `app_data/presentations/`, starts the browser download as soon as generation completes, and emails the configured recipient with the result and Gemini model-call count. Generation failures send a retry notification. Email delivery errors are logged without preventing the deck download. Leave `GENERATION_MODE=auto` on Vercel; its Vercel environment variable continues selecting background jobs.

## Vercel Deployment

Deploy this repository to Vercel with Python 3.12 or newer. Set `GOOGLE_API_KEY`, `DATABASE_URL`, and optionally `TAVILY_API_KEY` in the Vercel project's environment variables.

On Vercel, the API prepares the sources, stores the job and extracted source material in Neon, publishes a durable queue message, and returns a job ID without waiting for Gemini generation. A Vercel Queue subscriber runs generation after the browser closes, uploads the completed deck to a private Vercel Blob store, and records the result in Neon. The browser remembers its active job across reloads; a running job can be cancelled.

## Vercel Setup

1. Connect a Neon Postgres database and set `DATABASE_URL` in the Vercel project. Use Neon's pooled connection string.
2. Create and connect a **private** Vercel Blob store to the project (for example, `vercel blob create-store Decks --access private --yes`). Configure its `BLOB_STORE_ID` in the Vercel project's Production environment variables when using OIDC. Alternatively, configure `BLOB_READ_WRITE_TOKEN` for token-based access. Do not commit either value. Redeploy after changing environment variables.
3. Enable Vercel Queues for the project. Queues are a Vercel beta feature and are available on Hobby; the current Hobby allowance includes 1 million queue operations per month.
4. Configure `GOOGLE_API_KEY`, and optionally `TAVILY_API_KEY`, in the Vercel project environment.
5. Configure `MAILJET_API_KEY`, `MAILJET_API_SECRET`, `MAILJET_FROM_EMAIL` (a verified Mailjet sender), and `NOTIFICATION_EMAIL` (the fixed recipient). Optionally set `MAILJET_FROM_NAME` and `APP_BASE_URL` if Vercel's automatic production URL is not the desired email link. Generation requests are rejected before starting if required Mailjet settings are missing. Completion/final-failure emails include the count of Gemini model requests; Tavily calls are not included. Mail delivery errors are logged and do not change the job's deck result.
6. Set a strong random `CRON_SECRET` in Vercel Production environment variables. A daily Vercel Cron run removes terminal job records at least seven days old. Completed decks remain listed and downloadable: their private Blob objects and small `deck_downloads` index records containing the filename, Blob path, title, and completion time are retained until the deck is explicitly deleted. The daily schedule can make job-record removal occur up to roughly 24 hours after the seven-day threshold.

Vercel Hobby Functions have a 300-second maximum duration. Queue messages are retained for up to 7 days and the Hobby allowance currently includes 1 million queue operations per month. Blob's Hobby allowance currently includes 1 GB of storage. Queue delivery is at-least-once, so the subscriber claims jobs idempotently and retries one failed attempt. Cancellation is cooperative: it is recorded immediately and generation stops at the next model-call or research boundary; a Gemini call already in progress cannot be interrupted. Jobs that exceed the function duration may restart from the beginning and can fail after the retry limit. This is a bounded background-job setup, not an unlimited worker.

Neon stores extracted source text only while a job is pending or running; payload is cleared on completion, failure, or cancellation. Detailed terminal job records are removed by the daily cleanup after seven days. A smaller download-index row and the Blob file remain for completed decks until the user deletes the deck, preserving the app library and email download links. Configure usage alerts and review the current free-tier quotas before deploying.

## Code Structure

```text
Browser: templates/index.html + static/app.js + static/app.css
    | multipart request
    v
FastAPI routes: app.py
    | validate and extract inputs; persist job
    v
Neon job record + Vercel Queue
    |
    v
Queue subscriber: queue_worker.py ----> sources.py + deck_generator.py
    | combined source text
    v
LangGraph + Gemini staged workflow: deck_generator.py ----> rate_limit.py
    v
PPTX renderer: pptx_builder.py
    |
    +---- local execution: app_data/presentations/
    +---- Vercel: private Vercel Blob; job metadata in Neon
```

Vercel's daily `/api/cron/purge-terminal-jobs` endpoint deletes only terminal job rows older than seven days. Completed deck downloads and library entries are resolved from the separate minimal `deck_downloads` index, which is intentionally retained until the corresponding deck is deleted.

### Modules

- `config.py` loads `.env` and centralizes the API keys, `DATABASE_URL`, model, data/output paths, input limits, slide limit, and rate-budget values. Vercel requests use a smaller upload cap to fit the serverless request-body limit.
- `app.py` creates the FastAPI application, initializes storage, serves the UI/static files, exposes `/health`, and implements generation, job, and library routes. On Vercel, `POST /api/generate` saves validated job input in Neon and publishes it to Vercel Queues; job status and cancellation routes read/update the durable job record. Deck download and delete routes use its private Blob pathname.
- `job_store.py` owns the Neon schema and atomic job-state transitions.
- `queue_worker.py` is the Vercel Queue subscriber. It claims jobs idempotently, runs the staged generator, checks cancellation between model calls, and saves the PowerPoint to Blob.
- `sources.py` fetches public HTTP(S) webpages and validates redirects against private/local IP addresses. It extracts text from web pages and TXT/Markdown, PDF, DOCX, and PPTX documents, labeling each source so slides can cite it. PPTX extraction reads slide text shapes; images and speaker notes are not OCR-processed.
- `deck_generator.py` runs a bounded LangGraph workflow with separate research planning, source research, outline, per-slide content, validation, and render/visual-QA stages. It retains Gemini and Tavily, validates model output and source references, and allows one correction per slide before failing.
- `rate_limit.py` is a thread-safe, in-memory rolling RPM/TPM reservation gate used before every Gemini model call. It requires no database. Accounting resets on process restart and is not coordinated across separate server instances.
- `pptx_builder.py` renders validated slide content into a styled `.pptx` containing one cover slide plus the generated content slides, with per-bullet source citations, blue text for Tavily web-researched points, clickable web citations, and a fitted body font for dense slides.
- `visual_qa.py` renders temporary PNG previews of generated slides and checks slide bounds and text-box fit before the deck is saved to its final path.
- `logging_config.py` configures console logs. `app.py` logs request timings at DEBUG and unhandled request exceptions with tracebacks. `sources.py` and `deck_generator.py` log source/model progress and output paths.
- `templates/index.html` provides the interface structure. `static/app.js` submits the form, restores and polls active jobs, offers cancellation, downloads completed PPTX files, and renders the deck library. `static/app.css` contains responsive visual styles.
- `pyproject.toml` and `uv.lock` define the Python dependencies and Vercel queue-subscriber entrypoint.

## Request Flow

1. The browser posts notes, optional audience and presentation goal, a web-research depth, up to 10 URLs (one per line), and up to 10 files as multipart form data.
2. FastAPI validates URL and file limits and extracts document text.
3. `sources.py` fetches each URL and combines the text within the configured source budget.
4. `deck_generator.py` runs research planning, bounded Tavily research, outline generation, and per-slide content generation as separate LangGraph stages. `rate_limit.py` reserves estimated capacity before every Gemini request.
5. Pydantic validates the outline and deck, source IDs are checked against the source catalog, content density is checked, and one correction attempt is allowed.
6. `pptx_builder.py` creates a candidate PowerPoint; `visual_qa.py` renders temporary slide previews and verifies text fit and slide bounds before the file is finalized.
7. On Vercel, the API responds with a job ID; the browser polls Neon-backed job status, offers cancellation, and downloads the finished deck through the app. The job continues if the browser is closed. In local mode, generation runs synchronously, sends a Mailjet notification, and the browser downloads the PowerPoint as soon as generation completes.

## Limits And Files

- Up to 10 public HTTP(S) URLs, one per line and 2,048 characters each. Local/private IP addresses are rejected.
- Combined pasted notes, extracted documents, and direct URL text are limited to 100,000 characters per presentation.
- Up to 10 files: PPTX, PDF, DOCX, TXT, or Markdown. Locally, each file is limited to 10 MB and the combined upload to 15 MB. Vercel requests are capped at 4 MB to respect its function body limit.
- Audience and presentation-goal preferences are optional; they are limited to 120 and 300 characters.
- Web research is optional: Off uses provided sources only; Light allows 1 search and up to 2 fetched pages; Standard allows 2 searches and up to 5 pages; Deep allows 3 searches and up to 8 pages. Search results alone are not cited; a page must be safely fetched and source-validated first.
- Web research requires `TAVILY_API_KEY` in `.env` or the host environment. Search uses Tavily's basic search mode; the selected depth controls the per-request maximum searches and safely fetched pages. The generation result displays up to 10 search results per query, marking fetched pages cited in the presentation as relevant and showing unfetched or unreadable results as not assessed.
- Up to 25 content slides plus one cover slide.
- In local execution only, output directory defaults to `app_data/presentations/`; deployed decks are stored in Vercel Blob.

Uploaded source text is sent to Google AI Studio. In Vercel mode, extracted source text is also temporarily stored in Neon until the job reaches a terminal state. The in-memory limiter reserves at most 14 calls per rolling minute and 200,000 estimated tokens per minute, below the stated 15 RPM / 250,000 TPM quota. Output is capped at 8,000 tokens. Token accounting is approximate and does not include other users sharing the Google project.

## Vercel Limitation

The Vercel deployment uses Neon, Vercel Queues, and a private Blob store for persistent background jobs. Hobby limits still apply, including the 300-second function maximum duration, queue-operation allowance, and Blob storage/transfer allowance. The app keeps synchronous generation and local-file storage for local development. Keep secrets in environment settings and do not commit `.env`.