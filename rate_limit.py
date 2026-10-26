import threading
import time
from collections import deque

from config import (
    GEMINI_MIN_INTERVAL_SECONDS,
    GEMINI_RPM_LIMIT,
    GEMINI_TPM_BUDGET,
)
from logging_config import configure_logging


logger = configure_logging().getChild("rate_limit")
_lock = threading.Lock()
_calls: deque[tuple[float, int]] = deque()
_last_call_at = 0.0


def reserve_gemini_capacity(request_id: str, tokens: int) -> bool:
    global _last_call_at
    if tokens > GEMINI_TPM_BUDGET:
        raise ValueError("This source is too large for the current Gemini token budget.")

    while True:
        now = time.monotonic()
        sleep_seconds = 0.1
        with _lock:
            while _calls and _calls[0][0] <= now - 60:
                _calls.popleft()

            current_tokens = sum(reserved for _, reserved in _calls)
            interval_remaining = GEMINI_MIN_INTERVAL_SECONDS - (now - _last_call_at)
            if (
                len(_calls) < GEMINI_RPM_LIMIT
                and current_tokens + tokens <= GEMINI_TPM_BUDGET
                and interval_remaining <= 0
            ):
                _calls.append((now, tokens))
                _last_call_at = now
                logger.info("Request %s reserved %d estimated Gemini tokens", request_id, tokens)
                return True

            wait_for_window = max(0.1, _calls[0][0] + 60 - now) if _calls else 0.1
            sleep_seconds = min(0.5, max(interval_remaining, wait_for_window, 0.1))
        time.sleep(sleep_seconds)