from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
import argparse
import json
import math
from pathlib import Path
import time
from typing import Any

import pandas as pd

from trading_lab.strategies.builtins.rsiqui.btcusd import RsiquiV3Config, evaluate_rsiqui_v3_signal, prepare_rsiqui_v3_frame, rsiqui_v3_config_for_preset
from trading_lab.telegram_notifier import TelegramNotifier, TelegramSettings, format_filled_order_message


@dataclass(frozen=True)
class Mt5DemoConfig:
    symbol: str = "XAUUSD"
    symbol_base: str = "BTCUSD"
    symbol_candidates: tuple[str, ...] = ()
    entry_mode: str = "close_confirm"
    timeframe: str = "M5"
    preset: str = "gold-loose"
    trade_side: str = "both"
    volume_lots: float = 0.01
    price_value_per_lot: float = 100.0
    risk_usd: float = 10.0
    reward_usd: float = 20.0
    equity_risk_cap_pct: float | None = 0.0025
    max_spread_price: float = 1.2
    poll_seconds: float = 1.0
    magic: int = 573503
    deviation_points: int = 20
    blocked_entry_hours_gmt7: tuple[int, ...] = ()
    telegram_enabled: bool = False
    status_log_interval_seconds: float = 300.0
    preclose_check_min_seconds: int = 1
    preclose_check_max_seconds: int = 5
    postclose_confirm_max_seconds: int = 5
    trailing_activation_profit_usd: float = 5.0
    trailing_locked_profit_usd: float = 4.0
    trailing_step_profit_usd: float = 0.5


def load_demo_config(path: str | Path) -> Mt5DemoConfig:
    config_path = Path(path)
    if not config_path.is_absolute() and not config_path.exists():
        candidate = Path(__file__).resolve().parents[2] / "configs" / "strategies" / "rsiqui" / config_path.name
        if candidate.exists():
            config_path = candidate
        else:
            config_path = Path(__file__).with_name(config_path.name)
    payload = json.loads(config_path.read_text(encoding="utf-8"))
    if payload.get("strategy") != "rsiqui-v3-btcusd":
        raise ValueError("runner accepts only strategy rsiqui-v3-btcusd")
    timeframe = str(payload["timeframe"]).upper()
    timeframe = {"5M": "M5", "15M": "M15"}.get(timeframe, timeframe)
    if timeframe not in {"M5", "M15"}:
        raise ValueError("timeframe must be 5m/M5 or 15m/M15")
    cap_payload = payload.get("equity_risk_cap_pct", 0.0025)
    equity_risk_cap_pct = None if cap_payload is None else float(cap_payload)
    return Mt5DemoConfig(
        timeframe=timeframe,
        entry_mode=str(payload.get("entry_mode", "close_confirm")),
        symbol=str(payload.get("symbol", "BTCUSD")),
        symbol_base=str(payload.get("symbol_base", "BTCUSD")),
        symbol_candidates=tuple(str(symbol) for symbol in payload.get("symbol_candidates", ())),
        preset=str(payload["preset"]),
        trade_side=str(payload["side"]),
        volume_lots=float(payload["volume"]),
        price_value_per_lot=float(payload["price_value_per_lot"]),
        risk_usd=float(payload["risk_usd"]),
        reward_usd=float(payload["reward_usd"]),
        equity_risk_cap_pct=equity_risk_cap_pct,
        max_spread_price=float(payload["max_spread"]),
        magic=int(payload.get("magic", 573505)),
        blocked_entry_hours_gmt7=tuple(int(hour) for hour in payload.get("blocked_entry_hours_gmt7", ())),
        telegram_enabled=bool(payload.get("telegram_enabled", False)),
        status_log_interval_seconds=float(payload.get("status_log_interval_seconds", 300.0)),
        trailing_activation_profit_usd=float(payload.get("trailing_activation_profit_usd", 5.0)),
        trailing_locked_profit_usd=float(payload.get("trailing_locked_profit_usd", 4.0)),
        trailing_step_profit_usd=float(payload.get("trailing_step_profit_usd", 0.5)),
    )


class DemoOnlyRsiquiMt5Runner:
    """Closed-bar BTCUSD RSIQUI V3 executor for the active MT5 account."""

    def __init__(self, config: Mt5DemoConfig, *, mt5: Any, notifier: TelegramNotifier | None = None) -> None:
        self.config = config
        self.mt5 = mt5
        self.notifier = notifier or TelegramNotifier(TelegramSettings.from_environment(enabled=config.telegram_enabled))
        self.last_status = "not started"
        self._started = False
        self._last_submitted_bar: int | None = None
        self._last_evaluated_bar: int | None = None
        self._last_confirmed_bar: int | None = None
        self._pending_preclose_signal: tuple[int, str] | None = None
        self._effective_risk_usd = config.risk_usd
        self._account_equity: float | None = None
        self._last_status_log_at: float | None = None
        self._resolved_symbol = config.symbol
        self._last_immediate_attempt_bar: int | None = None
        self._last_immediate_attempt_side: str | None = None

    def _active_symbol(self, now: datetime | None = None) -> str:
        return self._resolved_symbol

    def _symbol_candidates(self) -> tuple[str, ...]:
        ordered = [self.config.symbol, *self.config.symbol_candidates]
        seen: set[str] = set()
        result: list[str] = []
        for symbol in ordered:
            if symbol and symbol not in seen:
                seen.add(symbol)
                result.append(symbol)
        return tuple(result)

    def _resolve_symbol(self) -> str | None:
        for candidate in self._symbol_candidates():
            if self.mt5.symbol_info(candidate) is not None:
                return candidate
        symbols_get = getattr(self.mt5, "symbols_get", None)
        if symbols_get is None:
            return None
        try:
            available = symbols_get() or ()
        except Exception:
            return None
        base = self.config.symbol_base.upper()
        names = []
        for item in available:
            name = str(getattr(item, "name", ""))
            upper = name.upper()
            if upper.startswith(base) and "." not in name:
                names.append(name)
        preferred = {name.upper(): index for index, name in enumerate(self._symbol_candidates())}
        names.sort(key=lambda name: (preferred.get(name.upper(), len(preferred)), name.upper()))
        return names[0] if names else None

    def _symbols_to_guard(self) -> tuple[str, ...]:
        return (self._active_symbol(),)

    def _max_spread_price_for_symbol(self, symbol: str) -> float:
        return float(self.config.max_spread_price)

    def _money_contract_for_symbol(self, symbol: str) -> tuple[float, float, float]:
        return float(self.config.volume_lots), float(self.config.risk_usd), float(self.config.reward_usd)

    def _price_value_per_lot_for_symbol(self, symbol: str) -> float:
        return float(self.config.price_value_per_lot)

    def _effective_risk_for(self, risk_usd: float) -> float:
        if self.config.equity_risk_cap_pct is None:
            return float(risk_usd)
        if self._account_equity is None:
            return min(float(risk_usd), 0.0)
        return min(float(risk_usd), float(self._account_equity) * self.config.equity_risk_cap_pct)

    def _symbols_required_for_start(self) -> tuple[str, ...]:
        return (self._active_symbol(),)

    def start(self) -> bool:
        if not self.mt5.initialize():
            self.last_status = f"MT5 initialize failed: {self.mt5.last_error()}"
            return False
        account = self.mt5.account_info()
        if account is None:
            self.last_status = "BLOCKED: MT5 account information unavailable"
            self.mt5.shutdown()
            return False
        resolved_symbol = self._resolve_symbol()
        if resolved_symbol is None:
            self.last_status = f"BLOCKED: No available symbol variant for {self.config.symbol_base}"
            self.mt5.shutdown()
            return False
        self._resolved_symbol = resolved_symbol
        self._account_equity = float(account.equity)
        active_symbol = self._active_symbol()
        _, active_risk_usd, _ = self._money_contract_for_symbol(active_symbol)
        self._effective_risk_usd = self._effective_risk_for(active_risk_usd)
        if self._effective_risk_usd <= 0:
            self.last_status = "BLOCKED: Non-positive account equity/risk cap"
            self.mt5.shutdown()
            return False
        for symbol in self._symbols_required_for_start():
            if not self.mt5.symbol_select(symbol, True):
                self.last_status = f"BLOCKED: Cannot select symbol {symbol}"
                self.mt5.shutdown()
                return False
            if self.mt5.symbol_info(symbol) is None:
                self.last_status = f"BLOCKED: Unavailable symbol {symbol}"
                self.mt5.shutdown()
                return False
        self._started = True
        self.last_status = f"READY: {self._active_symbol()} {self.config.timeframe} RSIQUI V3 BTCUSD ({self.config.preset})"
        return True

    def stop(self) -> None:
        if self._started:
            self.mt5.shutdown()
        self._started = False
        self.last_status = "stopped"

    def _strategy_config(self) -> RsiquiV3Config:
        symbol = self._active_symbol()
        volume_lots, risk_usd, reward_usd = self._money_contract_for_symbol(symbol)
        return rsiqui_v3_config_for_preset(
            self.config.preset,
            volume_lots=volume_lots,
            price_value_per_lot=self._price_value_per_lot_for_symbol(symbol),
            risk_usd=risk_usd,
            reward_usd=reward_usd,
            max_spread=self._max_spread_price_for_symbol(symbol),
            trade_side=self.config.trade_side,
        )

    def _evaluate_signal_bar(self, bar_time: int, *, active: bool) -> tuple[str | None, int | None]:
        symbol = self._active_symbol()
        timeframe = getattr(self.mt5, f"TIMEFRAME_{self.config.timeframe}")
        rates = self.mt5.copy_rates_from_pos(symbol, timeframe, 0, 200)
        if rates is None or len(rates) < 121:
            self.last_status = f"BLOCKED: Insufficient {self.config.timeframe} history for {symbol}"
            return None, None
        frame = pd.DataFrame(rates)
        frame = frame[frame["time"] <= bar_time]
        if frame.empty or int(frame.iloc[-1]["time"]) != bar_time:
            kind = "active" if active else "closed"
            self.last_status = f"BLOCKED: {kind} {self.config.timeframe} bar {bar_time} is unavailable"
            return None, None
        frame["timestamp"] = pd.to_datetime(frame["time"], unit="s", utc=True)
        frame["spread"] = frame["spread"] * float(self.mt5.symbol_info(symbol).point)
        strategy_config = self._strategy_config()
        prepared = prepare_rsiqui_v3_frame(frame, strategy_config)
        row = prepared.iloc[-1]
        side = evaluate_rsiqui_v3_signal(row, strategy_config)
        return side, int(row["time"])

    def evaluate_preclose_bar(self, bar_time: int) -> tuple[str | None, int | None]:
        # Preview only: never submit from the forming bar.
        return self._evaluate_signal_bar(bar_time, active=True)

    def evaluate_confirmed_close_bar(self, bar_time: int) -> tuple[str | None, int | None]:
        # Entry confirmation: submit only after this bar has closed and the
        # closed-bar signal matches the preview captured before close.
        return self._evaluate_signal_bar(bar_time, active=False)

    def _seconds_per_bar(self) -> int:
        return {"M5": 5 * 60, "M15": 15 * 60}[self.config.timeframe]

    def _bar_open_timestamp(self, now_utc: datetime) -> int:
        seconds_per_bar = self._seconds_per_bar()
        return int(now_utc.timestamp()) // seconds_per_bar * seconds_per_bar

    def _seconds_until_bar_close(self, now_utc: datetime) -> float:
        seconds_per_bar = self._seconds_per_bar()
        elapsed = now_utc.timestamp() - self._bar_open_timestamp(now_utc)
        return seconds_per_bar - elapsed

    def _is_preclose_entry_window(self, now_utc: datetime) -> bool:
        remaining = self._seconds_until_bar_close(now_utc)
        return self.config.preclose_check_min_seconds <= remaining <= self.config.preclose_check_max_seconds

    def _seconds_since_bar_open(self, now_utc: datetime) -> float:
        return now_utc.timestamp() - self._bar_open_timestamp(now_utc)

    def _is_postclose_confirm_window(self, now_utc: datetime) -> bool:
        return 0 <= self._seconds_since_bar_open(now_utc) <= self.config.postclose_confirm_max_seconds

    def _last_closed_bar_timestamp(self, now_utc: datetime) -> int:
        return self._bar_open_timestamp(now_utc) - self._seconds_per_bar()

    def _current_tick_time(self) -> datetime:
        tick = self.mt5.symbol_info_tick(self._active_symbol())
        tick_time = getattr(tick, "time", None) if tick is not None else None
        if tick_time is not None:
            return datetime.fromtimestamp(int(tick_time), tz=UTC)
        return datetime.now(tz=UTC)

    def _open_positions_exist(self) -> bool:
        positions_get = getattr(self.mt5, "positions_get", None)
        if positions_get is None:
            return False
        for symbol in self._symbols_to_guard():
            if positions_get(symbol=symbol):
                return True
        return False

    def _waiting_open_position_status(self) -> str:
        return "waiting: a BTCUSD position is already open; runner staying alive until the position closes"

    def _trailing_price_distance(self, profit_usd: float, volume_lots: float) -> float:
        return profit_usd / max(volume_lots * self.config.price_value_per_lot, 1e-12)

    def _trail_open_position(self) -> bool:
        """Move BTCUSD SL into profit after $5, ratcheting every $0.50."""
        positions_get = getattr(self.mt5, "positions_get", None)
        if positions_get is None:
            return False
        positions = positions_get(symbol=self._active_symbol()) or ()
        positions = tuple(
            position for position in positions
            if int(getattr(position, "magic", self.config.magic)) == int(self.config.magic)
        )
        if not positions:
            return False
        position = positions[0]
        if not all(hasattr(position, field) for field in ("type", "price_open", "ticket")):
            return False
        tick = self.mt5.symbol_info_tick(self._active_symbol())
        info = self.mt5.symbol_info(self._active_symbol())
        if tick is None or info is None:
            return False

        is_long = int(position.type) == int(self.mt5.ORDER_TYPE_BUY)
        entry = float(position.price_open)
        volume = float(getattr(position, "volume", self.config.volume_lots))
        favorable_price = float(tick.bid if is_long else tick.ask)
        direction = 1.0 if is_long else -1.0
        profit_usd = (favorable_price - entry) * volume * self.config.price_value_per_lot * direction
        activation = float(self.config.trailing_activation_profit_usd)
        if profit_usd <= activation:
            return False

        step_profit = max(float(self.config.trailing_step_profit_usd), 1e-12)
        locked_profit = float(self.config.trailing_locked_profit_usd) + math.floor(
            (profit_usd - activation) / step_profit + 1e-12
        ) * step_profit
        lock_distance = self._trailing_price_distance(locked_profit, volume)
        minimum_stop_distance = max(
            float(getattr(info, "trade_stops_level", 0)) * float(info.point),
            float(info.trade_tick_size),
        )
        current_sl = float(getattr(position, "sl", 0.0) or 0.0)
        if is_long:
            candidate_sl = entry + lock_distance
            if current_sl > 0 and candidate_sl <= current_sl + float(info.trade_tick_size) / 2:
                return False
        else:
            candidate_sl = entry - lock_distance
            if current_sl > 0 and candidate_sl >= current_sl - float(info.trade_tick_size) / 2:
                return False

        if abs(favorable_price - candidate_sl) < minimum_stop_distance:
            self.last_status = "blocked: BTCUSD trailing SL is below broker minimum stop distance"
            return False
        candidate_sl = round(candidate_sl, int(info.digits))
        request = {
            "action": self.mt5.TRADE_ACTION_SLTP,
            "symbol": self._active_symbol(),
            "position": int(position.ticket),
            "sl": candidate_sl,
            "tp": float(getattr(position, "tp", 0.0) or 0.0),
        }
        result = self.mt5.order_send(request)
        if result is None or result.retcode != self.mt5.TRADE_RETCODE_DONE:
            self.last_status = f"trailing BTCUSD SL rejected: {None if result is None else result.retcode}"
            return False
        self.last_status = f"trailing BTCUSD SL moved to {candidate_sl:.2f} (locked ${locked_profit:.2f})"
        return True

    @staticmethod
    def _floor_volume(raw: float, minimum: float, maximum: float, step: float) -> float:
        if raw < minimum or step <= 0:
            return 0.0
        bounded = min(raw, maximum)
        return math.floor((bounded + 1e-12) / step) * step

    def _build_request(self, side: str) -> dict | None:
        symbol = self._active_symbol()
        info = self.mt5.symbol_info(symbol)
        tick = self.mt5.symbol_info_tick(symbol)
        if info is None or tick is None or info.trade_tick_size <= 0 or info.trade_tick_value <= 0:
            self.last_status = "BLOCKED: Incomplete symbol/tick metadata"
            return None
        spread = float(tick.ask - tick.bid)
        max_spread_price = self._max_spread_price_for_symbol(symbol)
        if spread > max_spread_price:
            self.last_status = f"BLOCKED: {symbol} spread {spread:.3f} > cap {max_spread_price:.3f}"
            return None
        entry = float(tick.ask if side == "long" else tick.bid)
        configured_volume_lots, configured_risk_usd, configured_reward_usd = self._money_contract_for_symbol(symbol)
        effective_risk_usd = self._effective_risk_for(configured_risk_usd)
        price_value_per_lot = self._price_value_per_lot_for_symbol(symbol)
        raw_stop_distance = effective_risk_usd / max(configured_volume_lots * price_value_per_lot, 1e-12)
        stops_level = float(getattr(info, "trade_stops_level", 0))
        min_stop_distance = max(stops_level * float(info.point), float(info.trade_tick_size))
        stop_distance = max(raw_stop_distance, min_stop_distance)
        loss_per_lot = stop_distance / float(info.trade_tick_size) * float(info.trade_tick_value)
        risk_volume_cap = effective_risk_usd / loss_per_lot
        volume = self._floor_volume(min(configured_volume_lots, risk_volume_cap), float(info.volume_min), float(info.volume_max), float(info.volume_step))
        if volume <= 0:
            self.last_status = "BLOCKED: broker minimum volume exceeds effective risk cap"
            return None
        actual_risk = stop_distance / float(info.trade_tick_size) * float(info.trade_tick_value) * volume
        if actual_risk > effective_risk_usd + 1e-9:
            self.last_status = "BLOCKED: rounded volume exceeds configured risk cap"
            return None
        reward_ratio = configured_reward_usd / max(configured_risk_usd, 1e-12)
        reward_distance = stop_distance * reward_ratio
        filling_mode = int(getattr(info, "filling_mode", 0))
        if filling_mode & 1:  # SYMBOL_FILLING_FOK
            filling = self.mt5.ORDER_FILLING_FOK
        elif filling_mode & 2:  # SYMBOL_FILLING_IOC
            filling = self.mt5.ORDER_FILLING_IOC
        else:
            self.last_status = "BLOCKED: broker exposes no supported FOK/IOC filling mode"
            return None
        digits = int(info.digits)
        is_long = side == "long"
        return {
            "action": self.mt5.TRADE_ACTION_DEAL,
            "symbol": symbol,
            "volume": volume,
            "type": self.mt5.ORDER_TYPE_BUY if is_long else self.mt5.ORDER_TYPE_SELL,
            "price": entry,
            "sl": round(entry - stop_distance if is_long else entry + stop_distance, digits),
            "tp": round(entry + reward_distance if is_long else entry - reward_distance, digits),
            "deviation": self.config.deviation_points,
            "magic": self.config.magic,
            "comment": "BTC_DoanhHD",
            "type_time": self.mt5.ORDER_TIME_GTC,
            "type_filling": filling,
        }

    def _entry_time_from_signal_bar(self, bar_time: int) -> datetime:
        seconds_per_bar = self._seconds_per_bar()
        return datetime.fromtimestamp(bar_time + seconds_per_bar, tz=UTC) + timedelta(hours=7)

    def _is_gmt7_entry_blackout(self, bar_time: int) -> bool:
        return self._entry_time_from_signal_bar(bar_time).hour in self.config.blocked_entry_hours_gmt7

    def poll_once(self, now_utc: datetime | None = None) -> bool:
        if not self._started:
            self.last_status = "BLOCKED: runner not started"
            return False
        self._trail_open_position()
        # Scheduling must follow the wall clock, not the broker tick timestamp.
        # During quiet/stale ticks MT5 can keep symbol_info_tick().time frozen,
        # which makes the runner appear stuck at the same seconds-since-open and
        # can miss the M5 close-confirm windows entirely.
        now_utc = datetime.now(tz=UTC) if now_utc is None else now_utc.astimezone(UTC)

        if self.config.entry_mode == "immediate_signal":
            bar_time = self._bar_open_timestamp(now_utc)
            if self._last_immediate_attempt_bar == bar_time:
                side_label = (self._last_immediate_attempt_side or "unknown").capitalize()
                self.last_status = f"WAITING: {side_label} signal already handled for bar {bar_time}; no duplicate order"
                return False
            side, evaluated_bar = self._evaluate_signal_bar(bar_time, active=True)
            if side is None or evaluated_bar is None:
                self.last_status = "No immediate BTCUSD signal" if not self.last_status.startswith("BLOCKED:") else self.last_status
                return False
            if self._open_positions_exist():
                self.last_status = self._waiting_open_position_status()
                return False
            self._last_immediate_attempt_bar = evaluated_bar
            self._last_immediate_attempt_side = side
            if self._is_gmt7_entry_blackout(evaluated_bar):
                local_time = self._entry_time_from_signal_bar(evaluated_bar)
                self.last_status = f"BLOCKED: GMT+7 blackout at {local_time:%H:%M}"
                return False
            if evaluated_bar == self._last_submitted_bar:
                self.last_status = f"BLOCKED: Duplicate immediate {side.capitalize()} signal setup"
                return False
            request = self._build_request(side)
            if request is None:
                return False
            result = self.mt5.order_send(request)
            if result is None or result.retcode != self.mt5.TRADE_RETCODE_DONE:
                self.last_status = f"OR: {None if result is None else result.retcode}"
                return False
            self._last_submitted_bar = evaluated_bar
            self.last_status = f"OF: {side} ticket {getattr(result, 'order', '?')} immediate signal bar {evaluated_bar}"
            message = format_filled_order_message(
                symbol=str(request.get("symbol", self.config.symbol)),
                side=side,
                request=request,
                ticket=getattr(result, "order", "?"),
                timeframe=self.config.timeframe,
            )
            if not self.notifier.send(message) and self.config.telegram_enabled:
                self.last_status += f"; {self.notifier.last_status}"
            return True

        if self._is_preclose_entry_window(now_utc):
            bar_time = self._bar_open_timestamp(now_utc)
            if bar_time == self._last_evaluated_bar:
                self.last_status = "BLOCKED: duplicate pre-close preview for this bar"
                return False
            self._last_evaluated_bar = bar_time
            self._pending_preclose_signal = None
            if self._open_positions_exist():
                self.last_status = self._waiting_open_position_status()
                return False
            side, evaluated_bar = self.evaluate_preclose_bar(bar_time)
            if side is None or evaluated_bar is None:
                self.last_status = "No pre-close preview BTCUSD signal" if not self.last_status.startswith("BLOCKED:") else self.last_status
                return False
            self._pending_preclose_signal = (evaluated_bar, side)
            self.last_status = f"PREVIEW: {side} {self.config.timeframe} bar {evaluated_bar}; waiting for candle close confirmation"
            return False

        if not self._is_postclose_confirm_window(now_utc):
            remaining = self._seconds_until_bar_close(now_utc)
            elapsed = self._seconds_since_bar_open(now_utc)
            self.last_status = f"WAITING: Outside {self.config.timeframe} close-confirm windows ({elapsed:.1f}s since open, {remaining:.1f}s to close)"
            return False

        closed_bar = self._last_closed_bar_timestamp(now_utc)
        if closed_bar == self._last_confirmed_bar:
            self.last_status = "BLOCKED: Duplicate close confirmation for this bar"
            return False
        self._last_confirmed_bar = closed_bar
        pending = self._pending_preclose_signal
        if pending is None or pending[0] != closed_bar:
            self.last_status = "BLOCKED: No matching pre-close preview for the just-closed bar"
            return False
        if self._open_positions_exist():
            self.last_status = self._waiting_open_position_status()
            return False
        close_side, evaluated_bar = self.evaluate_confirmed_close_bar(closed_bar)
        if close_side is None or evaluated_bar is None:
            self.last_status = "BLOCKED: Closed candle no longer has RSIQUI V3 signal" if not self.last_status.startswith("BLOCKED:") else self.last_status
            return False
        preview_side = pending[1]
        if evaluated_bar != closed_bar or close_side != preview_side:
            self.last_status = f"BLOCKED: Pre-close preview {preview_side} does not match closed-candle signal {close_side}"
            return False
        if self._is_gmt7_entry_blackout(evaluated_bar):
            local_time = self._entry_time_from_signal_bar(evaluated_bar)
            self.last_status = f"BLOCKED: GMT+7 blackout at {local_time:%H:%M}"
            return False
        if evaluated_bar == self._last_submitted_bar:
            self.last_status = "BLOCKED: Duplicate closed-candle setup"
            return False
        request = self._build_request(close_side)
        if request is None:
            return False
        result = self.mt5.order_send(request)
        if result is None or result.retcode != self.mt5.TRADE_RETCODE_DONE:
            self.last_status = f"OR: {None if result is None else result.retcode}"
            return False
        self._last_submitted_bar = evaluated_bar
        self._pending_preclose_signal = None
        self.last_status = f"OF: {close_side} ticket {getattr(result, 'order', '?')} after {self.config.timeframe} candle close {evaluated_bar}"
        message = format_filled_order_message(
            symbol=str(request.get("symbol", self.config.symbol)),
            side=close_side,
            request=request,
            ticket=getattr(result, "order", "?"),
            timeframe=self.config.timeframe,
        )
        if not self.notifier.send(message) and self.config.telegram_enabled:
            self.last_status += f"; {self.notifier.last_status}"
        return True

    def should_print_status(self, now: float | None = None) -> bool:
        """Rate-limit repetitive terminal output while preserving order-fill evidence."""
        now = time.monotonic() if now is None else now
        urgent = self.last_status.startswith(("OF:", "OR:"))
        if urgent or self._last_status_log_at is None or now - self._last_status_log_at >= self.config.status_log_interval_seconds:
            self._last_status_log_at = now
            return True
        return False

    def _terminal_status_line(self) -> str:
        return f"[{datetime.now():%H:%M:%S}] {self.last_status}"

    def run_forever(self) -> None:
        if not self.start():
            print(self._terminal_status_line(), flush=True)
            return
        print(self._terminal_status_line(), flush=True)
        try:
            while True:
                self.poll_once()
                if self.should_print_status():
                    print(self._terminal_status_line(), flush=True)
                time.sleep(self.config.poll_seconds)
        finally:
            self.stop()


def main() -> None:
    parser = argparse.ArgumentParser(description="Run RSIQUI V3 BTCUSD on the currently logged-in MT5 account.")
    parser.add_argument("--config", default="btcusd_m5_demo.json")
    parser.add_argument("--symbol", default="BTCUSD")
    parser.add_argument("--poll-seconds", type=float, default=1.0)
    parser.add_argument("--status-log-interval-seconds", type=float, default=None)
    args = parser.parse_args()
    config = load_demo_config(args.config)
    overrides = {"symbol": args.symbol, "poll_seconds": args.poll_seconds}
    if args.status_log_interval_seconds is not None:
        overrides["status_log_interval_seconds"] = args.status_log_interval_seconds
    config = Mt5DemoConfig(**(config.__dict__ | overrides))
    import MetaTrader5 as mt5
    DemoOnlyRsiquiMt5Runner(config, mt5=mt5).run_forever()


if __name__ == "__main__":
    main()
