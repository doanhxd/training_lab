from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta, timezone
from typing import Any, Callable, Iterable


RSIQUI_V3_COMMENT = "DoanhHD - RSIQUI V3"
GMT_PLUS_7 = timezone(timedelta(hours=7))
RUNNER_IDENTIFIERS = {
    "rsiqui_v3_ori": ("rsiqui_ori_demo.py", "trading_lab.runners.mt5.rsiqui_ori_demo", "rsiqui_ori_demo"),
    "rsiqui_v3_neg": ("rsiqui_neg_demo.py", "trading_lab.runners.mt5.rsiqui_neg_demo", "rsiqui_neg_demo"),
    "rsiqui_v3_final": ("rsiqui_final_demo.py", "trading_lab.runners.mt5.rsiqui_final_demo", "rsiqui_final_demo"),
    "rsiqui_v3_btcusd": ("rsiqui_btcusd_demo.py", "trading_lab.runners.mt5.rsiqui_btcusd_demo", "rsiqui_btcusd_demo"),
}


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
class RunnerView:
    strategy_key: str
    label: str
    pid: int
    command: str


@dataclass(frozen=True)
class MonitorSnapshot:
    login: int
    server: str
    balance: float
    equity: float
    currency: str
    positions: tuple[PositionView, ...]
    log_entries: tuple[str, ...]
    runner_detection_available: bool
    running_runners: tuple[RunnerView, ...]


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


class RsiquiV3PositionMonitor:
    """Read-only MT5 position monitor for the RSIQUI V3 demo runner.

    This class never authenticates, evaluates signals, sends orders, closes positions,
    or changes MT5 state. It reads the account currently connected in MT5 only.
    """

    def __init__(self, *, symbol: str, mt5: Any, process_iter: Callable[[], Iterable[Any]] | None = None, extra_symbols: Iterable[str] = ("BTCUSD",)) -> None:
        self.symbol = symbol
        self.symbols = self._normalize_symbols((symbol, *tuple(extra_symbols)))
        self._symbol_prefixes = self._normalize_symbol_prefixes(self.symbols)
        self.mt5 = mt5
        self._connected = False
        self._previous_positions: dict[int, PositionView] = {}
        self._process_iter = process_iter or self._default_process_iter

    @staticmethod
    def _normalize_symbols(symbols: Iterable[str]) -> tuple[str, ...]:
        normalized: list[str] = []
        seen: set[str] = set()
        for symbol in symbols:
            value = str(symbol or "").strip()
            if not value:
                continue
            key = value.casefold()
            if key in seen:
                continue
            seen.add(key)
            normalized.append(value)
        return tuple(normalized)

    @staticmethod
    def _normalize_symbol_prefixes(symbols: Iterable[str]) -> tuple[str, ...]:
        """Build broker-independent roots for symbols with suffixes."""
        prefixes: list[str] = []
        seen: set[str] = set()
        for symbol in symbols:
            value = str(symbol or "").strip().upper()
            if not value:
                continue
            for known_root in ("XAUUSD", "BTCUSD"):
                if value.startswith(known_root):
                    value = known_root
                    break
            if value.casefold() not in seen:
                seen.add(value.casefold())
                prefixes.append(value)
        return tuple(prefixes)

    def _position_matches_observed_symbol(self, position: Any) -> bool:
        if not self.symbols:
            return True
        symbol = str(getattr(position, "symbol", "") or "").strip().upper()
        return bool(symbol) and any(symbol.startswith(prefix) for prefix in self._symbol_prefixes)

    def _broker_symbol_variants(self) -> tuple[str, ...]:
        """Discover suffix variants when MT5 requires positions_get(symbol=...)."""
        symbols_get = getattr(self.mt5, "symbols_get", None)
        if not callable(symbols_get):
            return self.symbols
        try:
            broker_symbols = symbols_get() or ()
        except Exception:
            return self.symbols
        variants = list(self.symbols)
        seen = {item.casefold() for item in variants}
        for item in broker_symbols:
            name = str(getattr(item, "name", item) or "").strip()
            if name and any(name.upper().startswith(prefix) for prefix in self._symbol_prefixes) and name.casefold() not in seen:
                variants.append(name)
                seen.add(name.casefold())
        return tuple(variants)

    def _read_positions(self) -> tuple[Any, ...]:
        try:
            raw_positions = self.mt5.positions_get() or ()
        except TypeError:
            raw_positions = None
        if raw_positions is not None:
            return tuple(position for position in raw_positions if self._position_matches_observed_symbol(position))
        collected: list[Any] = []
        seen_tickets: set[int] = set()
        for symbol in self._broker_symbol_variants() or (self.symbol,):
            for position in self.mt5.positions_get(symbol=symbol) or ():
                ticket = int(getattr(position, "ticket", 0) or 0)
                if ticket in seen_tickets:
                    continue
                seen_tickets.add(ticket)
                collected.append(position)
        return tuple(collected)

    @staticmethod
    def _default_process_iter() -> Iterable[Any]:
        try:
            import psutil  # type: ignore
        except ImportError:
            return ()
        return psutil.process_iter(["pid", "cmdline", "name"])

    @staticmethod
    def _process_info(process: Any) -> tuple[int, list[str]]:
        info = getattr(process, "info", None)
        if isinstance(info, dict):
            pid = int(info.get("pid") or 0)
            cmdline = info.get("cmdline") or ()
        else:
            pid = int(getattr(process, "pid", 0) or 0)
            cmdline_method = getattr(process, "cmdline", None)
            cmdline = cmdline_method() if callable(cmdline_method) else ()
        return pid, [str(part) for part in cmdline if part]

    def _running_runners(self) -> tuple[bool, tuple[RunnerView, ...]]:
        try:
            processes = list(self._process_iter())
        except Exception:
            return False, ()
        detected: list[RunnerView] = []
        for process in processes:
            try:
                pid, cmdline_parts = self._process_info(process)
            except Exception:
                continue
            command = " ".join(cmdline_parts).lower()
            if not command:
                continue
            for strategy_key, identifiers in RUNNER_IDENTIFIERS.items():
                if any(identifier.lower() in command for identifier in identifiers):
                    detected.append(
                        RunnerView(
                            strategy_key=strategy_key,
                            label=strategy_key,
                            pid=pid,
                            command=" ".join(cmdline_parts),
                        )
                    )
                    break
        detected.sort(key=lambda runner: (runner.strategy_key, runner.pid))
        return True, tuple(detected)

    def _ensure_connected(self) -> None:
        if self._connected:
            return
        if not self.mt5.initialize():
            raise RuntimeError(f"Không thể kết nối MT5: {self.mt5.last_error()}")
        self._connected = True

    def _broker_utc_offset_hours(self) -> int:
        now_epoch = int(datetime.now(UTC).timestamp())
        freshest_tick_time = 0
        symbol_info_tick = getattr(self.mt5, "symbol_info_tick", None)
        if symbol_info_tick is None:
            return 0
        for symbol in self.symbols or (self.symbol,):
            try:
                tick = symbol_info_tick(symbol)
            except Exception:
                continue
            raw = int(getattr(tick, "time", 0) or 0) if tick is not None else 0
            freshest_tick_time = max(freshest_tick_time, raw)
        if freshest_tick_time <= 0:
            return 0
        offset_hours = round((freshest_tick_time - now_epoch) / 3600)
        return offset_hours if -14 <= offset_hours <= 14 else 0

    def _mt5_timestamp_to_gmt7(self, timestamp: int) -> datetime | None:
        if timestamp <= 0:
            return None
        offset_hours = self._broker_utc_offset_hours()
        return datetime.fromtimestamp(timestamp, tz=UTC) + timedelta(hours=7 - offset_hours)

    def _position_view(self, position: Any) -> PositionView:
        side = "BUY" if position.type == self.mt5.POSITION_TYPE_BUY else "SELL"
        comment = str(getattr(position, "comment", "") or "").upper()
        source = "DoanhHD_GOLD" if RSIQUI_V3_COMMENT in comment else "TAY"
        timestamp = int(getattr(position, "time", 0) or 0)
        opened_at = self._mt5_timestamp_to_gmt7(timestamp)
        return PositionView(
            ticket=int(position.ticket),
            opened_at=opened_at,
            symbol=str(getattr(position, "symbol", self.symbol) or self.symbol),
            side=side,
            volume=float(position.volume),
            price_open=float(position.price_open),
            stop_loss=float(getattr(position, "sl", 0.0)),
            take_profit=float(getattr(position, "tp", 0.0)),
            profit=float(position.profit),
            source=source,
        )

    def _history_deal_view(self, deal: Any) -> HistoryDealView | None:
        deal_type = int(getattr(deal, "type", -1))
        buy_type = int(getattr(self.mt5, "DEAL_TYPE_BUY", 0))
        sell_type = int(getattr(self.mt5, "DEAL_TYPE_SELL", 1))
        if deal_type not in {buy_type, sell_type}:
            return None
        entry = int(getattr(deal, "entry", getattr(self.mt5, "DEAL_ENTRY_OUT", 1)))
        closing_entries = {
            int(getattr(self.mt5, "DEAL_ENTRY_OUT", 1)),
            int(getattr(self.mt5, "DEAL_ENTRY_INOUT", 2)),
        }
        if entry not in closing_entries:
            return None
        timestamp = int(getattr(deal, "time", 0) or 0)
        profit = float(getattr(deal, "profit", 0.0) or 0.0)
        commission = float(getattr(deal, "commission", 0.0) or 0.0)
        swap = float(getattr(deal, "swap", 0.0) or 0.0)
        fee = float(getattr(deal, "fee", 0.0) or 0.0)
        net_profit = profit + commission + swap + fee
        return HistoryDealView(
            time=self._mt5_timestamp_to_gmt7(timestamp) or datetime.fromtimestamp(timestamp, tz=UTC).astimezone(GMT_PLUS_7),
            symbol=str(getattr(deal, "symbol", "") or ""),
            side="BUY" if deal_type == buy_type else "SELL",
            volume=float(getattr(deal, "volume", 0.0) or 0.0),
            price=float(getattr(deal, "price", 0.0) or 0.0),
            profit=profit,
            commission=commission,
            swap=swap,
            net_profit=net_profit,
            comment=str(getattr(deal, "comment", "") or ""),
        )

    @staticmethod
    def history_stats(deals: Iterable[HistoryDealView], *, raw_deals: int = 0) -> HistoryStats:
        deal_list = tuple(deals)
        closed = tuple(deal for deal in deal_list if abs(deal.net_profit) > 1e-9)
        wins = sum(1 for deal in closed if deal.net_profit > 0)
        losses = sum(1 for deal in closed if deal.net_profit < 0)
        by_day: dict[object, list[HistoryDealView]] = {}
        for deal in closed:
            by_day.setdefault(deal.time.date(), []).append(deal)
        max_daily_drawdown = 0.0
        for day_deals in by_day.values():
            running = 0.0
            peak = 0.0
            day_dd = 0.0
            for deal in sorted(day_deals, key=lambda item: item.time):
                running += deal.net_profit
                peak = max(peak, running)
                day_dd = min(day_dd, running - peak)
            max_daily_drawdown = min(max_daily_drawdown, day_dd)
        return HistoryStats(
            deals=len(closed),
            wins=wins,
            losses=losses,
            net_profit=sum(deal.net_profit for deal in closed),
            winrate=(wins / len(closed) * 100.0) if closed else 0.0,
            max_daily_drawdown=abs(max_daily_drawdown),
            raw_deals=raw_deals,
        )

    def history(self, start: datetime, end: datetime) -> tuple[tuple[HistoryDealView, ...], HistoryStats]:
        self._ensure_connected()
        raw_deals = self.mt5.history_deals_get(start, end)
        if raw_deals is None:
            raise RuntimeError(f"MT5 không trả về lịch sử lệnh: {self.mt5.last_error()}")
        deals = tuple(
            sorted(
                (view for deal in raw_deals if (view := self._history_deal_view(deal)) is not None),
                key=lambda item: item.time,
                reverse=True,
            )
        )
        return deals, self.history_stats(deals, raw_deals=len(raw_deals))

    def refresh(self) -> MonitorSnapshot:
        self._ensure_connected()
        account = self.mt5.account_info()
        if account is None:
            raise RuntimeError("MT5 chưa có tài khoản đang đăng nhập.")
        raw_positions = self._read_positions()
        positions = tuple(self._position_view(position) for position in raw_positions)
        current_positions = {position.ticket: position for position in positions}
        log_entries: list[str] = []
        for ticket, position in current_positions.items():
            if ticket not in self._previous_positions:
                log_entries.append(
                    f"{'🟢' if position.source == 'RSIQUI V3' else '•'} "
                    f"{position.source} mở {position.side} #{ticket} | "
                    f"{position.volume:.2f} lot @ {position.price_open:.2f}"
                )
        for ticket, position in self._previous_positions.items():
            if ticket not in current_positions:
                log_entries.append(f"⚪ {position.source} #{ticket} đã đóng / không còn hiển thị trên MT5.")
        self._previous_positions = current_positions
        runner_detection_available, running_runners = self._running_runners()
        return MonitorSnapshot(
            login=int(account.login),
            server=str(account.server),
            balance=float(account.balance),
            equity=float(account.equity),
            currency=str(account.currency),
            positions=positions,
            log_entries=tuple(log_entries),
            runner_detection_available=runner_detection_available,
            running_runners=running_runners,
        )

    def stop(self) -> None:
        if self._connected:
            self.mt5.shutdown()
        self._connected = False
