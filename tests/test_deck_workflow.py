"""Test generation stages using a fake Gemini model instead of network calls."""

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import deck_generator


def _outline_payload() -> dict[str, object]:
    """Return a small valid outline that can be reused by workflow tests."""
    return {
        "title": "A Staged Test Deck",
        "subtitle": "A workflow regression check",
        "slides": [
            {
                "title": f"Slide {number}",
                "key_points": [
                    {"text": f"Grounded key point {point} for slide {number}.", "sources": ["S1"]}
                    for point in range(1, 4)
                ],
            }
            for number in range(1, 6)
        ],
    }


def _slide_content_payload(slide_number: int) -> dict[str, object]:
    """Build matching content for one numbered outline slide."""
    outline = _outline_payload()
    slide = outline["slides"][slide_number - 1]
    return {
        "bullets": [
            {"text": point["text"], "sources": point["sources"]}
            for point in slide["key_points"]
        ]
    }


def _content_responses() -> list[str]:
    return [
        json.dumps(_outline_payload()),
        *[
            json.dumps(_slide_content_payload(number))
            for number in range(1, 6)
        ],
    ]


def _build_candidate(deck, path: Path, source_catalog) -> None:
    _ = deck, source_catalog
    path.write_bytes(b"candidate pptx")


class FakeModel:
    """Stand-in for Gemini that returns saved test responses in order."""

    def __init__(self, responses=None, run_callbacks=False) -> None:
        self.responses = list(responses or _content_responses())
        self.prompts = []
        self.configs = []
        self.run_callbacks = run_callbacks

    def invoke(self, prompt, config):
        self.prompts.append(prompt)
        self.configs.append(config)
        if self.run_callbacks:
            for callback in config["callbacks"]:
                callback.on_chat_model_start({}, [[prompt]])
        return type("Response", (), {"content": self.responses.pop(0)})()


class DeckWorkflowTests(unittest.TestCase):
    """Verify valid workflow, retries, source checks, and cancellation behavior."""

    def test_cancellation_stops_before_the_next_model_call(self) -> None:
        callback = deck_generator.GeminiCapacityCallback("cancel-test", lambda: True)
        with (
            patch.object(deck_generator, "reserve_gemini_capacity") as reserve,
            self.assertRaises(deck_generator.GenerationCancelled),
        ):
            callback.on_chat_model_start({}, [])

        reserve.assert_not_called()

    def test_runs_outline_content_validation_and_visual_stages(self) -> None:
        with tempfile.TemporaryDirectory() as output:
            model = FakeModel()

            def create_model(**kwargs):
                _ = kwargs
                return model

            def build(_deck, path: Path, _source_catalog) -> None:
                _ = _deck, _source_catalog
                path.write_bytes(b"candidate pptx")

            with (
                patch.object(deck_generator, "API_KEY", "configured"),
                patch.object(deck_generator, "ChatGoogleGenerativeAI", side_effect=create_model),
                patch.object(deck_generator, "OUTPUT_DIR", Path(output)),
                patch.object(deck_generator, "build_presentation", side_effect=build),
                patch.object(deck_generator, "render_presentation_previews", return_value=6) as render,
            ):
                path = deck_generator.generate_presentation(
                    request_id="workflow-test",
                    source_text="Test source material.",
                    source_urls=[],
                    source_catalog={"S1": "Test source"},
                )

            self.assertTrue(path.is_file(), f"Missing generated file {path}")
            self.assertEqual(len(model.prompts), 6)
            self.assertIn("Stage: create a presentation outline only", model.prompts[0])
            self.assertIn("Stage: write final content for one slide only", model.prompts[1])
            self.assertIn("Slide 5 title: Slide 5", model.prompts[-1])
            render.assert_called_once()

    def test_retries_outline_when_model_returns_too_few_slides(self) -> None:
        incomplete_outline = {
            "title": "A Staged Test Deck",
            "subtitle": "A workflow regression check",
            "slides": [
                {
                    "title": "Introduction",
                    "key_points": [
                        {"text": "Grounded introductory point.", "sources": ["S1"]}
                        for _ in range(3)
                    ],
                }
            ],
        }
        model = FakeModel(
            [json.dumps(incomplete_outline), *_content_responses()],
            run_callbacks=True,
        )
        model_call_counter = [0]
        with tempfile.TemporaryDirectory() as output:
            with (
                patch.object(deck_generator, "API_KEY", "configured"),
                patch.object(deck_generator, "ChatGoogleGenerativeAI", return_value=model),
                patch.object(deck_generator, "OUTPUT_DIR", Path(output)),
                patch.object(deck_generator, "build_presentation", side_effect=_build_candidate),
                patch.object(deck_generator, "render_presentation_previews", return_value=6),
                patch.object(deck_generator, "reserve_gemini_capacity", return_value=True),
            ):
                path = deck_generator.generate_presentation(
                    request_id="outline-retry-test",
                    source_text="Test source material.",
                    source_urls=[],
                    source_catalog={"S1": "Test source"},
                    model_call_counter=model_call_counter,
                )
                self.assertTrue(path.is_file(), f"Missing generated file {path}")

        self.assertEqual(len(model.prompts), 7)
        self.assertEqual(model_call_counter[0], 7)
        self.assertIn("at least 5 distinct slides", model.prompts[1])

    def test_rejects_unknown_citation_sources(self) -> None:
        deck = deck_generator.SlideDeck.model_validate(
            {
                "title": "A Staged Test Deck",
                "subtitle": "A workflow regression check",
                "slides": [
                    {
                        "title": f"Slide {number}",
                        "bullets": [
                            {
                                "text": f"Grounded key point {point} for slide {number}.",
                                "sources": ["S9" if number == 1 and point == 1 else "S1"],
                            }
                            for point in range(1, 4)
                        ],
                    }
                    for number in range(1, 6)
                ],
            }
        )
        deck.slides[0].bullets[0].sources = ["S9"]
        feedback = deck_generator._validation_feedback(deck, {"S1": "Test source"})
        self.assertIn("Unknown source IDs: S9", feedback)

    def test_retries_only_the_slide_with_invalid_citations(self) -> None:
        responses = _content_responses()
        invalid_slide = _slide_content_payload(1)
        invalid_slide["bullets"][0]["sources"] = ["S9"]
        responses.insert(1, json.dumps(invalid_slide))
        model = FakeModel(responses)
        with tempfile.TemporaryDirectory() as output:
            with (
                patch.object(deck_generator, "API_KEY", "configured"),
                patch.object(deck_generator, "ChatGoogleGenerativeAI", return_value=model),
                patch.object(deck_generator, "OUTPUT_DIR", Path(output)),
                patch.object(deck_generator, "build_presentation", side_effect=_build_candidate),
                patch.object(deck_generator, "render_presentation_previews", return_value=6),
            ):
                path = deck_generator.generate_presentation(
                    request_id="slide-retry-test",
                    source_text="Test source material.",
                    source_urls=[],
                    source_catalog={"S1": "Test source"},
                )

            self.assertTrue(path.is_file())
            self.assertEqual(len(model.prompts), 7)
            self.assertIn("approved source IDs: S1", model.prompts[2])

    def test_runs_bounded_web_research_as_its_own_stage(self) -> None:
        model = FakeModel(
            [
                json.dumps({"queries": ["test query"]}),
                *_content_responses(),
            ]
        )
        stats = {}
        results = []
        with tempfile.TemporaryDirectory() as output:
            with (
                patch.object(deck_generator, "API_KEY", "configured"),
                patch.object(deck_generator, "TAVILY_API_KEY", "configured"),
                patch.object(deck_generator, "ChatGoogleGenerativeAI", return_value=model),
                patch.object(deck_generator, "OUTPUT_DIR", Path(output)),
                patch.object(
                    deck_generator,
                    "build_presentation",
                    side_effect=_build_candidate,
                ),
                patch.object(deck_generator, "render_presentation_previews", return_value=6),
                patch.object(
                    deck_generator,
                    "tavily_search",
                    return_value=[
                        {
                            "title": "Research result",
                            "link": "https://research.example/page",
                            "snippet": "A test result.",
                        }
                    ],
                ) as search,
                patch.object(deck_generator, "fetch_public_url", return_value="Fetched evidence."),
            ):
                path = deck_generator.generate_presentation(
                    request_id="research-test",
                    source_text="Test source material.",
                    source_urls=[],
                    source_catalog={"S1": "Test source"},
                    research_depth="light",
                    search_results=results,
                    research_stats=stats,
                )

            self.assertTrue(path.is_file(), f"Missing generated file {path}")
            search.assert_called_once_with("test query", "configured", 10)
            self.assertIn("Fetched evidence.", model.prompts[1])
            self.assertEqual(
                stats,
                {
                    "searches_performed": 1,
                    "pages_attempted": 1,
                    "pages_fetched": 1,
                },
            )
            self.assertEqual(results[0]["status"], "not_relevant")


if __name__ == "__main__":
    unittest.main()
