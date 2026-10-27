import unittest
from unittest.mock import AsyncMock, patch

import notifications


class DeckNotificationTests(unittest.IsolatedAsyncioTestCase):
    async def test_completion_email_contains_download_link_and_gemini_call_count(self) -> None:
        response = AsyncMock()
        client = AsyncMock()
        client.__aenter__.return_value = client
        client.__aexit__.return_value = False
        client.post.return_value = response
        with (
            patch.object(notifications, "MAILJET_API_KEY", "mailjet-key"),
            patch.object(notifications, "MAILJET_API_SECRET", "mailjet-secret"),
            patch.object(notifications, "MAILJET_FROM_EMAIL", "verified@example.com"),
            patch.object(notifications, "MAILJET_FROM_NAME", "Slides"),
            patch.object(notifications, "NOTIFICATION_EMAIL", "recipient@example.com"),
            patch.object(notifications, "APP_BASE_URL", "https://slides.example.com"),
            patch.object(notifications.httpx, "AsyncClient", return_value=client),
        ):
            await notifications.send_deck_notification(
                succeeded=True,
                title="A Test Deck",
                gemini_api_calls=7,
                filename="a-test-deck.pptx",
            )

        payload = client.post.await_args.kwargs["json"]
        message = payload["Messages"][0]
        self.assertEqual(
            message["To"],
            [{"Email": "recipient@example.com"}],
        )
        self.assertIn("/api/decks/a-test-deck.pptx/download", message["TextPart"])
        self.assertIn("Gemini API calls: 7", message["TextPart"])
        response.raise_for_status.assert_called_once()

    async def test_failure_email_asks_recipient_to_retry(self) -> None:
        response = AsyncMock()
        client = AsyncMock()
        client.__aenter__.return_value = client
        client.__aexit__.return_value = False
        client.post.return_value = response
        with (
            patch.object(notifications, "MAILJET_API_KEY", "mailjet-key"),
            patch.object(notifications, "MAILJET_API_SECRET", "mailjet-secret"),
            patch.object(notifications, "MAILJET_FROM_EMAIL", "verified@example.com"),
            patch.object(notifications, "MAILJET_FROM_NAME", "Slides"),
            patch.object(notifications, "NOTIFICATION_EMAIL", "recipient@example.com"),
            patch.object(notifications.httpx, "AsyncClient", return_value=client),
        ):
            await notifications.send_deck_notification(
                succeeded=False,
                title="Your presentation",
                gemini_api_calls=3,
            )

        message = client.post.await_args.kwargs["json"]["Messages"][0]
        self.assertIn("Please try again", message["TextPart"])
        self.assertIn("Gemini API calls: 3", message["TextPart"])


if __name__ == "__main__":
    unittest.main()
