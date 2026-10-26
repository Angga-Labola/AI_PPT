import os
from pathlib import Path

from dotenv import load_dotenv


BASE_DIR = Path(__file__).resolve().parent
load_dotenv(BASE_DIR / ".env")

IS_VERCEL = os.getenv("VERCEL", "").lower() in {"1", "true"}
DATA_DIR = Path(os.getenv("SLIDES_DATA_DIR", "/tmp/slides-generator" if IS_VERCEL else BASE_DIR / "app_data")).resolve()
OUTPUT_DIR = DATA_DIR / "presentations"
MODEL_NAME = os.getenv("GEMINI_MODEL", "gemini-3.5-flash-lite")
API_KEY = os.getenv("GOOGLE_API_KEY", "")

MAX_SOURCE_CHARS = 30_000
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