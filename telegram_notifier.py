"""Fail-closed Telegram notification helper for trade runners.

Credentials are intentionally read only from environment variables.  This module
never raises from a notification attempt, so a Telegram outage cannot affect MT5
risk controls or an already-filled broker order.
"""
from __future__ import annotations

from dataclasses import dataclass
import json
import os
from typing import Callable
from urllib.error import URLError
from urllib.request import Request, urlopen


def _read_env(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if value or os.name != "nt":
        return value
    try:
        import winreg
        for root_key, subkey in (
            (winreg.HKEY_CURRENT_USER, "Environment"),
            (winreg.HKEY_LOCAL_MACHINE, r"SYSTEM\CurrentControlSet\Control\Session Manager\Environment"),
        ):
            try:
                with winreg.OpenKey(root_key, subkey) as key:
                    value, _ = winreg.QueryValueEx(key, name)
            except OSError:
                continue
            value = str(value).strip()
            if value:
                return value
    except Exception:
        return ""
    return ""


@dataclass(frozen=True)
class TelegramSettings:
    enabled: bool = False
    bot_token: str = ""
    chat_id: str = ""
    message_thread_id: int | None = None
    timeout_seconds: float = 5.0

    @classmethod
    def from_environment(cls, *, enabled: bool = False) -> "TelegramSettings":
        raw_thread_id = _read_env("TELEGRAM_MESSAGE_THREAD_ID")
        try:
            message_thread_id = int(raw_thread_id) if raw_thread_id else None
        except ValueError:
            message_thread_id = None
        return cls(
            enabled=enabled,
            bot_token=_read_env("TELEGRAM_BOT_TOKEN"),
            chat_id=_read_env("TELEGRAM_CHAT_ID"),
            message_thread_id=message_thread_id,
        )

    @property
    def configured(self) -> bool:
        return self.enabled and bool(self.bot_token) and bool(self.chat_id)


class TelegramNotifier:
    """Small sender for Telegram Bot API `sendMessage` requests."""

    def __init__(
        self,
        settings: TelegramSettings,
        *,
        opener: Callable[..., object] = urlopen,
    ) -> None:
        self.settings = settings
        self._opener = opener
        self.last_status = "Telegram disabled"

    def send(self, text: str) -> bool:
        if not self.settings.enabled:
            self.last_status = "Telegram disabled"
            return False
        if not self.settings.bot_token or not self.settings.chat_id:
            self.last_status = "Telegram unavailable: TELEGRAM_BOT_TOKEN or TELEGRAM_CHAT_ID is missing"
            return False
        payload_data: dict[str, object] = {
            "chat_id": self.settings.chat_id,
            "text": text,
            "disable_web_page_preview": True,
        }
        if self.settings.message_thread_id is not None:
            payload_data["message_thread_id"] = self.settings.message_thread_id
        payload = json.dumps(payload_data, ensure_ascii=False).encode("utf-8")
        request = Request(
            f"https://api.telegram.org/bot{self.settings.bot_token}/sendMessage",
            data=payload,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with self._opener(request, timeout=self.settings.timeout_seconds) as response:
                status = getattr(response, "status", 200)
                if not 200 <= int(status) < 300:
                    self.last_status = f"Telegram rejected notification: HTTP {status}"
                    return False
        except (OSError, URLError, ValueError) as exc:
            self.last_status = f"Telegram notification failed: {type(exc).__name__}"
            return False
        self.last_status = "Telegram notification sent"
        return True


def format_filled_order_message(*, symbol: str, side: str, request: dict, ticket: object, timeframe: str) -> str:
    """Build a compact immutable broker-fill alert from the submitted request."""
    direction = "LONG 🟢" if side == "long" else "SHORT 🔴"
    return "\n".join(
        (
            f"{symbol} | {timeframe} | {direction}",
            f"Entry: {float(request['price']):.2f}",
            f"SL: {float(request['sl']):.2f}",
            f"TP: {float(request['tp']):.2f}",
        )
    )


def format_signal_message(*, symbol: str, side: str, request: dict, timeframe: str) -> str:
    """Build a pre-submission alert for a signal that passed all local guards."""
    direction = "LONG 🟢" if side == "long" else "SHORT 🔴"
    return "\n".join(
        (
            f"{symbol} | {timeframe} | {direction}",
            f"Entry: {float(request['price']):.2f}",
            f"SL: {float(request['sl']):.2f}",
            f"TP: {float(request['tp']):.2f}",
        )
    )
