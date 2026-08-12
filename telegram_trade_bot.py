"""Telegram command -> MT5 market-order bridge for fixed XAUUSD trades.

Run only on a machine with the intended MT5 terminal already logged in.  The
bot is fail-closed: token, chat, and user allowlists are required, and only
explicit ``/long gold`` / ``/short gold`` commands are accepted.
"""
from __future__ import annotations

from dataclasses import dataclass
import json
import os
import socket
import time
from datetime import datetime, timedelta, timezone
from typing import Any
from urllib.error import URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen


@dataclass(frozen=True)
class TradeCommand:
    side: str
    symbol: str = "XAUUSD"


def parse_trade_command(text: str, *, bot_username: str | None = None) -> TradeCommand:
    parts = text.strip().split()
    if len(parts) not in (2, 3):
        raise ValueError("Use /long gold @bot or /short gold @bot")
    command, asset = parts[0].lower(), parts[1].lower()
    if command not in {"/long", "/short"} or asset not in {"gold", "xauusd", "xauusdc"}:
        raise ValueError("Only /long gold, /short gold, /long xauusd, and /short xauusdc are supported")
    if len(parts) == 3:
        mention = parts[2].lstrip("@").lower()
        if not mention or (bot_username and mention != bot_username.lstrip("@").lower()):
            raise ValueError("Command is addressed to another bot")
    elif bot_username:
        raise ValueError("Include the bot mention")
    # Keep ``gold`` unresolved until MT5 is queried. Different brokers expose
    # the contract as XAUUSD or XAUUSDc.
    symbol = "gold" if asset == "gold" else ("XAUUSDc" if asset == "xauusdc" else "XAUUSD")
    return TradeCommand(side=command[1:], symbol=symbol)


def format_dollar_amount(value: float, *, show_plus: bool = False) -> str:
    """Format USD with the sign before the currency symbol (for example -$455.30)."""
    amount = float(value)
    if amount < 0:
        return f"-${abs(amount):.2f}"
    return f"{'+' if show_plus else ''}${amount:.2f}"


def volume_for_symbol(symbol: str) -> float:
    """Return the fixed lot size for the supported gold symbol variants."""
    normalized = symbol.strip().upper()
    if normalized == "XAUUSDC":
        return 0.1
    if normalized == "XAUUSD":
        return 0.03
    raise ValueError(f"Unsupported trade symbol: {symbol}")


def resolve_trade_symbol(mt5: Any, symbol: str) -> str:
    """Resolve the ``gold`` alias against symbols actually available in MT5."""
    if symbol.strip().lower() != "gold":
        return symbol
    for candidate in ("XAUUSDc", "XAUUSD"):
        info = mt5.symbol_info(candidate)
        if info is not None and mt5.symbol_select(candidate, True):
            return candidate
    raise ValueError("MT5 has neither XAUUSDc nor XAUUSD available")


def guard_opposite_position(
    positions: list[Any], *, symbol: str, side: str, position_type_buy: int,
) -> None:
    """Reject only a reverse-side entry for the same resolved symbol."""
    wanted_buy = side == "long"
    for position in positions:
        if str(getattr(position, "symbol", "")) != symbol:
            continue
        position_buy = int(getattr(position, "type", -1)) == int(position_type_buy)
        if position_buy != wanted_buy:
            current = "LONG" if position_buy else "SHORT"
            raise ValueError(f"Reverse order blocked: {symbol} already has {current}")


def parse_control_command(text: str, *, bot_username: str | None = None) -> tuple[str, int | str | None]:
    """Parse non-trading commands; destructive close-all requires confirmation."""
    parts = text.strip().split()
    if not parts:
        raise ValueError("Empty command")
    raw_command = parts[0].lower()
    command, _, mention = raw_command.partition("@")
    if bot_username and mention != bot_username.lstrip("@").lower():
        raise ValueError("Command is addressed to another bot")
    if bot_username and not mention:
        raise ValueError("Include the bot mention")
    if command in {"/status", "/positions", "/enable", "/disable", "/config"} and len(parts) == 1:
        return command[1:], None
    if command == "/close" and len(parts) == 2 and parts[1].isdigit():
        return "close", int(parts[1])
    if command == "/closeall" and len(parts) == 2 and parts[1].lower() == "confirm":
        return "closeall", "confirm"
    raise ValueError("Use /status, /positions, /close <ticket>, /closeall confirm, /enable, /disable, or /config")


def parse_period(value: str | None, *, default_days: int = 1) -> int | None:
    if value is None:
        return default_days
    normalized = value.lower()
    if normalized == "all":
        return None
    if normalized.endswith("d") and normalized[:-1].isdigit():
        days = int(normalized[:-1])
    elif normalized.isdigit():
        days = int(normalized)
    else:
        raise ValueError("Period must be 1d, 7d, 30d, 90d, or all")
    if days < 1 or days > 90:
        raise ValueError("Period must be between 1 and 90 days")
    return days


def parse_analytics_command(text: str) -> tuple[str, int | None]:
    parts = text.strip().split()
    command = parts[0].lower().split("@", 1)[0] if parts else ""
    if command in {"/stats", "/equity"} and len(parts) <= 2:
        return command[1:], parse_period(parts[1] if len(parts) == 2 else None)
    if command == "/history" and len(parts) <= 2:
        if len(parts) == 1:
            return "history", 10
        if parts[1].isdigit() and 1 <= int(parts[1]) <= 50:
            return "history", int(parts[1])
    if command == "/drawdown" and len(parts) == 1:
        return "drawdown", None
    if command == "/daily" and len(parts) <= 2 and (len(parts) == 1 or parts[1].isdigit()):
        days = 1 if len(parts) == 1 else int(parts[1])
        if 1 <= days <= 30:
            return "daily", days
    raise ValueError("Use /stats [1d|7d|30d|90d|all], /history [1-50], /equity [period], /drawdown, or /daily [1-30]")


def build_market_order_request(*, mt5: Any, symbol: str, side: str, bid: float, ask: float,
                               volume: float, distance: float) -> dict[str, Any]:
    if side not in {"long", "short"} or volume <= 0 or distance <= 0:
        raise ValueError("Invalid trade parameters")
    info = mt5.symbol_info(symbol)
    if info is None:
        raise ValueError(f"MT5 symbol is unavailable: {symbol}")
    digits = int(getattr(info, "digits", 2))
    price = float(ask if side == "long" else bid)
    is_buy = side == "long"
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
        "comment": "GOLD_DoanhHD",
        "type_time": mt5.ORDER_TIME_GTC,
        "type_filling": getattr(mt5, "ORDER_FILLING_IOC", 1),
    }


class TelegramTradeBot:
    def __init__(self, *, mt5: Any, token: str, chat_id: str, allowed_user_ids: set[int],
                 bot_username: str | None = None, volume: float | None = None,
                 distance: float = 10.0, timeout: float = 40.0,
                 allowed_chat_ids: set[str] | None = None) -> None:
        self.mt5, self.token, self.chat_id = mt5, token, str(chat_id)
        self.allowed_chat_ids = {self.chat_id, *(allowed_chat_ids or set())}
        self.allowed_user_ids = allowed_user_ids
        self.bot_username = bot_username
        self.volume, self.distance, self.timeout = volume, distance, timeout
        self.api = f"https://api.telegram.org/bot{token}/"
        self.enabled = True

    def _telegram(self, method: str, **params: Any) -> Any:
        payload = urlencode(params).encode()
        request = Request(self.api + method, data=payload, method="POST")
        with urlopen(request, timeout=self.timeout) as response:
            result = json.loads(response.read().decode("utf-8"))
        if not result.get("ok"):
            raise RuntimeError(result.get("description", "Telegram API error"))
        return result.get("result")

    def send(self, text: str, *, chat_id: str | None = None) -> None:
        self._telegram("sendMessage", chat_id=chat_id or self.chat_id, text=text)

    def execute(self, command: TradeCommand) -> str:
        if not self.enabled:
            raise RuntimeError("Bot is disabled")
        symbol = resolve_trade_symbol(self.mt5, command.symbol)
        guard_opposite_position(
            self._positions(), symbol=symbol, side=command.side,
            position_type_buy=int(getattr(self.mt5, "POSITION_TYPE_BUY", 0)),
        )
        if not self.mt5.symbol_select(symbol, True):
            raise RuntimeError(f"Cannot select {symbol}")
        tick = self.mt5.symbol_info_tick(symbol)
        if tick is None:
            raise RuntimeError("MT5 tick unavailable")
        volume = self.volume if self.volume is not None else volume_for_symbol(symbol)
        request = build_market_order_request(mt5=self.mt5, symbol=symbol, side=command.side,
                                             bid=float(tick.bid), ask=float(tick.ask),
                                             volume=volume, distance=self.distance)
        result = self.mt5.order_send(request)
        done = getattr(self.mt5, "TRADE_RETCODE_DONE", 10009)
        if result is None or getattr(result, "retcode", None) != done:
            code = None if result is None else getattr(result, "retcode", "?")
            comment = "" if result is None else getattr(result, "comment", "")
            raise RuntimeError(f"MT5 rejected order: {code} {comment}".strip())
        ticket = getattr(result, "deal", None) or getattr(result, "order", "?")
        return (f"{'LONG' if command.side == 'long' else 'SHORT'} XAUUSD đã khớp\n"
                f"Entry: {request['price']:.2f}\nSL: {request['sl']:.2f}\nTP: {request['tp']:.2f}\n"
                f"Lot: {request['volume']:.2f}\nDeal/Order ID: {ticket}")

    def _positions(self) -> list[Any]:
        return list(self.mt5.positions_get() or ())

    def _today_deals(self) -> list[Any]:
        """Return deals since today's UTC midnight for status counters."""
        now = datetime.now(timezone.utc)
        start = datetime(now.year, now.month, now.day, tzinfo=timezone.utc)
        return list(self.mt5.history_deals_get(start, now) or ())

    def _format_status(self) -> str:
        account = self.mt5.account_info()
        positions = self._positions()
        if account is None:
            raise RuntimeError("MT5 account information unavailable")
        floating = sum(float(getattr(position, "profit", 0.0)) for position in positions)
        today_deals = self._today_deals()
        today_pnl = sum(
            float(getattr(deal, "profit", 0.0))
            + float(getattr(deal, "commission", 0.0))
            + float(getattr(deal, "swap", 0.0))
            for deal in today_deals
        )
        return ("📊 ACCOUNT STATUS\n"
                f"Bot: {'🟢 ENABLED' if self.enabled else '🔴 DISABLED'}\n"
                f"Account: MT5 #{getattr(account, 'login', '?')}\n"
                # f"Account: MT5 #198384858 (HFMarketsGlobal-Live16)\n"
                f"Environment: {'LIVE' if getattr(account, 'trade_mode', 0) else 'DEMO'}\n"
                f"Balance: {format_dollar_amount(float(getattr(account, 'balance', 0.0)))}\n"
                f"Equity: {format_dollar_amount(float(getattr(account, 'equity', 0.0)))}\n"
                f"Floating P/L: {format_dollar_amount(floating)}\n"
                f"SL/TP distance: {self.distance:g}\n"
                f"Open positions: {len(positions)}\n"
                f"Trades today: {len(today_deals)}\n"
                f"Today's net P/L: {format_dollar_amount(today_pnl, show_plus=True)}")

    def _format_positions(self) -> str:
        positions = self._positions()
        if not positions:
            return "📌 POSITIONS\nKhông có position đang mở."
        rows = ["📌 POSITIONS"]
        for position in positions:
            side = "LONG" if getattr(position, "type", 0) == getattr(self.mt5, "POSITION_TYPE_BUY", 0) else "SHORT"
            rows.append(f"#{getattr(position, 'ticket', '?')} {side} {getattr(position, 'symbol', '?')} "
                        f"Lot: {float(getattr(position, 'volume', 0.0)):.2f} "
                        f"P/L: {format_dollar_amount(float(getattr(position, 'profit', 0.0)))}")
        return "\n".join(rows)

    def _close_position(self, ticket: int) -> str:
        positions = [p for p in self._positions() if int(getattr(p, "ticket", -1)) == ticket]
        if not positions:
            raise ValueError(f"Position {ticket} không tồn tại")
        position = positions[0]
        symbol = str(position.symbol)
        tick = self.mt5.symbol_info_tick(symbol)
        if tick is None:
            raise RuntimeError(f"Tick unavailable: {symbol}")
        is_buy = int(getattr(position, "type", 0)) == int(getattr(self.mt5, "POSITION_TYPE_BUY", 0))
        request = {
            "action": self.mt5.TRADE_ACTION_DEAL,
            "symbol": symbol,
            "volume": float(position.volume),
            "type": self.mt5.ORDER_TYPE_SELL if is_buy else self.mt5.ORDER_TYPE_BUY,
            "position": ticket,
            "price": float(tick.bid if is_buy else tick.ask),
            "deviation": int(os.getenv("MT5_DEVIATION_POINTS", "30")),
            "magic": int(os.getenv("MT5_MAGIC", "573510")),
            "comment": "TG_CLOSE",
            "type_time": self.mt5.ORDER_TIME_GTC,
            "type_filling": getattr(self.mt5, "ORDER_FILLING_IOC", 1),
        }
        result = self.mt5.order_send(request)
        if result is None or getattr(result, "retcode", None) != getattr(self.mt5, "TRADE_RETCODE_DONE", 10009):
            raise RuntimeError(f"Close rejected: {getattr(result, 'retcode', '?')}")
        return f"✅ Đã gửi lệnh đóng position #{ticket} ({symbol})"

    def _format_config(self) -> str:
        return ("⚙️ CONFIG\n"
                "Commands: /long /short\n"
                "XAUUSD: 0.03 lot\n"
                "XAUUSDc: 0.10 lot\n"
                f"SL/TP: {self.distance:g} giá\n"
                "Close-all scope: bot magic only\n"
                f"Magic: {os.getenv('MT5_MAGIC', '573510')}")

    def _deal_history(self, days: int | None) -> list[Any]:
        end = datetime.now(timezone.utc)
        start = datetime(1970, 1, 1, tzinfo=timezone.utc) if days is None else end - timedelta(days=days)
        return list(self.mt5.history_deals_get(start, end) or ())

    def _format_analytics(self, kind: str, argument: int | None) -> str:
        if kind == "history":
            deals = self._deal_history(90)
            deals.sort(key=lambda deal: getattr(deal, "time", 0), reverse=True)
            rows = ["🧾 HISTORY"]
            for deal in deals[:int(argument or 10)]:
                rows.append(f"#{getattr(deal, 'ticket', '?')} {getattr(deal, 'symbol', '?')} "
                            f"P/L: {format_dollar_amount(float(getattr(deal, 'profit', 0.0)))} "
                            f"Volume: {float(getattr(deal, 'volume', 0.0)):.2f}")
            return "\n".join(rows) if len(rows) > 1 else "🧾 HISTORY\nKhông có deal."
        deals = self._deal_history(argument)
        pnl = sum(float(getattr(deal, "profit", 0.0)) + float(getattr(deal, "commission", 0.0)) + float(getattr(deal, "swap", 0.0)) for deal in deals)
        if kind == "stats":
            wins = sum(1 for deal in deals if float(getattr(deal, "profit", 0.0)) > 0)
            losses = sum(1 for deal in deals if float(getattr(deal, "profit", 0.0)) < 0)
            return f"📈 STATS\nPeriod: {'all' if argument is None else str(argument) + 'd'}\nDeals: {len(deals)}\nWins: {wins}\nLosses: {losses}\nNet P/L: {format_dollar_amount(pnl)}"
        if kind == "equity":
            account = self.mt5.account_info()
            return f"💹 EQUITY\nPeriod: {'all' if argument is None else str(argument) + 'd'}\nCurrent equity: {format_dollar_amount(float(getattr(account, 'equity', 0.0)))}\nRealized P/L: {format_dollar_amount(pnl)}"
        if kind == "daily":
            return f"📅 DAILY\nLast {argument} day(s)\nDeals: {len(deals)}\nNet P/L: {format_dollar_amount(pnl)}"
        account = self.mt5.account_info()
        balance = float(getattr(account, "balance", 0.0))
        peak = balance - pnl if pnl < 0 else balance
        drawdown = max(0.0, peak - balance)
        return f"📉 DRAWDOWN\nCurrent balance: {format_dollar_amount(balance)}\nEstimated drawdown: {format_dollar_amount(drawdown)}"

    def handle_update(self, update: dict[str, Any]) -> None:
        message = update.get("message") or update.get("edited_message")
        if not message or str(message.get("chat", {}).get("id")) not in self.allowed_chat_ids:
            return
        user_id = int(message.get("from", {}).get("id", -1))
        if user_id not in self.allowed_user_ids:
            return
        source_chat_id = str(message["chat"]["id"])
        parser_bot_username = None if message.get("chat", {}).get("type") == "private" else self.bot_username
        text = str(message.get("text", ""))
        try:
            command_name = text.lower().split()[0].split("@", 1)[0] if text.strip() else ""
            if command_name in {"/status", "/positions", "/close", "/closeall", "/enable", "/disable", "/config"}:
                control, argument = parse_control_command(text, bot_username=parser_bot_username)
                if control == "status":
                    self.send(self._format_status(), chat_id=source_chat_id)
                elif control == "positions":
                    self.send(self._format_positions(), chat_id=source_chat_id)
                elif control == "config":
                    self.send(self._format_config(), chat_id=source_chat_id)
                elif control == "enable":
                    self.enabled = True
                    self.send("🟢 Bot đã ENABLED", chat_id=source_chat_id)
                elif control == "disable":
                    self.enabled = False
                    self.send("🔴 Bot đã DISABLED; position đang mở không bị đóng", chat_id=source_chat_id)
                elif control == "close":
                    self.send(self._close_position(int(argument)), chat_id=source_chat_id)
                elif control == "closeall":
                    positions = [p for p in self._positions() if int(getattr(p, "magic", -1)) == int(os.getenv("MT5_MAGIC", "573510"))]
                    results = [self._close_position(int(p.ticket)) for p in positions]
                    self.send("✅ CLOSEALL\n" + ("\n".join(results) if results else "Không có position của bot."), chat_id=source_chat_id)
                return
            if command_name in {"/stats", "/history", "/equity", "/drawdown", "/daily"}:
                analytics, argument = parse_analytics_command(text)
                if parser_bot_username and message.get("chat", {}).get("type") != "private":
                    mention = text.split()[0].split("@", 1)[1] if "@" in text.split()[0] else ""
                    if mention.lower() != parser_bot_username.lstrip("@").lower():
                        raise ValueError("Command is addressed to another bot")
                self.send(self._format_analytics(analytics, argument), chat_id=source_chat_id)
                return
            command = parse_trade_command(text, bot_username=parser_bot_username)
            resolved_symbol = resolve_trade_symbol(self.mt5, command.symbol)
            resolved_command = TradeCommand(side=command.side, symbol=resolved_symbol)
            volume = self.volume if self.volume is not None else volume_for_symbol(resolved_symbol)
            self.send(f"Đã nhận lệnh {command.side.upper()} {resolved_symbol}\nLot: {volume:.2f}\nSL: {self.distance:g}\nTP: {self.distance:g}\nTrạng thái: QUEUED", chat_id=source_chat_id)
            self.send(self.execute(resolved_command), chat_id=source_chat_id)
        except ValueError as exc:
            self.send(f"Lệnh không hợp lệ: {exc}", chat_id=source_chat_id)
        except Exception as exc:
            self.send(f"Lệnh thất bại: {type(exc).__name__}: {exc}", chat_id=source_chat_id)

    def run_forever(self) -> None:
        offset = 0
        while True:
            try:
                # The HTTP timeout must exceed Telegram's long-poll timeout.
                # Otherwise urllib raises TimeoutError before getUpdates returns.
                updates = self._telegram("getUpdates", offset=offset, timeout=25, allowed_updates='["message"]') or []
            except (TimeoutError, socket.timeout, URLError):
                # A transient network timeout must not stop the trading bridge.
                time.sleep(2)
                continue
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
    raw_chats = os.environ.get("TELEGRAM_TRADE_ALLOWED_CHAT_IDS", "").strip()
    allowed_chats = {value.strip() for value in raw_chats.split(",") if value.strip()}
    import MetaTrader5 as mt5
    if not mt5.initialize():
        raise SystemExit(f"MT5 initialize failed: {mt5.last_error()}")
    try:
        TelegramTradeBot(mt5=mt5, token=token, chat_id=chat_id, allowed_user_ids=allowed,
                         bot_username=os.environ.get("TELEGRAM_TRADE_BOT_USERNAME"),
                         allowed_chat_ids=allowed_chats).run_forever()
    finally:
        mt5.shutdown()


if __name__ == "__main__":
    main()
