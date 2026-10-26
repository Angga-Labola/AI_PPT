# Fieldnotes Slide Studio

FastAPI application that turns notes, documents, and public web pages into PowerPoint presentations using DeepAgents and Gemini.

## Run Locally

Install Python 3.11+ and [uv](https://docs.astral.sh/uv/getting-started/installation/), then run `uv sync --locked`. Set `GOOGLE_API_KEY` in `.env` (copy `.env.example` as a starting point). Start the web app and worker in separate terminals:

```sh
uv run --locked python app.py
uv run --locked python worker.py
```

Open `http://127.0.0.1:8000`. In VS Code, select **FastAPI + worker** in Run and Debug to launch both processes with breakpoints. Set `LOG_LEVEL=DEBUG` to include successful request timings and more detailed diagnostics.

## Code Structure

```text
Browser: templates/index.html + static/app.js + static/app.css
	| HTTP / JSON / multipart uploads
	v
FastAPI: app.py  <---->  SQLite state and Gemini ledger: jobs.py
							  ^
							  | shared persistent storage
Worker: worker.py ------------+
	|                       \
	v                        v
sources.py              deck_generator.py
							 |
							 v
					   pptx_builder.py
							 |
							 v
				  app_data/presentations/*.pptx
```

### Module Responsibilities

- `config.py` loads `.env` and centralizes model credentials, data-directory paths, upload/URL/slide limits, and model output/token reservation budgets. Both web and worker processes use the same settings.
- `app.py` creates the FastAPI application and initializes SQLite/storage on startup. It serves the HTML and static files, exposes `/health`, and owns the `/api` routes for job creation, status, cancellation, deck listing, download, and deletion. Job creation validates up to 10 URLs, extracts uploaded documents, and persists the request before returning. Running `python app.py` starts Uvicorn directly.
- `jobs.py` is the persistence and coordination layer. It defines the SQLite schema, transactional single-job queue, state transitions, worker recovery, deck records, and rolling Gemini RPM/TPM reservation ledger. It is shared by the web process, worker, and worker child process.
- `worker.py` polls and claims queued work, supervises a separate child process for generation, handles cancellation, and recovers interrupted jobs after restart. A filesystem lock prevents multiple worker instances from consuming the same queue.
- `sources.py` fetches each public HTTP(S) URL with redirect validation and local/private address rejection. It extracts text from web pages and TXT/Markdown, PDF, DOCX, and PPTX documents. PPTX imports read text from slide shapes, not embedded images or speaker notes.
- `deck_generator.py` defines the Pydantic slide/deck schemas, agent instructions, DeepAgents invocation, and Gemini callback. The callback checks the shared rate ledger before every model call. Agent output is validated for 5-25 content slides before it is passed to the file builder.
- `pptx_builder.py` renders the validated outline into a styled PowerPoint file, including a cover slide and one slide for every generated outline slide.
- `logging_config.py` configures the shared console logger. Web logs include startup, queued/cancelled/deleted actions, request duration at DEBUG level, and full tracebacks for unhandled HTTP errors. Worker logs include job claims and failures; source and generation logs show URL progress, model invocation, slide count, and output path. Set `LOG_LEVEL=DEBUG` to see successful request timing logs; `INFO` avoids noise from frequent browser polling.
- `templates/index.html` defines the page structure. `static/app.js` submits multipart text/files/URLs, displays progress, polls job state, and implements cancel/download/delete interactions. `static/app.css` provides the responsive layout and visual styles.
- `pyproject.toml` and `uv.lock` define the Python project and locked dependencies. `setup.bat`, `run_app.bat`, `worker.bat`, and `.vscode/launch.json` support Windows setup, local app/worker execution, and debugging.

### Request Lifecycle

1. The browser submits optional notes, up to 10 public URLs (one URL per line), and up to 10 documents in one multipart request.
2. FastAPI validates URL schemes/DNS, input counts, file sizes, and document types. It stores extracted document text and newline-separated URLs in a queued SQLite row.
3. The worker claims the row transactionally, then `sources.py` fetches all URLs and combines the inputs within the source-character budget.
4. `deck_generator.py` asks DeepAgents/Gemini for an outline. The callback reserves estimated TPM/RPM capacity before every LLM call.
5. Pydantic validates the result. `pptx_builder.py` writes the `.pptx` under the configured presentation directory, and the job is marked completed.
6. The browser observes status through polling. The download route streams the file; the delete route removes the file and completed-job record.

## Inputs, Limits, And Storage

- Up to 10 public HTTP(S) URLs, one per line and 2,048 characters each. Local/private network hosts are rejected.
- Up to 10 documents per request: PPTX, PDF, DOCX, TXT, or Markdown. Each file is limited to 10 MB; combined upload size is limited to 15 MB.
- Up to 25 content slides per deck, plus one cover slide.
- By default, jobs and presentations are stored in `app_data/`. Set `SLIDES_DATA_DIR` to a persistent directory on a server and share it between web and worker processes.

Uploaded/extracted source text is sent to Google AI Studio. The worker limits this app to 14 model requests per rolling minute and 200,000 estimated tokens per rolling minute, leaving margin below 15 RPM / 250,000 TPM. Output is capped at 8,000 tokens. Token counts are estimates, not Google's tokenizer; other clients sharing the same Google project also consume quota. Set `GEMINI_MODEL` if the default model ID is unavailable to your Google project.

## Server Deployment

Run the web app and worker as two long-lived processes from the same release. Set `HOST=0.0.0.0` and the host-provided `PORT` for the web app. Configure both processes with the same `GOOGLE_API_KEY`, `GEMINI_MODEL`, and `SLIDES_DATA_DIR`. Mount the same persistent writable volume at `SLIDES_DATA_DIR`, enable process restarts, and use `/health` for the web health check. There is no built-in authentication; apply access controls and HTTPS at the hosting provider or reverse proxy before exposing the app publicly. Keep secrets in the provider's environment settings and never commit `.env`.