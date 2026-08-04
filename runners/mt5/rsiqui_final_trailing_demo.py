from __future__ import annotations

from dataclasses import dataclass, replace
import json
import math
from pathlib import Path
from typing import Any

from trading_lab.runners.mt5.rsiqui_final_demo import (
    DemoOnlyRsiquiMt5Runner as FinalRunner,
    Mt5DemoConfig,
)


ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CONFIG = ROOT / "configs" / "strategies" / "rsiqui" / "final_trailing_m5_demo.json"


@dataclass(frozen=True)
class FinalTrailingConfig(Mt5DemoConfig):
    trailing_activation_profit_usd: float = 6.0
    trailing_locked_profit_usd: float = 5.0
    trailing_gap_profit_usd: float = 0.5
    trailing_step_profit_usd: float = 0.5


def _resolve_config_path(path: str | Path) -> Path:
    config_path = Path(path)
    if not config_path.is_absolute() and not config_path.exists():
        candidate = ROOT / "configs" / "strategies" / "rsiqui" / config_path.name
        config_path = candidate if candidate.exists() else Path(__file__).with_name(config_path.name)
    return config_path


def load_demo_config(path: str | Path = DEFAULT_CONFIG) -> Mt5DemoConfig:
    config_path = _resolve_config_path(path)
    payload = json.loads(config_path.read_text(encoding="utf-8"))
    if payload.get("strategy") != "rsiqui-v3-final-trailing":
        raise ValueError("runner accepts only strategy rsiqui-v3-final-trailing")
    timeframe = str(payload["timeframe"]).upper()
    timeframe = {"5M": "M5", "15M": "M15"}.get(timeframe, timeframe)
    if timeframe not in {"M5", "M15"}:
        raise ValueError("timeframe must be 5m/M5 or 15m/M15")
    cap_payload = payload.get("equity_risk_cap_pct", 0.0025)
    cap = None if cap_payload is None else float(cap_payload)
    config = FinalTrailingConfig(
        symbol=str(payload.get("symbol", "XAUUSD")),
        symbol_base=str(payload.get("symbol_base", "XAUUSD")),
        symbol_candidates=tuple(str(symbol) for symbol in payload.get("symbol_candidates", ())),
        entry_mode=str(payload.get("entry_mode", "close_confirm")),
        timeframe=timeframe,
        preset=str(payload["preset"]),
        trade_side=str(payload["side"]),
        volume_lots=float(payload["volume"]),
        price_value_per_lot=float(payload["price_value_per_lot"]),
        risk_usd=float(payload["risk_usd"]),
        reward_usd=float(payload["reward_usd"]),
        equity_risk_cap_pct=cap,
        max_spread_price=float(payload["max_spread"]),
        blocked_entry_hours_gmt7=tuple(int(hour) for hour in payload.get("blocked_entry_hours_gmt7", ())),
        blackout_start_gmt7=(str(payload["blackout_start_gmt7"]) if payload.get("blackout_start_gmt7") else None),
        blackout_until_gmt7=(str(payload["blackout_until_gmt7"]) if payload.get("blackout_until_gmt7") else None),
        telegram_enabled=bool(payload.get("telegram_enabled", False)),
        status_log_interval_seconds=float(payload.get("status_log_interval_seconds", 300.0)),
        magic=int(payload.get("magic", 573504)),
        trailing_activation_profit_usd=float(payload.get("trailing_activation_profit_usd", 6.0)),
        trailing_locked_profit_usd=float(payload.get("trailing_locked_profit_usd", 5.0)),
        trailing_gap_profit_usd=float(payload.get("trailing_gap_profit_usd", 0.5)),
        trailing_step_profit_usd=float(payload.get("trailing_step_profit_usd", 0.5)),
    )
    return config


class DemoOnlyRsiquiFinalTrailingMt5Runner(FinalRunner):
    """FINAL replacement with trigger $6, lock $5, gap $1; no breakeven step."""

    def __init__(self, config: Mt5DemoConfig, *, mt5: Any, notifier: Any = None) -> None:
        super().__init__(config, mt5=mt5, notifier=notifier)
        self.trailing_activation_profit_usd = config.trailing_activation_profit_usd
        self.trailing_locked_profit_usd = config.trailing_locked_profit_usd
        self.trailing_gap_profit_usd = config.trailing_gap_profit_usd

    def _trailing_price_distance(self, profit_usd: float, volume_lots: float) -> float:
        return profit_usd / max(volume_lots * self.config.price_value_per_lot, 1e-12)

    def _trail_open_position(self) -> bool:
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
        if profit_usd <= self.trailing_activation_profit_usd:
            return False

        step_profit = max(float(self.config.trailing_step_profit_usd), 1e-12)
        locked_profit = self.trailing_locked_profit_usd + math.floor(
            (profit_usd - self.trailing_activation_profit_usd) / step_profit + 1e-12
        ) * step_profit
        lock_distance = self._trailing_price_distance(locked_profit, volume)
        minimum_stop_distance = max(
            float(getattr(info, "trade_stops_level", 0)) * float(info.point),
            float(info.trade_tick_size),
        )
        if is_long:
            minimum_lock_sl = entry + lock_distance
            candidate_sl = minimum_lock_sl
            current_sl = float(getattr(position, "sl", 0.0) or 0.0)
            if current_sl > 0 and candidate_sl <= current_sl + float(info.trade_tick_size) / 2:
                return False
        else:
            minimum_lock_sl = entry - lock_distance
            candidate_sl = minimum_lock_sl
            current_sl = float(getattr(position, "sl", 0.0) or 0.0)
            if current_sl > 0 and candidate_sl >= current_sl - float(info.trade_tick_size) / 2:
                return False

        if abs(favorable_price - candidate_sl) < minimum_stop_distance:
            self.last_status = "blocked: trailing SL is below broker minimum stop distance"
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
            self.last_status = f"trailing SL rejected: {None if result is None else result.retcode}"
            return False
        self.last_status = f"trailing SL moved to {candidate_sl:.2f}"
        return True

    def _build_request(self, side: str) -> dict | None:
        request = super()._build_request(side)
        if request is not None:
            request["comment"] = "DoanhHD_GOLT"
        return request

    def poll_once(self, now_utc=None) -> bool:
        if not self._started:
            self.last_status = "blocked: runner not started"
            return False
        self._trail_open_position()
        return super().poll_once(now_utc)


def main() -> None:
    import argparse
    import MetaTrader5 as mt5

    parser = argparse.ArgumentParser(description="Run RSIQUI FINAL trailing replacement on an MT5 account.")
    parser.add_argument("--config", default=str(DEFAULT_CONFIG))
    parser.add_argument("--symbol", default=None)
    parser.add_argument("--poll-seconds", type=float, default=1.0)
    parser.add_argument("--status-log-interval-seconds", type=float, default=None)
    args = parser.parse_args()
    config = load_demo_config(args.config)
    overrides = {"poll_seconds": args.poll_seconds}
    if args.symbol is not None:
        overrides["symbol"] = args.symbol
    if args.status_log_interval_seconds is not None:
        overrides["status_log_interval_seconds"] = args.status_log_interval_seconds
    config = replace(config, **overrides)
    DemoOnlyRsiquiFinalTrailingMt5Runner(config, mt5=mt5).run_forever()


if __name__ == "__main__":
    main()
