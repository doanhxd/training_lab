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

from trading_lab.strategies.builtins.rsiqui.ori import RsiquiV3Config, evaluate_rsiqui_v3_signal, prepare_rsiqui_v3_frame, rsiqui_v3_config_for_preset
from trading_lab.telegram_notifier import TelegramNotifier, TelegramSettings, format_filled_order_message


@dataclass(frozen=True)
class Mt5DemoConfig:
    symbol: str = "XAUUSD"
    weekend_symbol: str | None = "XAUUSD.24-7"
    timeframe: str = "M5"
    preset: str = "gold-loose"
    trade_side: str = "both"
    volume_lots: float = 0.01
    price_value_per_lot: float = 100.0
    risk_usd: float = 10.0
    reward_usd: float = 20.0
    max_spread_price: float = 1.2
    poll_seconds: float = 5.0
    magic: int = 573503
    deviation_points: int = 20
    max_open_positions: int = 1
    blocked_entry_hours_gmt7: tuple[int, ...] = ()
    telegram_enabled: bool = False
    status_log_interval_seconds: float = 300.0

def load_demo_config(path: str | Path) -> Mt5DemoConfig:
    config_path = Path(path)
    if not config_path.is_absolute() and not config_path.exists():
        candidate = Path(__file__).resolve().parents[2] / "configs" / "strategies" / "rsiqui" / config_path.name
        if candidate.exists():
            config_path = candidate
        else:
            config_path = Path(__file__).with_name(config_path.name)
    payload = json.loads(config_path.read_text(encoding="utf-8"))
    if payload.get("strategy") != "rsiqui-v3-ori":
        raise ValueError("runner accepts only strategy rsiqui-v3-ori")
    timeframe = str(payload["timeframe"]).upper()
    timeframe = {"5M": "M5", "15M": "M15"}.get(timeframe, timeframe)
    if timeframe not in {"M5", "M15"}:
        raise ValueError("timeframe must be 5m/M5 or 15m/M15")
    return Mt5DemoConfig(
        timeframe=timeframe,
        weekend_symbol=payload.get("weekend_symbol", "XAUUSD.24-7"),
        preset=str(payload["preset"]),
        trade_side=str(payload["side"]),
        volume_lots=float(payload["volume"]),
        price_value_per_lot=float(payload["price_value_per_lot"]),
        risk_usd=float(payload["risk_usd"]),
        reward_usd=float(payload["reward_usd"]),
        max_spread_price=float(payload["max_spread"]),
        max_open_positions=max(1, int(payload.get("max_open_positions", 1))),
        blocked_entry_hours_gmt7=tuple(int(hour) for hour in payload.get("blocked_entry_hours_gmt7", ())),
        telegram_enabled=bool(payload.get("telegram_enabled", False)),
        status_log_interval_seconds=float(payload.get("status_log_interval_seconds", 300.0)),
    )


class DemoOnlyRsiquiMt5Runner:


    def __init__(self, config: Mt5DemoConfig, *, mt5: Any, notifier: TelegramNotifier | None = None) -> None:
        self.config = config
        self.mt5 = mt5
        self.notifier = notifier or TelegramNotifier(TelegramSettings.from_environment(enabled=config.telegram_enabled))
        self.last_status = "not started"
        self._started = False
        self._last_submitted_bar: int | None = None
        self._effective_risk_usd = config.risk_usd
        self._last_status_log_at: float | None = None

    def _is_weekend_symbol_session(self, now: datetime | None = None) -> bool:
        """Use the broker 24/7 gold symbol during UTC Saturday/Sunday sessions."""
        now = now or datetime.now(UTC)
        return now.weekday() >= 5

    def _active_symbol(self, now: datetime | None = None) -> str:
        weekend_symbol = self.config.weekend_symbol
        if weekend_symbol and self._is_weekend_symbol_session(now):
            return str(weekend_symbol)
        return self.config.symbol

    def _symbols_to_guard(self) -> tuple[str, ...]:
        symbols = [self.config.symbol]
        if self.config.weekend_symbol and self.config.weekend_symbol not in symbols:
            symbols.append(str(self.config.weekend_symbol))
        return tuple(symbols)

    def _symbols_required_for_start(self) -> tuple[str, ...]:
        symbols = [self.config.symbol]
        active_symbol = self._active_symbol()
        if active_symbol not in symbols:
            symbols.append(active_symbol)
        return tuple(symbols)

    def start(self) -> bool:
        if not self.mt5.initialize():
            self.last_status = f"MT5 initialize failed: {self.mt5.last_error()}"
            return False
        account = self.mt5.account_info()
        if account is None or account.trade_mode != self.mt5.ACCOUNT_TRADE_MODE_DEMO:
            self.last_status = "blocked: a DEMO MT5 account is required"
            self.mt5.shutdown()
            return False
        self._effective_risk_usd = min(self.config.risk_usd, float(account.equity) * 0.0025)
        if self._effective_risk_usd <= 0:
            self.last_status = "blocked: non-positive demo equity/risk cap"
            self.mt5.shutdown()
            return False
        for symbol in self._symbols_required_for_start():
            if not self.mt5.symbol_select(symbol, True):
                self.last_status = f"blocked: cannot select symbol {symbol}"
                self.mt5.shutdown()
                return False
            if self.mt5.symbol_info(symbol) is None:
                self.last_status = f"blocked: unavailable symbol {symbol}"
                self.mt5.shutdown()
                return False
        self._started = True
        self.last_status = f"ready: DEMO {self._active_symbol()} {self.config.timeframe} closed-bar RSIQUI V3 ORI ({self.config.preset})"
        return True

    def stop(self) -> None:
        if self._started:
            self.mt5.shutdown()
        self._started = False
        self.last_status = "stopped"

    def _strategy_config(self) -> RsiquiV3Config:
        return rsiqui_v3_config_for_preset(
            self.config.preset,
            volume_lots=self.config.volume_lots,
            price_value_per_lot=self.config.price_value_per_lot,
            risk_usd=self.config.risk_usd,
            reward_usd=self.config.reward_usd,
            max_spread=self.config.max_spread_price,
            trade_side=self.config.trade_side,
            blocked_entry_hours_gmt7=self.config.blocked_entry_hours_gmt7,
        )

    def evaluate_closed_bar(self) -> tuple[str | None, int | None]:
        symbol = self._active_symbol()
        timeframe = getattr(self.mt5, f"TIMEFRAME_{self.config.timeframe}")
        rates = self.mt5.copy_rates_from_pos(symbol, timeframe, 0, 200)
        if rates is None or len(rates) < 121:
            self.last_status = f"blocked: insufficient {self.config.timeframe} history for {symbol}"
            return None, None
        frame = pd.DataFrame(rates)
        frame["timestamp"] = pd.to_datetime(frame["time"], unit="s", utc=True)
        frame["spread"] = frame["spread"] * float(self.mt5.symbol_info(symbol).point)
        strategy_config = self._strategy_config()
        prepared = prepare_rsiqui_v3_frame(frame, strategy_config)
        row = prepared.iloc[-2]  # Never trade from the forming bar.
        side = evaluate_rsiqui_v3_signal(row, strategy_config)
        return side, int(row["time"])

    def _open_positions_count(self) -> int:
        positions_get = getattr(self.mt5, "positions_get", None)
        if positions_get is None:
            return 0
        total = 0
        for symbol in self._symbols_to_guard():
            total += len(positions_get(symbol=symbol) or ())
        return total

    def _waiting_open_position_status(self, open_positions: int) -> str:
        return f"waiting: {open_positions} XAUUSD positions already open (cap {self.config.max_open_positions}); runner staying alive until capacity frees up"

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
            self.last_status = "blocked: incomplete symbol/tick metadata"
            return None
        spread = float(tick.ask - tick.bid)
        if spread > self.config.max_spread_price:
            self.last_status = f"blocked: spread {spread:.3f} > cap {self.config.max_spread_price:.3f}"
            return None
        entry = float(tick.ask if side == "long" else tick.bid)
        raw_stop_distance = self._effective_risk_usd / max(self.config.volume_lots * self.config.price_value_per_lot, 1e-12)
        stops_level = float(getattr(info, "trade_stops_level", 0))
        min_stop_distance = max(stops_level * float(info.point), float(info.trade_tick_size))
        stop_distance = max(raw_stop_distance, min_stop_distance)
        loss_per_lot = stop_distance / float(info.trade_tick_size) * float(info.trade_tick_value)
        risk_volume_cap = self._effective_risk_usd / loss_per_lot
        volume = self._floor_volume(min(self.config.volume_lots, risk_volume_cap), float(info.volume_min), float(info.volume_max), float(info.volume_step))
        if volume <= 0:
            self.last_status = "blocked: broker minimum volume exceeds effective risk cap"
            return None
        actual_risk = stop_distance / float(info.trade_tick_size) * float(info.trade_tick_value) * volume
        if actual_risk > self._effective_risk_usd + 1e-9:
            self.last_status = "blocked: rounded volume exceeds configured risk cap"
            return None
        reward_ratio = self.config.reward_usd / max(self.config.risk_usd, 1e-12)
        reward_distance = stop_distance * reward_ratio
        filling_mode = int(getattr(info, "filling_mode", 0))
        if filling_mode & 1:  # SYMBOL_FILLING_FOK
            filling = self.mt5.ORDER_FILLING_FOK
        elif filling_mode & 2:  # SYMBOL_FILLING_IOC
            filling = self.mt5.ORDER_FILLING_IOC
        else:
            self.last_status = "blocked: broker exposes no supported FOK/IOC filling mode"
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
            "comment": "DoanhHD_Trader O",
            "type_time": self.mt5.ORDER_TIME_GTC,
            "type_filling": filling,
        }

    def _entry_time_from_signal_bar(self, bar_time: int) -> datetime:
        seconds_per_bar = {"M5": 5 * 60, "M15": 15 * 60}[self.config.timeframe]
        return datetime.fromtimestamp(bar_time + seconds_per_bar, tz=UTC) + timedelta(hours=7)

    def _is_gmt7_entry_blackout(self, bar_time: int) -> bool:
        return self._entry_time_from_signal_bar(bar_time).hour in self.config.blocked_entry_hours_gmt7

    def poll_once(self) -> bool:
        if not self._started:
            self.last_status = "blocked: runner not started"
            return False
        open_positions = self._open_positions_count()
        if open_positions >= self.config.max_open_positions:
            self.last_status = self._waiting_open_position_status(open_positions)
            return False
        side, bar_time = self.evaluate_closed_bar()
        if side is None or bar_time is None:
            self.last_status = "no closed-bar RSIQUI V3 ORI signal"
            return False
        if self._is_gmt7_entry_blackout(bar_time):
            local_time = self._entry_time_from_signal_bar(bar_time)
            self.last_status = f"blocked: GMT+7 blackout at {local_time:%H:%M}"
            return False
        if bar_time == self._last_submitted_bar:
            self.last_status = "blocked: duplicate closed-bar setup"
            return False
        request = self._build_request(side)
        if request is None:
            return False
        result = self.mt5.order_send(request)
        if result is None or result.retcode != self.mt5.TRADE_RETCODE_DONE:
            self.last_status = f"order rejected: {None if result is None else result.retcode}"
            return False
        self._last_submitted_bar = bar_time
        self.last_status = f"order filled: {side} ticket {getattr(result, 'order', '?')} on {self.config.timeframe} bar {bar_time}"
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

    def should_print_status(self, now: float | None = None) -> bool:
        """Rate-limit repetitive terminal output while preserving order-fill evidence."""
        now = time.monotonic() if now is None else now
        urgent = self.last_status.startswith(("order filled:", "order rejected:"))
        if urgent or self._last_status_log_at is None or now - self._last_status_log_at >= self.config.status_log_interval_seconds:
            self._last_status_log_at = now
            return True
        return False

    def run_forever(self) -> None:
        if not self.start():
            print(self.last_status, flush=True)
            return
        print(self.last_status, flush=True)
        try:
            while True:
                self.poll_once()
                if self.should_print_status():
                    print(self.last_status, flush=True)
                time.sleep(self.config.poll_seconds)
        finally:
            self.stop()


def main() -> None:
    parser = argparse.ArgumentParser(description="Run RSIQUI V3 ORI on a currently logged-in MT5 DEMO account only.")
    parser.add_argument("--config", default="configs/strategies/rsiqui/ori_m5_demo.json")
    parser.add_argument("--symbol", default="XAUUSD")
    parser.add_argument("--weekend-symbol", default="XAUUSD.24-7")
    parser.add_argument("--poll-seconds", type=float, default=5.0)
    parser.add_argument("--status-log-interval-seconds", type=float, default=None)
    args = parser.parse_args()
    config = load_demo_config(args.config)
    overrides = {"symbol": args.symbol, "weekend_symbol": args.weekend_symbol, "poll_seconds": args.poll_seconds}
    if args.status_log_interval_seconds is not None:
        overrides["status_log_interval_seconds"] = args.status_log_interval_seconds
    config = Mt5DemoConfig(**(config.__dict__ | overrides))
    import MetaTrader5 as mt5
    DemoOnlyRsiquiMt5Runner(config, mt5=mt5).run_forever()


if __name__ == "__main__":
    main()
