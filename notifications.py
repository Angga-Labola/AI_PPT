import html

import httpx

from config import (
    APP_BASE_URL,
    MAILJET_API_KEY,
    MAILJET_API_SECRET,
    MAILJET_FROM_EMAIL,
    MAILJET_FROM_NAME,
    NOTIFICATION_EMAIL,
)

MAILJET_SEND_URL = "https://api.mailjet.com/v3.1/send"


async def send_deck_notification(
    *,
    succeeded: bool,
    title: str,
    gemini_api_calls: int,
    filename: str = "",
) -> None:
    if not all(
        (MAILJET_API_KEY, MAILJET_API_SECRET, MAILJET_FROM_EMAIL, NOTIFICATION_EMAIL)
    ):
        raise RuntimeError("Mailjet notification settings are incomplete.")

    safe_title = html.escape(title)
    if succeeded:
        download_url = f"{APP_BASE_URL}/api/decks/{filename}/download"
        subject = f"Your presentation is ready: {title}"
        text = (
            f'Your presentation "{title}" is ready to download.\n'
            f"Download: {download_url}\n"
            f"Gemini API calls: {gemini_api_calls}"
        )
        body = (
            f"<p>Your presentation <strong>{safe_title}</strong> is ready.</p>"
            f'<p><a href="{html.escape(download_url, quote=True)}">Download your deck</a></p>'
            f"<p>Gemini API calls: {gemini_api_calls}</p>"
        )
    else:
        subject = "Your presentation could not be generated"
        text = (
            "Your presentation could not be generated. Please try again.\n"
            f"Gemini API calls: {gemini_api_calls}"
        )
        body = (
            "<p>Your presentation could not be generated. Please try again.</p>"
            f"<p>Gemini API calls: {gemini_api_calls}</p>"
        )

    payload = {
        "Messages": [
            {
                "From": {"Email": MAILJET_FROM_EMAIL, "Name": MAILJET_FROM_NAME},
                "To": [{"Email": NOTIFICATION_EMAIL}],
                "Subject": subject,
                "TextPart": text,
                "HTMLPart": body,
            }
        ]
    }
    async with httpx.AsyncClient(
        auth=(MAILJET_API_KEY, MAILJET_API_SECRET),
        timeout=20,
    ) as client:
        response = await client.post(MAILJET_SEND_URL, json=payload)
        response.raise_for_status()
