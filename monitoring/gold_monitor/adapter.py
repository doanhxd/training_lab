from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta, timezone
from typing import Any, Iterable

GMT_PLUS_7 = timezone(timedelta(hours=7))


@dataclass(frozen=True)
class PositionView:
    ticket: int
    opened_at: datetime | None
    symbol: str
    side: str
    volume: float
    price_open: float
    stop_loss: float
    take_profit: float
    profit: float
    source: str


@dataclass(frozen=True)
class AccountSnapshot:
    login: int
    server: str
    balance: float
    equity: float
    currency: str
    positions: tuple[PositionView, ...]
    log_entries: tuple[str, ...]


@dataclass(frozen=True)
class HistoryDealView:
    time: datetime
    symbol: str
    side: str
    volume: float
    price: float
    profit: float
    commission: float
    swap: float
    net_profit: float
    comment: str


@dataclass(frozen=True)
class HistoryStats:
    deals: int
    wins: int
    losses: int
    net_profit: float
    winrate: float
    max_daily_drawdown: float
    raw_deals: int = 0


@dataclass(frozen=True)
class EquityEvent:
    time: datetime
    amount: float
    kind: str


class GoldPositionMonitor:
    """Read-only observer for the MT5 terminal already logged in by the user.

    This adapter never calls ``mt5.login``, submits/modifies orders, or imports
    strategy/runner code. A terminal is initialized only to read its own snapshot
    or deal history and is shut down by ``stop``.
    """

    def __init__(self, *, mt5: Any, symbols: Iterable[str] = ("XAUUSD", "BTCUSD")) -> None:
        self.mt5 = mt5
        self.symbols = self._normalize_symbols(symbols)
        self._symbol_prefixes = self._normalize_symbol_prefixes(self.symbols)
        self._connected = False
        self._previous_positions: dict[int, PositionView] = {}

    @staticmethod
    def _normalize_symbols(symbols: Iterable[str]) -> tuple[str, ...]:
        seen: set[str] = set()
        result: list[str] = []
        for symbol in symbols:
            text = str(symbol or "").strip()
            if text and text.casefold() not in seen:
                seen.add(text.casefold())
                result.append(text)
        return tuple(result)

    @staticmethod
    def _normalize_symbol_prefixes(symbols: Iterable[str]) -> tuple[str, ...]:
        prefixes: list[str] = []
        seen: set[str] = set()
        for symbol in symbols:
            value = str(symbol or "").strip().upper()
            if value.startswith("XAUUSD"):
                value = "XAUUSD"
            elif value.startswith("BTCUSD"):
                value = "BTCUSD"
            if value and value.casefold() not in seen:
                seen.add(value.casefold())
                prefixes.append(value)
        return tuple(prefixes)

    def _position_matches_observed_symbol(self, position: Any) -> bool:
        symbol = str(getattr(position, "symbol", "") or "").strip().upper()
        return bool(symbol) and any(symbol.startswith(prefix) for prefix in self._symbol_prefixes)

    def _broker_symbol_variants(self) -> tuple[str, ...]:
        symbols_get = getattr(self.mt5, "symbols_get", None)
        if not callable(symbols_get):
            return self.symbols
        try:
            broker_symbols = symbols_get() or ()
        except Exception:
            return self.symbols
        variants = list(self.symbols)
        seen = {symbol.casefold() for symbol in variants}
        for item in broker_symbols:
            symbol = str(getattr(item, "name", item) or "").strip()
            if symbol and self._position_matches_observed_symbol(type("Symbol", (), {"symbol": symbol})()) and symbol.casefold() not in seen:
                variants.append(symbol)
                seen.add(symbol.casefold())
        return tuple(variants)

    def _read_positions(self) -> tuple[Any, ...]:
        try:
            all_positions = self.mt5.positions_get() or ()
        except TypeError:
            all_positions = None
        if all_positions is not None:
            return tuple(position for position in all_positions if self._position_matches_observed_symbol(position))
        result: list[Any] = []
        seen: set[int] = set()
        for symbol in self._broker_symbol_variants():
            for position in self.mt5.positions_get(symbol=symbol) or ():
                ticket = int(getattr(position, "ticket", 0) or 0)
                if ticket not in seen:
                    seen.add(ticket)
                    result.append(position)
        return tuple(result)

    def _ensure_connected(self) -> None:
        if self._connected:
            return
        if not self.mt5.initialize():
            raise RuntimeError(f"Không thể kết nối MT5: {self.mt5.last_error()}")
        self._connected = True

    def _broker_utc_offset_hours(self) -> int:
        tick_reader = getattr(self.mt5, "symbol_info_tick", None)
        if not callable(tick_reader):
            return 0
        now_epoch = int(datetime.now(UTC).timestamp())
        freshest = 0
        for symbol in self.symbols:
            try:
                tick = tick_reader(symbol)
            except Exception:
                continue
            freshest = max(freshest, int(getattr(tick, "time", 0) or 0))
        offset = round((freshest - now_epoch) / 3600) if freshest else 0
        return offset if -14 <= offset <= 14 else 0

    def _timestamp_gmt7(self, timestamp: int) -> datetime | None:
        if timestamp <= 0:
            return None
        offset = self._broker_utc_offset_hours()
        value = datetime.fromtimestamp(timestamp, tz=UTC) + timedelta(hours=7 - offset)
        return value.replace(tzinfo=GMT_PLUS_7)

    def _position_view(self, position: Any) -> PositionView:
        side = "BUY" if int(getattr(position, "type", -1)) == int(getattr(self.mt5, "POSITION_TYPE_BUY", 0)) else "SELL"
        comment = str(getattr(position, "comment", "") or "").strip()
        magic = int(getattr(position, "magic", 0) or 0)
        source = comment or (f"MAGIC {magic}" if magic else "MANUAL")
        return PositionView(
            ticket=int(getattr(position, "ticket", 0) or 0),
            opened_at=self._timestamp_gmt7(int(getattr(position, "time", 0) or 0)),
            symbol=str(getattr(position, "symbol", "") or ""),
            side=side,
            volume=float(getattr(position, "volume", 0.0) or 0.0),
            price_open=float(getattr(position, "price_open", 0.0) or 0.0),
            stop_loss=float(getattr(position, "sl", 0.0) or 0.0),
            take_profit=float(getattr(position, "tp", 0.0) or 0.0),
            profit=float(getattr(position, "profit", 0.0) or 0.0),
            source=source,
        )

    def _history_deal_view(self, deal: Any, opening_comments: dict[int, str]) -> HistoryDealView | None:
        deal_type = int(getattr(deal, "type", -1))
        buy_type = int(getattr(self.mt5, "DEAL_TYPE_BUY", 0))
        sell_type = int(getattr(self.mt5, "DEAL_TYPE_SELL", 1))
        closing_entries = {int(getattr(self.mt5, "DEAL_ENTRY_OUT", 1)), int(getattr(self.mt5, "DEAL_ENTRY_INOUT", 2))}
        if deal_type not in {buy_type, sell_type} or int(getattr(deal, "entry", -1)) not in closing_entries:
            return None
        profit = float(getattr(deal, "profit", 0.0) or 0.0)
        commission = float(getattr(deal, "commission", 0.0) or 0.0)
        swap = float(getattr(deal, "swap", 0.0) or 0.0)
        fee = float(getattr(deal, "fee", 0.0) or 0.0)
        position_id = int(getattr(deal, "position_id", 0) or 0)
        timestamp = int(getattr(deal, "time", 0) or 0)
        return HistoryDealView(
            time=self._timestamp_gmt7(timestamp) or datetime.fromtimestamp(timestamp, tz=UTC).astimezone(GMT_PLUS_7),
            symbol=str(getattr(deal, "symbol", "") or ""),
            side="SELL" if deal_type == buy_type else "BUY",
            volume=float(getattr(deal, "volume", 0.0) or 0.0),
            price=float(getattr(deal, "price", 0.0) or 0.0),
            profit=profit,
            commission=commission,
            swap=swap,
            net_profit=profit + commission + swap + fee,
            comment=opening_comments.get(position_id, str(getattr(deal, "comment", "") or "")),
        )

    @staticmethod
    def history_stats(deals: Iterable[HistoryDealView], *, raw_deals: int = 0) -> HistoryStats:
        closed = tuple(deal for deal in deals if abs(deal.net_profit) > 1e-9)
        wins = sum(deal.net_profit > 0 for deal in closed)
        losses = sum(deal.net_profit < 0 for deal in closed)
        max_daily_drawdown = 0.0
        by_day: dict[object, list[HistoryDealView]] = {}
        for deal in closed:
            by_day.setdefault(deal.time.date(), []).append(deal)
        for day_deals in by_day.values():
            running = peak = 0.0
            for deal in sorted(day_deals, key=lambda item: item.time):
                running += deal.net_profit
                peak = max(peak, running)
                max_daily_drawdown = max(max_daily_drawdown, peak - running)
        return HistoryStats(
            deals=len(closed), wins=wins, losses=losses,
            net_profit=sum(deal.net_profit for deal in closed),
            winrate=(wins / len(closed) * 100.0) if closed else 0.0,
            max_daily_drawdown=max_daily_drawdown, raw_deals=raw_deals,
        )

    def history(self, start: datetime, end: datetime) -> tuple[tuple[HistoryDealView, ...], HistoryStats]:
        self._ensure_connected()
        raw_deals = self.mt5.history_deals_get(start, end)
        if raw_deals is None:
            raise RuntimeError(f"MT5 không trả về lịch sử lệnh: {self.mt5.last_error()}")
        opening_entry = int(getattr(self.mt5, "DEAL_ENTRY_IN", 0))
        opening_comments = {
            int(getattr(deal, "position_id", 0) or 0): str(getattr(deal, "comment", "") or "")
            for deal in raw_deals
            if int(getattr(deal, "entry", -1)) == opening_entry and int(getattr(deal, "position_id", 0) or 0)
        }
        deals = tuple(sorted(
            (view for deal in raw_deals if (view := self._history_deal_view(deal, opening_comments)) is not None),
            key=lambda item: item.time, reverse=True,
        ))
        return deals, self.history_stats(deals, raw_deals=len(raw_deals))

    def equity_events(self, start: datetime, end: datetime) -> tuple[EquityEvent, ...]:
        """Return deposits and closed-trade P/L for a display-only equity curve.

        Positive DEAL_TYPE_BALANCE records represent deposits. Negative balance
        records (withdrawals) and other balance/credit movements are excluded.
        """
        self._ensure_connected()
        raw_deals = self.mt5.history_deals_get(start, end)
        if raw_deals is None:
            raise RuntimeError(f"MT5 không trả về lịch sử equity: {self.mt5.last_error()}")
        opening_entry = int(getattr(self.mt5, "DEAL_ENTRY_IN", 0))
        opening_comments = {
            int(getattr(deal, "position_id", 0) or 0): str(getattr(deal, "comment", "") or "")
            for deal in raw_deals
            if int(getattr(deal, "entry", -1)) == opening_entry and int(getattr(deal, "position_id", 0) or 0)
        }
        balance_type = int(getattr(self.mt5, "DEAL_TYPE_BALANCE", 2))
        events: list[EquityEvent] = []
        for deal in raw_deals:
            deal_type = int(getattr(deal, "type", -1))
            timestamp = int(getattr(deal, "time", 0) or 0)
            if deal_type == balance_type:
                amount = float(getattr(deal, "profit", 0.0) or 0.0)
                if amount > 0:
                    events.append(EquityEvent(self._timestamp_gmt7(timestamp) or datetime.fromtimestamp(timestamp, tz=UTC).astimezone(GMT_PLUS_7), amount, "DEPOSIT"))
                continue
            view = self._history_deal_view(deal, opening_comments)
            if view is not None:
                events.append(EquityEvent(view.time, view.net_profit, "TRADE"))
        return tuple(sorted(events, key=lambda item: item.time))

    def refresh(self) -> AccountSnapshot:
        self._ensure_connected()
        account = self.mt5.account_info()
        if account is None:
            raise RuntimeError("MT5 chưa có tài khoản đang đăng nhập.")
        positions = tuple(self._position_view(position) for position in self._read_positions())
        current = {position.ticket: position for position in positions}
        entries: list[str] = []
        for ticket, position in current.items():
            if ticket not in self._previous_positions:
                entries.append(f"MỞ {position.side} #{ticket} • {position.symbol} • {position.volume:.2f} lot")
        for ticket, position in self._previous_positions.items():
            if ticket not in current:
                entries.append(f"ĐÓNG #{ticket} • {position.symbol}")
        self._previous_positions = current
        return AccountSnapshot(
            login=int(getattr(account, "login", 0) or 0), server=str(getattr(account, "server", "") or ""),
            balance=float(getattr(account, "balance", 0.0) or 0.0), equity=float(getattr(account, "equity", 0.0) or 0.0),
            currency=str(getattr(account, "currency", "USD") or "USD"), positions=positions, log_entries=tuple(entries),
        )

    def stop(self) -> None:
        if self._connected:
            self.mt5.shutdown()
        self._connected = False
