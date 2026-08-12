"""Telegram command -> MT5 market-order bridge for fixed XAUUSD trades.

Run only on a machine with the intended MT5 terminal already logged in.  The
bot is fail-closed: token, chat, and user allowlists are required, and only
explicit ``/buy gold`` / ``/sell gold`` commands are accepted.
"""
from __future__ import annotations

from dataclasses import dataclass
import json
import os
import time
from typing import Any
from urllib.parse import urlencode
from urllib.request import Request, urlopen


@dataclass(frozen=True)
class TradeCommand:
    side: str
    symbol: str = "XAUUSD"


def parse_trade_command(text: str, *, bot_username: str | None = None) -> TradeCommand:
    parts = text.strip().split()
    if len(parts) not in (2, 3):
        raise ValueError("Use /buy gold @bot or /sell gold @bot")
    command, asset = parts[0].lower(), parts[1].lower()
    if command not in {"/buy", "/sell"} or asset not in {"gold", "xauusd", "xauusdc"}:
        raise ValueError("Only /buy gold, /buy xauusd, and /buy xauusdc are supported")
    if len(parts) == 3:
        mention = parts[2].lstrip("@").lower()
        if not mention or (bot_username and mention != bot_username.lstrip("@").lower()):
            raise ValueError("Command is addressed to another bot")
    elif bot_username:
        raise ValueError("Include the bot mention")
    symbol = "XAUUSDc" if asset == "xauusdc" else "XAUUSD"
    return TradeCommand(side=command[1:], symbol=symbol)


def volume_for_symbol(symbol: str) -> float:
    """Return the fixed lot size for the supported gold symbol variants."""
    normalized = symbol.strip().upper()
    if normalized == "XAUUSDC":
        return 0.1
    if normalized == "XAUUSD":
        return 0.03
    raise ValueError(f"Unsupported trade symbol: {symbol}")


def build_market_order_request(*, mt5: Any, symbol: str, side: str, bid: float, ask: float,
                               volume: float, distance: float) -> dict[str, Any]:
    if side not in {"buy", "sell"} or volume <= 0 or distance <= 0:
        raise ValueError("Invalid trade parameters")
    info = mt5.symbol_info(symbol)
    if info is None:
        raise ValueError(f"MT5 symbol is unavailable: {symbol}")
    digits = int(getattr(info, "digits", 2))
    price = float(ask if side == "buy" else bid)
    is_buy = side == "buy"
    return {
        "action": mt5.TRADE_ACTION_DEAL,
        "symbol": symbol,
        "volume": volume,
        "type": mt5.ORDER_TYPE_BUY if is_buy else mt5.ORDER_TYPE_SELL,
        "price": price,
        "sl": round(price - distance if is_buy else price + distance, digits),
        "tp": round(price + distance if is_buy else price - distance, digits),
        "deviation": int(os.getenv("MT5_DEVIATION_POINTS", "30")),
        "magic": int(os.getenv("MT5_MAGIC", "573510")),
        "comment": "TG_XAUUSD_CMD",
        "type_time": mt5.ORDER_TIME_GTC,
        "type_filling": getattr(mt5, "ORDER_FILLING_IOC", 1),
    }


class TelegramTradeBot:
    def __init__(self, *, mt5: Any, token: str, chat_id: str, allowed_user_ids: set[int],
                 bot_username: str | None = None, volume: float | None = None,
                 distance: float = 10.0, timeout: float = 20.0) -> None:
        self.mt5, self.token, self.chat_id = mt5, token, str(chat_id)
        self.allowed_user_ids = allowed_user_ids
        self.bot_username = bot_username
        self.volume, self.distance, self.timeout = volume, distance, timeout
        self.api = f"https://api.telegram.org/bot{token}/"

    def _telegram(self, method: str, **params: Any) -> Any:
        payload = urlencode(params).encode()
        request = Request(self.api + method, data=payload, method="POST")
        with urlopen(request, timeout=self.timeout) as response:
            result = json.loads(response.read().decode("utf-8"))
        if not result.get("ok"):
            raise RuntimeError(result.get("description", "Telegram API error"))
        return result.get("result")

    def send(self, text: str) -> None:
        self._telegram("sendMessage", chat_id=self.chat_id, text=text)

    def execute(self, command: TradeCommand) -> str:
        if not self.mt5.symbol_select(command.symbol, True):
            raise RuntimeError(f"Cannot select {command.symbol}")
        tick = self.mt5.symbol_info_tick(command.symbol)
        if tick is None:
            raise RuntimeError("MT5 tick unavailable")
        volume = self.volume if self.volume is not None else volume_for_symbol(command.symbol)
        request = build_market_order_request(mt5=self.mt5, symbol=command.symbol, side=command.side,
                                             bid=float(tick.bid), ask=float(tick.ask),
                                             volume=volume, distance=self.distance)
        result = self.mt5.order_send(request)
        done = getattr(self.mt5, "TRADE_RETCODE_DONE", 10009)
        if result is None or getattr(result, "retcode", None) != done:
            code = None if result is None else getattr(result, "retcode", "?")
            comment = "" if result is None else getattr(result, "comment", "")
            raise RuntimeError(f"MT5 rejected order: {code} {comment}".strip())
        ticket = getattr(result, "deal", None) or getattr(result, "order", "?")
        return (f"{'BUY' if command.side == 'buy' else 'SELL'} {command.symbol} đã khớp\n"
                f"Entry: {request['price']:.2f}\nSL: {request['sl']:.2f}\nTP: {request['tp']:.2f}\n"
                f"Lot: {request['volume']:.2f}\nDeal/Order ID: {ticket}")

    def handle_update(self, update: dict[str, Any]) -> None:
        message = update.get("message") or update.get("edited_message")
        if not message or str(message.get("chat", {}).get("id")) != self.chat_id:
            return
        user_id = int(message.get("from", {}).get("id", -1))
        if user_id not in self.allowed_user_ids:
            return
        text = str(message.get("text", ""))
        try:
            command = parse_trade_command(text, bot_username=self.bot_username)
            volume = self.volume if self.volume is not None else volume_for_symbol(command.symbol)
            self.send(f"Đã nhận lệnh {command.side.upper()} {command.symbol}\nLot: {volume:.2f}\nSL: {self.distance:g}\nTP: {self.distance:g}\nTrạng thái: QUEUED")
            self.send(self.execute(command))
        except ValueError as exc:
            self.send(f"Lệnh không hợp lệ: {exc}")
        except Exception as exc:
            self.send(f"Lệnh thất bại: {type(exc).__name__}: {exc}")

    def run_forever(self) -> None:
        offset = 0
        while True:
            updates = self._telegram("getUpdates", offset=offset, timeout=25, allowed_updates='["message"]') or []
            for update in updates:
                offset = max(offset, int(update["update_id"]) + 1)
                self.handle_update(update)


def main() -> None:
    token = os.environ.get("TELEGRAM_TRADE_BOT_TOKEN", "").strip()
    chat_id = os.environ.get("TELEGRAM_TRADE_CHAT_ID", "").strip()
    raw_users = os.environ.get("TELEGRAM_TRADE_ALLOWED_USER_IDS", "").strip()
    if not token or not chat_id or not raw_users:
        raise SystemExit("Set TELEGRAM_TRADE_BOT_TOKEN, TELEGRAM_TRADE_CHAT_ID, and TELEGRAM_TRADE_ALLOWED_USER_IDS")
    allowed = {int(value.strip()) for value in raw_users.split(",") if value.strip()}
    import MetaTrader5 as mt5
    if not mt5.initialize():
        raise SystemExit(f"MT5 initialize failed: {mt5.last_error()}")
    try:
        TelegramTradeBot(mt5=mt5, token=token, chat_id=chat_id, allowed_user_ids=allowed,
                         bot_username=os.environ.get("TELEGRAM_TRADE_BOT_USERNAME")).run_forever()
    finally:
        mt5.shutdown()


if __name__ == "__main__":
    main()
