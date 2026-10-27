import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import AsyncMock, patch

from fastapi.testclient import TestClient

import app


class BackgroundJobRouteTests(unittest.TestCase):
    def test_vercel_generation_persists_and_enqueues_without_blocking_for_gemini(self) -> None:
        with tempfile.TemporaryDirectory() as output:
            with (
                patch.object(app, "IS_VERCEL", True),
                patch.object(app, "DATABASE_URL", "postgresql://test"),
                patch.object(app, "API_KEY", "configured"),
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
