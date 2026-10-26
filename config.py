import os
from pathlib import Path

from dotenv import load_dotenv


BASE_DIR = Path(__file__).resolve().parent
load_dotenv(BASE_DIR / ".env")

DATA_DIR = Path(os.getenv("SLIDES_DATA_DIR", BASE_DIR / "app_data")).resolve()
OUTPUT_DIR = DATA_DIR / "presentations"
DATABASE_PATH = DATA_DIR / "slides.sqlite3"
MODEL_NAME = os.getenv("GEMINI_MODEL", "gemini-3.5-flash-lite")
API_KEY = os.getenv("GOOGLE_API_KEY", "")

MAX_SOURCE_CHARS = 30_000
MAX_UPLOAD_BYTES = 10 * 1024 * 1024
MAX_UPLOADS = 10
MAX_UPLOAD_TOTAL_BYTES = 15 * 1024 * 1024
MAX_URLS = 10
MAX_URL_LENGTH = 2_048
MAX_DECK_SLIDES = 25
LLM_OUTPUT_TOKENS = 8_000
LLM_OUTPUT_RESERVE = 9_000