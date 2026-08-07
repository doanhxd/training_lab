from __future__ import annotations

from io import BytesIO
import unittest
from urllib.error import HTTPError

from training_lab.telegram_notifier import TelegramNotifier, TelegramSettings


class TelegramNotifierTests(unittest.TestCase):
    def test_http_error_surfaces_safe_telegram_api_description(self) -> None:
        def reject(request, *, timeout: float):
            raise HTTPError(
                request.full_url,
                400,
                "Bad Request",
                hdrs=None,
                fp=BytesIO(b'{"ok":false,"description":"Bad Request: message thread not found"}'),
            )

        notifier = TelegramNotifier(
            TelegramSettings(enabled=True, bot_token="token", chat_id="123"),
            opener=reject,
        )

        self.assertFalse(notifier.send("signal"))
        self.assertEqual(
            "Telegram rejected notification: HTTP 400 — Bad Request: message thread not found",
            notifier.last_status,
        )


if __name__ == "__main__":
    unittest.main()
