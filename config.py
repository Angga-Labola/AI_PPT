"""Read app settings from environment variables and the local .env files."""

import os
from pathlib import Path

from dotenv import load_dotenv


BASE_DIR = Path(__file__).resolve().parent
load_dotenv(BASE_DIR / ".env")
load_dotenv(BASE_DIR / ".env.local", override=False)

# "auto" uses Vercel's own environment marker to choose the deployed workflow.
GENERATION_MODE = os.getenv("GENERATION_MODE", "auto").strip().lower()
if GENERATION_MODE not in {"auto", "local"}:
    raise ValueError("GENERATION_MODE must be 'auto' or 'local'.")
VERCEL_ENVIRONMENT = os.getenv("VERCEL", "").lower() in {"1", "true"}
# Do not let a local-only setting accidentally disable persistence on the live site.
if VERCEL_ENVIRONMENT and GENERATION_MODE == "local":
    raise RuntimeError("GENERATION_MODE=local cannot be used in a Vercel deployment.")
IS_VERCEL = VERCEL_ENVIRONMENT
DATA_DIR = Path(os.getenv("SLIDES_DATA_DIR", "/tmp/slides-generator" if IS_VERCEL else BASE_DIR / "app_data")).resolve()
OUTPUT_DIR = DATA_DIR / "presentations"
# These names are kept short because they are read in many modules.
MODEL_NAME = os.getenv("GEMINI_MODEL", "gemini-3.5-flash-lite")
API_KEY = os.getenv("GOOGLE_API_KEY", "")
TAVILY_API_KEY = os.getenv("TAVILY_API_KEY", "")
DATABASE_URL = os.getenv("DATABASE_URL", "")
BLOB_STORE_ID = os.getenv("BLOB_STORE_ID", "")
BLOB_READ_WRITE_TOKEN = os.getenv("BLOB_READ_WRITE_TOKEN", "")
MAILJET_API_KEY = os.getenv("MAILJET_API_KEY", "")
MAILJET_API_SECRET = os.getenv("MAILJET_API_SECRET", "")
MAILJET_FROM_EMAIL = os.getenv("MAILJET_FROM_EMAIL", "")
MAILJET_FROM_NAME = os.getenv("MAILJET_FROM_NAME", "AI PowerPoint Generator")
NOTIFICATION_EMAIL = os.getenv("NOTIFICATION_EMAIL", "")
CRON_SECRET = os.getenv("CRON_SECRET", "")
APP_BASE_URL = os.getenv(
    "APP_BASE_URL",
    os.getenv(
        "VERCEL_PROJECT_PRODUCTION_URL",
        os.getenv("VERCEL_URL", f"http://localhost:{os.getenv('PORT', '8000')}"),
    ),
).rstrip("/")
if APP_BASE_URL and "://" not in APP_BASE_URL:
    APP_BASE_URL = f"https://{APP_BASE_URL}"

MAX_SOURCE_CHARS = 100_000
# Vercel has a tighter request-size limit than a computer running locally.
MAX_UPLOAD_BYTES = 4 * 1024 * 1024 if IS_VERCEL else 10 * 1024 * 1024
MAX_UPLOADS = 10
MAX_UPLOAD_TOTAL_BYTES = 4 * 1024 * 1024 if IS_VERCEL else 15 * 1024 * 1024
MAX_UPLOAD_COUNT = MAX_UPLOADS
MAX_URLS = 10
MAX_URL_LENGTH = 2_048
MAX_DECK_SLIDES = 25
LLM_OUTPUT_TOKENS = 8_000
LLM_OUTPUT_RESERVE = 9_000
GEMINI_RPM_LIMIT = 14
GEMINI_TPM_BUDGET = 200_000
GEMINI_MIN_INTERVAL_SECONDS = 4.3

WEB_RESEARCH_LIMITS = {
    "off": (0, 0),
    "light": (1, 2),
    "standard": (2, 5),
    "deep": (3, 8),
}
MAX_WEB_PAGE_CHARS = 3_000