# Fieldnotes Slide Studio

This app turns your notes, documents, and web links into a PowerPoint. It uses Google's Gemini to write the slides, optionally uses Tavily to search the web, and can send completion or failure emails through Mailjet.

This guide is written for someone learning to program. The comments inside the code explain important decisions; this file explains how the pieces connect and how to investigate problems yourself.

## Start the app on your computer

1. Install Python 3.12 or newer.
2. Copy `.env.example` to `.env` and fill in your own secret values. Never share or commit `.env`.
3. In `.env`, set `GENERATION_MODE=local` (or leave it as `auto` when running outside Vercel).
4. Open a terminal in this folder and run `python app.py`.
5. Open `http://localhost:8000` in your browser.

Local mode waits while a presentation is generated, saves it in `app_data/presentations/`, starts a browser download when it finishes, and sends a Mailjet email to `NOTIFICATION_EMAIL`. A failed generation sends a failure email. If Mailjet itself is unavailable, the error is logged and the generated file is still returned.

For web research, also set `TAVILY_API_KEY` and choose Light, Standard, or Deep in the form. Off uses only the material you supplied.

## Choose local or Vercel mode

`GENERATION_MODE` accepts:

- `auto` (default): use Vercel's background-job path when Vercel sets its `VERCEL` environment marker; otherwise use local synchronous generation.
- `local`: explicitly use local synchronous generation. The app refuses to start this way if it detects the Vercel environment.

Changing `.env` only changes your computer. It does not update Vercel's environment settings.

## How one presentation is made

1. The browser sends the form and files to `POST /api/generate`.
2. `app.py` checks the input, extracts text from documents, and reads any directly provided web pages.
3. `deck_generator.py` runs the writing steps: plan optional research, search and read pages, create an outline, write each slide, validate the content, and check the rendered layout.
4. `pptx_builder.py` writes the PowerPoint file.
5. Locally, the request waits for the file and returns it for immediate download. On Vercel, the API saves a job in Neon, sends its ID to Vercel Queues, and returns quickly. The queue worker later generates the deck and saves it in private Vercel Blob storage.
6. Mailjet sends a completion or final-failure email. The email reports Gemini model requests; Tavily search requests are counted separately.

## Simple glossary

| Term | Simple meaning in this app |
|---|---|
| API | A way for two programs to ask each other to do something. The browser calls the app's API routes. |
| API route / endpoint | A URL in `app.py` that performs one action, such as `/api/generate` or `/api/decks`. |
| Environment variable | A setting read when Python starts. Secrets are stored here instead of written into code. |
| `.env` | A private text file containing settings for your computer. `.env.example` is a safe template without real secrets. |
| Synchronous | The caller waits for the work to finish. Local generation is synchronous, so the browser receives the finished file in the same request. |
| Background job | Work that continues after the web request returns. Vercel uses this so the user can close the browser while generation continues. |
| Queue | A durable inbox for job IDs. Vercel Queues passes a queued job to `queue_worker.py`. |
| Worker | A program that takes a job from a queue and performs the slow work. |
| Database / Neon | Stores job status and small records that tell the app where a deck is. Temporary source text is cleared when a job ends. |
| Blob storage | File storage for completed PowerPoints. On Vercel it is separate from Neon and configured as private. |
| Download index | The small `deck_downloads` table. It maps a deck filename to its Blob file, so downloads still work after an old job record is cleaned up. |
| Terminal job | A job that cannot continue: completed, failed, or cancelled. Queued and running jobs are not terminal. |
| Cron job | A scheduled request. Vercel runs the cleanup route once a day. |
| `CRON_SECRET` | A secret Vercel sends with the scheduled cleanup request so strangers cannot trigger it. |
| HTTP status | A number describing a request result. For example, 200 means success, 400 usually means invalid input, 404 means not found, and 500 means a server problem. |
| JSON | A common text format used to send structured information such as a job ID and status. |
| Pydantic model | A Python rule-set for what data should look like. It checks the AI's outline and slide content before saving them. |
| LangGraph | A library that runs the deck-writing stages in order and passes data between them. |
| Callback | A function the model library runs at a particular moment. Here it checks cancellation, enforces rate limits, and counts Gemini calls before a request. |
| Rate limit | A safety cap on how often the app calls Gemini, to avoid exceeding the configured quota. |
| Retry | Trying an operation again after it fails. The outline gets one correction attempt; Vercel queue jobs have a bounded retry. |
| Visual QA | Short for visual quality check. The app creates temporary slide previews and checks whether text or shapes go outside their boxes. |
| Traceback | The technical error report in the terminal or Vercel logs. Read from the last line upward for the error, then use the earlier file and line names to find its cause. |

## Find the code for a feature

| File | What it does |
|---|---|
| `config.py` | Loads `.env`, chooses local or Vercel behavior, and sets limits and paths. |
| `app.py` | Starts FastAPI and contains the web routes: generate, list, download, delete, job status, cancel, and scheduled cleanup. |
| `sources.py` | Safely downloads public web pages and extracts text from supported documents. |
| `web_search.py` | Sends search requests to Tavily and cleans up the returned search results. |
| `deck_generator.py` | Runs the staged Gemini writing and validation workflow. Start here to understand how slide content is produced. |
| `rate_limit.py` | Waits when too many Gemini requests would happen too close together. |
| `visual_qa.py` | Estimates text fit and creates temporary image previews to find layout overflow. |
| `pptx_builder.py` | Turns checked title, bullets, and citations into PowerPoint shapes. |
| `deck_queue.py` | Defines the small message Vercel Queues passes between the API and worker. |
| `queue_worker.py` | Claims Vercel jobs, calls the generator, uploads the deck, and sends completion/failure emails. |
| `job_store.py` | Creates and updates Neon tables for jobs and the long-lived download index. |
| `notifications.py` | Builds and sends Mailjet email messages. |
| `logging_config.py` | Sets up readable terminal/server log output. |
| `templates/index.html` | Defines the page elements and IDs used by the browser code. |
| `static/app.js` | Submits the form, polls background-job status, triggers downloads, and updates the page. |
| `static/app.css` | Controls the page colors, layout, and mobile styling. |
| `tests/` | Small checks that run without calling real Gemini, Tavily, Mailjet, Neon, or Blob services. |
| `pyproject.toml` and `uv.lock` | List Python packages and deployment settings. |
| `vercel.json` | Sets the Vercel function limit and daily cleanup schedule. |

## Debug a problem yourself

1. **Make a small reproduction.** Use a short note and leave optional web research off unless the issue is about Tavily.
2. **Read the exact error.** The browser message may be simplified. The terminal prints the traceback, and Vercel's Function logs show deployment errors. Find the first error message and the last application filename/line in the traceback.
3. **Follow the request flow.** A browser/form issue starts in `static/app.js` or `templates/index.html`; a rejected request starts in `app.py`; an unreadable link starts in `sources.py`; unexpected slide content starts in `deck_generator.py`; layout issues start in `visual_qa.py` or `pptx_builder.py`; Vercel job-state issues start in `queue_worker.py` or `job_store.py`; email issues start in `notifications.py`.
4. **Use log levels.** Set `LOG_LEVEL=DEBUG` in `.env` for more local request details, then restart `python app.py`. Do not add log statements that print API keys, email secrets, full connection strings, or private source contents.
5. **Add a test for a fix.** Put a small test in the matching file under `tests/`. Tests use fake responses so they do not send real emails or spend API credits.
6. **Run checks.** From the project folder run `.\.venv\Scripts\python.exe -m unittest discover -s tests`, `.\.venv\Scripts\python.exe -m compileall -q app.py config.py deck_generator.py deck_queue.py job_store.py logging_config.py notifications.py pptx_builder.py queue_worker.py rate_limit.py sources.py visual_qa.py web_search.py tests`, `node --check static\app.js`, and `git diff --check`.
7. **Change one thing at a time.** If the error changes, read the new error rather than assuming it is the old problem.

Common messages:

- `GOOGLE_API_KEY is not configured`: add the Google key to the environment where the server runs, then restart/redeploy.
- `Web research is not configured`: add `TAVILY_API_KEY` or choose Off.
- `Vercel Blob is not configured`: configure `BLOB_STORE_ID` or `BLOB_READ_WRITE_TOKEN` in Vercel.
- `Cannot use private access on a public store`: connect a private Blob store; do not make decks public just to hide the error.
- `CRON_SECRET is not configured`: scheduled cleanup needs this value in Vercel Production variables.
- `slides List should have at least 5 items`: Gemini did not provide a valid outline. The generator asks once for a correction; if that also fails, inspect the model/log output and retry with clearer or more complete source material.
- A `502` means the request reached the app but a later operation failed. Read the server traceback to find which operation.

## Local settings

Common `.env` variable names:

| Variable | Needed for | Meaning |
|---|---|---|
| `GOOGLE_API_KEY` | Every generation | Google AI Studio key for Gemini. |
| `GENERATION_MODE` | Optional | `auto` or `local`; see the section above. |
| `GEMINI_MODEL` | Optional | Model name; defaults to `gemini-3.5-flash-lite`. |
| `TAVILY_API_KEY` | Optional web research | Tavily web-search key. |
| `MAILJET_API_KEY`, `MAILJET_API_SECRET` | Email | Mailjet credentials; do not expose the secret. |
| `MAILJET_FROM_EMAIL` | Email | A sender address verified in Mailjet. |
| `NOTIFICATION_EMAIL` | Email | Fixed recipient of generation messages. |
| `APP_BASE_URL` | Optional email links | Base address used to build the email download link. Local default is `http://localhost:8000`. |
| `DATABASE_URL` | Vercel jobs | Neon connection string; not required for local synchronous mode. |
| `BLOB_STORE_ID` or `BLOB_READ_WRITE_TOKEN` | Vercel deck storage | Tells Vercel how to access the configured Blob store. |
| `CRON_SECRET` | Vercel cleanup | Random secret used to protect the cleanup route. |
| `LOG_LEVEL` | Optional | `INFO` by default; set to `DEBUG` for more detail. |

## Vercel deployment

Deploy using Python 3.12 or newer. In Vercel Project Settings, configure `GOOGLE_API_KEY`, `DATABASE_URL`, Blob settings, Mailjet settings, and a strong `CRON_SECRET`. Configure `TAVILY_API_KEY` if web research is offered. Environment variables added or changed in Vercel require a redeployment before Functions see the new values.

Enable Vercel Queues and connect a **private** Blob store. The app stores source text temporarily in Neon while work is pending or running; it clears that payload when the job ends. A daily Cron removes completed, failed, and cancelled job records after seven days. It does not delete the completed deck: the smaller `deck_downloads` lookup and the Blob file remain until the user deletes the deck in the app.

Vercel's Hobby plan has limits, including a 300-second Function maximum duration. Queue delivery can happen more than once, so the worker claims jobs safely and retries a limited number of times. Cancellation is cooperative: it stops at the next check, not in the middle of a Gemini request. These are bounded background jobs, not an always-running unlimited worker.

## Input and generation limits

- Up to 10 public web URLs, each at most 2,048 characters. Private/local network addresses are rejected.
- Up to 10 files: PPTX, PDF, DOCX, TXT, or Markdown. Local per-file limit is 10 MB and combined file limit is 15 MB. Vercel request upload limit is 4 MB.
- Combined notes, extracted documents, and direct URL text: 100,000 characters maximum.
- Audience: 120 characters. Goal: 300 characters.
- Up to 25 content slides plus a cover slide.
- Research depth: Off (provided material only), Light (1 search / up to 2 pages), Standard (2 / up to 5), or Deep (3 / up to 8).
- Gemini rate gate: at most 14 calls per rolling minute and 200,000 estimated tokens per minute for this app process. The estimate is approximate; other apps sharing the Google project are not counted.
- Source text is sent to Google AI Studio for generation. In Vercel mode it is also temporarily stored in Neon while the job runs.
