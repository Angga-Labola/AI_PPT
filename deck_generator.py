import json
import re

from deepagents import create_deep_agent
from langchain_core.callbacks import BaseCallbackHandler
from langchain_google_genai import ChatGoogleGenerativeAI
from pydantic import BaseModel, Field

from config import (
    API_KEY,
    LLM_OUTPUT_TOKENS,
    LLM_OUTPUT_RESERVE,
    MAX_DECK_SLIDES,
    MODEL_NAME,
    OUTPUT_DIR,
)
from jobs import cancellation_requested, complete_job, reserve_gemini_capacity, update_job
from logging_config import configure_logging
from pptx_builder import build_presentation


class Slide(BaseModel):
    title: str = Field(min_length=2, max_length=100)
    bullets: list[str] = Field(min_length=3, max_length=5)


class SlideDeck(BaseModel):
    title: str = Field(min_length=2, max_length=100)
    subtitle: str = Field(default="", max_length=180)
    slides: list[Slide] = Field(min_length=5, max_length=MAX_DECK_SLIDES)


logger = configure_logging().getChild("generation")


SYSTEM_PROMPT = f"""You are a research and presentation agent. Turn the supplied material into a clear, accurate PowerPoint outline. Use only claims supported by the source; distinguish inference from fact and preserve useful source URLs in slide bullets. Return only a JSON object matching this shape: {{\"title\": str, \"subtitle\": str, \"slides\": [{{\"title\": str, \"bullets\": [str, str, str]}}]}}. Create 5 to {MAX_DECK_SLIDES} slides, with 3 to 5 concise bullets per slide. Make the story coherent, specific, and useful to present aloud. Do not include markdown fences."""


class GeminiCapacityCallback(BaseCallbackHandler):
    raise_error = True

    def __init__(self, job_id: str) -> None:
        self.job_id = job_id

    def on_chat_model_start(self, serialized, messages, **kwargs) -> None:
        estimated_tokens = sum(
            len(str(message).encode("utf-8"))
            for prompt_messages in messages
            for message in prompt_messages
        ) + LLM_OUTPUT_RESERVE
        if not reserve_gemini_capacity(self.job_id, estimated_tokens):
            raise RuntimeError("Generation was cancelled before the next Gemini request.")


def _message_text(content) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "\n".join(
            item.get("text", "") for item in content if isinstance(item, dict)
        )
    raise ValueError("The agent returned an unsupported response format.")


def _parse_deck(content) -> SlideDeck:
    text = _message_text(content).strip()
    text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text, flags=re.IGNORECASE)
    start = text.find("{")
    end = text.rfind("}")
    if start < 0 or end <= start:
        raise ValueError("The agent did not return a valid slide outline. Please retry.")
    return SlideDeck.model_validate(json.loads(text[start : end + 1]))


def run_generation(job_id: str, source_text: str, source_url: str) -> None:
    if not API_KEY:
        raise RuntimeError("GOOGLE_API_KEY is missing. Add it to the app's .env file.")
    source_text = source_text[:30_000]
    source_urls = [line.strip() for line in source_url.splitlines() if line.strip()]
    source_context = f"\nSource URLs:\n{chr(10).join(source_urls)}" if source_urls else ""
    prompt = (
        f"Create a PowerPoint deck from the following material.{source_context}\n\n"
        f"SOURCE MATERIAL\n{source_text}"
    )
    update_job(job_id, progress="Waiting for Gemini rate capacity")
    if cancellation_requested(job_id):
        return

    update_job(job_id, progress="Drafting slides with Gemini")
    logger.info("Job %s: invoking %s", job_id, MODEL_NAME)
    model = ChatGoogleGenerativeAI(
        model=MODEL_NAME,
        google_api_key=API_KEY,
        temperature=0.2,
        max_output_tokens=LLM_OUTPUT_TOKENS,
        max_retries=0,
        timeout=180,
    )
    agent = create_deep_agent(model=model, tools=[], system_prompt=SYSTEM_PROMPT)
    result = agent.invoke(
        {"messages": [{"role": "user", "content": prompt}]},
        config={
            "recursion_limit": 25,
            "callbacks": [GeminiCapacityCallback(job_id)],
        },
    )
    messages = result.get("messages", [])
    response = next(
        (message for message in reversed(messages) if getattr(message, "type", "") == "ai"),
        None,
    )
    if response is None:
        raise ValueError("The agent returned no slide outline. Please retry.")
    deck = _parse_deck(response.content)
    logger.info("Job %s: generated outline with %d slides", job_id, len(deck.slides))
    if cancellation_requested(job_id):
        return

    update_job(job_id, progress="Building the PowerPoint file")
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    safe_title = re.sub(r"[^a-zA-Z0-9]+", "-", deck.title).strip("-").lower()[:48]
    filename = f"{safe_title or 'presentation'}-{job_id[:8]}.pptx"
    output_path = OUTPUT_DIR / filename
    build_presentation(deck, output_path)
    if cancellation_requested(job_id):
        output_path.unlink(missing_ok=True)
        return
    if not complete_job(job_id, title=deck.title, output_path=str(output_path)):
        output_path.unlink(missing_ok=True)
    else:
        logger.info("Job %s: saved presentation to %s", job_id, output_path)