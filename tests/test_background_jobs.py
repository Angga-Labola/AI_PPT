import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import AsyncMock, patch

from fastapi.testclient import TestClient

import app


class BackgroundJobRouteTests(unittest.TestCase):
    def test_local_generation_emails_and_returns_download_metadata(self) -> None:
        with tempfile.TemporaryDirectory() as output:
            output_path = Path(output) / "test-deck-12345678.pptx"

            def generate(*args):
                args[-1][0] = 4
                output_path.write_bytes(b"presentation")
                return output_path

            with (
                patch.object(app, "IS_VERCEL", False),
                patch.object(app, "API_KEY", "configured"),
                patch.object(app, "MAILJET_API_KEY", "mailjet-key"),
                patch.object(app, "MAILJET_API_SECRET", "mailjet-secret"),
                patch.object(app, "MAILJET_FROM_EMAIL", "verified@example.com"),
                patch.object(app, "NOTIFICATION_EMAIL", "recipient@example.com"),
                patch.object(app, "OUTPUT_DIR", Path(output)),
                patch.object(
                    app,
                    "collect_source_text",
                    return_value=("Prepared source text", set(), []),
                ),
                patch.object(app, "generate_presentation", side_effect=generate),
                patch.object(app, "send_deck_notification", new_callable=AsyncMock) as notify,
                TestClient(app.app) as client,
            ):
                response = client.post(
                    "/api/generate?include_metadata=true",
                    data={"text": "A presentation topic", "research_depth": "off"},
                )
                download = client.get(
                    f"/api/decks/{output_path.name}/download"
                )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["filename"], output_path.name)
        self.assertEqual(response.json()["gemini_api_calls"], 4)
        self.assertEqual(download.status_code, 200)
        self.assertEqual(download.content, b"presentation")
        notify.assert_awaited_once_with(
            succeeded=True,
            title="Test Deck",
            gemini_api_calls=4,
            filename=output_path.name,
        )

    def test_local_generation_failure_sends_retry_email(self) -> None:
        with (
            patch.object(app, "IS_VERCEL", False),
            patch.object(app, "API_KEY", "configured"),
            patch.object(app, "MAILJET_API_KEY", "mailjet-key"),
            patch.object(app, "MAILJET_API_SECRET", "mailjet-secret"),
            patch.object(app, "MAILJET_FROM_EMAIL", "verified@example.com"),
            patch.object(app, "NOTIFICATION_EMAIL", "recipient@example.com"),
            patch.object(
                app,
                "collect_source_text",
                return_value=("Prepared source text", set(), []),
            ),
            patch.object(
                app,
                "generate_presentation",
                side_effect=ValueError("Invalid generated outline."),
            ),
            patch.object(app, "send_deck_notification", new_callable=AsyncMock) as notify,
            TestClient(app.app) as client,
        ):
            response = client.post(
                "/api/generate",
                data={"text": "A presentation topic", "research_depth": "off"},
            )

        self.assertEqual(response.status_code, 400)
        notify.assert_awaited_once_with(
            succeeded=False,
            title="Your presentation",
            gemini_api_calls=0,
        )

    def test_vercel_generation_persists_and_enqueues_without_blocking_for_gemini(self) -> None:
        with tempfile.TemporaryDirectory() as output:
            with (
                patch.object(app, "IS_VERCEL", True),
                patch.object(app, "DATABASE_URL", "postgresql://test"),
                patch.object(app, "API_KEY", "configured"),
                patch.object(app, "BLOB_STORE_ID", "blob-store-id"),
                patch.object(app, "BLOB_READ_WRITE_TOKEN", ""),
                patch.object(app, "MAILJET_API_KEY", "mailjet-key"),
                patch.object(app, "MAILJET_API_SECRET", "mailjet-secret"),
                patch.object(app, "MAILJET_FROM_EMAIL", "verified@example.com"),
                patch.object(app, "NOTIFICATION_EMAIL", "recipient@example.com"),
                patch.object(app, "OUTPUT_DIR", Path(output)),
                patch.object(app.job_store, "initialize_job_store"),
                patch.object(app.job_store, "create_job") as create_job,
                patch.object(
                    app,
                    "collect_source_text",
                    return_value=("Prepared source text", set(), []),
                ),
                patch.object(app, "send", new_callable=AsyncMock) as send,
                patch.object(app, "generate_presentation") as generate,
            ):
                with TestClient(app.app) as client:
                    response = client.post(
                        "/api/generate",
                        data={"text": "A presentation topic", "research_depth": "off"},
                    )

            self.assertEqual(response.status_code, 202)
            job = response.json()
            self.assertEqual(job["status"], "queued")
            create_job.assert_called_once()
            send.assert_awaited_once()
            generate.assert_not_called()

    def test_vercel_generation_rejects_missing_blob_configuration(self) -> None:
        with (
            patch.object(app, "IS_VERCEL", True),
            patch.object(app, "API_KEY", "configured"),
            patch.object(app, "BLOB_STORE_ID", ""),
            patch.object(app, "BLOB_READ_WRITE_TOKEN", ""),
            patch.object(app, "collect_source_text") as collect_sources,
            patch.object(app, "send", new_callable=AsyncMock) as send,
            TestClient(app.app) as client,
        ):
            response = client.post(
                "/api/generate",
                data={"text": "A presentation topic", "research_depth": "off"},
            )

        self.assertEqual(response.status_code, 503)
        self.assertIn("Vercel Blob is not configured", response.json()["detail"])
        collect_sources.assert_not_called()
        send.assert_not_awaited()

    def test_vercel_generation_rejects_missing_mailjet_configuration(self) -> None:
        with (
            patch.object(app, "IS_VERCEL", True),
            patch.object(app, "API_KEY", "configured"),
            patch.object(app, "BLOB_STORE_ID", "blob-store-id"),
            patch.object(app, "BLOB_READ_WRITE_TOKEN", ""),
            patch.object(app, "MAILJET_API_KEY", ""),
            patch.object(app, "MAILJET_API_SECRET", ""),
            patch.object(app, "MAILJET_FROM_EMAIL", ""),
            patch.object(app, "NOTIFICATION_EMAIL", ""),
            patch.object(app.job_store, "initialize_job_store"),
            patch.object(app, "collect_source_text") as collect_sources,
            patch.object(app, "send", new_callable=AsyncMock) as send,
            TestClient(app.app) as client,
        ):
            response = client.post(
                "/api/generate",
                data={"text": "A presentation topic", "research_depth": "off"},
            )

        self.assertEqual(response.status_code, 503)
        self.assertIn("Email notifications are not configured", response.json()["detail"])
        collect_sources.assert_not_called()
        send.assert_not_awaited()

    def test_job_status_route_returns_durable_job_state(self) -> None:
        now = datetime.now(timezone.utc)
        job = {
            "id": "job-123",
            "status": "running",
            "created_at": now,
            "updated_at": now,
            "error": None,
            "filename": None,
            "metadata": None,
        }
        with (
            patch.object(app, "IS_VERCEL", True),
            patch.object(app.job_store, "initialize_job_store"),
            patch.object(app.job_store, "get_job", return_value=job),
            TestClient(app.app) as client,
        ):
            response = client.get("/api/jobs/job-123")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["status"], "running")

    def test_cancel_route_requests_cooperative_cancellation(self) -> None:
        with (
            patch.object(app, "IS_VERCEL", True),
            patch.object(app.job_store, "initialize_job_store"),
            patch.object(
                app.job_store,
                "request_job_cancellation",
                return_value={"id": "job-123", "status": "cancel_requested"},
            ) as cancel,
            TestClient(app.app) as client,
        ):
            response = client.post("/api/jobs/job-123/cancel")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["status"], "cancel_requested")
        cancel.assert_called_once_with("job-123")


if __name__ == "__main__":
    unittest.main()
