from pathlib import Path
import json
import os

ROOT = Path(__file__).resolve().parents[1]
BASE_RUNNER = ROOT / "scripts" / "run_rsiqui_final_20260101_present_m5_sl24_tp9_vol003_eq700_spread04_backtest.py"
CONFIG_PATH = ROOT / "configs" / "strategies" / "rsiqui" / os.environ.get("FINAL_TRAILING_BACKTEST_CONFIG", "final_trailing_m5.json")
RAW_CSV_NAME = os.environ.get("FINAL_TRAILING_BACKTEST_RAW_CSV", "XAUUSD_M5_202601012305_202608030925.csv")
START_UTC = os.environ.get("FINAL_TRAILING_BACKTEST_START_UTC", "2026-01-01")
PERIOD_LABEL = os.environ.get("FINAL_TRAILING_BACKTEST_PERIOD_LABEL", "20260101_present")
OLD_CONFIG_LITERAL = "final_m5_backtest_20260101_present_sl24_tp9_vol003_eq700_spread04.json"
OLD_RAW_LITERAL = "XAUUSD_M5_202601020105_202607312345.csv"
OLD_STRATEGY_LITERAL = "builtin_rsiqui-v3-final_gold-loose_both_vol0.03_risk24_reward9_spreadcap0.4_closeconfirm_blackout_gmt7_M5_20260101_present"
_config_payload = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
TRAILING_POLICY = (
    f"activate trailing when bar favorable excursion exceeds ${float(_config_payload['trailing_activation_profit_usd']):g}; "
    f"lock at least +${float(_config_payload['trailing_locked_profit_usd']):g}; "
    f"maintain a ${float(_config_payload['trailing_gap_profit_usd']):g} profit gap; never loosen SL"
)
NEW_STRATEGY_LITERAL = (
    "builtin_rsiqui-v3-final-trailing_gold-loose_both_"
    f"vol{float(_config_payload['volume']):.2f}_risk{float(_config_payload['risk_usd']):g}_"
    f"reward{float(_config_payload['reward_usd']):g}_spreadcap{float(_config_payload['max_spread']):g}_"
    f"trailing_gap{float(_config_payload['trailing_gap_profit_usd']):g}_"
    f"lock{float(_config_payload['trailing_locked_profit_usd']):g}_"
    f"trigger{float(_config_payload['trailing_activation_profit_usd']):g}_M5_{PERIOD_LABEL}"
)

source = BASE_RUNNER.read_text(encoding="utf-8")
for literal, label in (
    (OLD_CONFIG_LITERAL, "config"),
    (OLD_RAW_LITERAL, "raw CSV"),
    (OLD_STRATEGY_LITERAL, "strategy"),
):
    if source.count(literal) != 1:
        raise RuntimeError(f"Base runner {label} literal changed unexpectedly")
source = source.replace(OLD_CONFIG_LITERAL, CONFIG_PATH.name)
source = source.replace(OLD_RAW_LITERAL, RAW_CSV_NAME)
source = source.replace("2026-01-01T00:00:00Z", f"{START_UTC}T00:00:00Z")
source = source.replace("2026-01-01..now", f"{START_UTC}..now")
source = source.replace(OLD_STRATEGY_LITERAL, NEW_STRATEGY_LITERAL)
source = source.replace(
    'if payload["strategy"] != "rsiqui-v3-final" or payload["symbol"] != SYMBOL or payload["timeframe"] != "M5":',
    'if payload["strategy"] != "rsiqui-v3-final-trailing" or payload.get("symbol_base", payload.get("symbol")) != SYMBOL or str(payload["timeframe"]).upper() not in {"M5", "5M"}:',
)
source = source.replace('"This runner only accepts FINAL XAUUSD M5"', '"This runner only accepts FINAL_TRAILING XAUUSD M5"')
source = source.replace(
    '"optimization_policy": "none",',
    '"optimization_policy": "none",\n'
    '        "monthly_equity_reset": bool(payload.get("monthly_equity_reset", False)),\n'
    '        "monthly_reset_day": int(payload.get("monthly_reset_day", 1)),\n'
    '        "trailing_activation_profit_usd": float(payload["trailing_activation_profit_usd"]),\n'
    '        "trailing_locked_profit_usd": float(payload["trailing_locked_profit_usd"]),\n'
    '        "trailing_gap_profit_usd": float(payload["trailing_gap_profit_usd"]),\n'
    '        "trailing_step_profit_usd": float(payload.get("trailing_step_profit_usd", 0.5)),\n'
    f'        "trailing_policy": "{TRAILING_POLICY}",',
)
source = source.replace(
    'equity = config.initial_equity\n    equity_curve: list[float] = []',
    'equity = config.initial_equity\n'
    '    monthly_reset_enabled = bool(payload.get("monthly_equity_reset", False))\n'
    '    monthly_reset_day = int(payload.get("monthly_reset_day", 1))\n'
    '    monthly_reset_months: set[str] = set()\n'
    '    monthly_reset_events: list[dict] = []\n'
    '    trailing_activation_distance = float(payload["trailing_activation_profit_usd"]) / config.quantity\n'
    '    trailing_locked_distance = float(payload["trailing_locked_profit_usd"]) / config.quantity\n'
    '    trailing_gap_distance = float(payload["trailing_gap_profit_usd"]) / config.quantity\n'
    '    equity_curve: list[float] = []',
)
source = source.replace(
    '"overlapping_trades": 0}',
    '"overlapping_trades": 0, "trailing_activated": 0, "trailing_moves": 0, "monthly_reset_events": 0}',
)
source = source.replace(
    '        row = data.iloc[index]\n        had_active_at_start = active is not None',
    '        row = data.iloc[index]\n'
    '        row_month = row["timestamp"].strftime("%Y-%m")\n'
    '        if monthly_reset_enabled and row_month not in monthly_reset_months:\n'
    '            equity = config.initial_equity\n'
    '            monthly_reset_months.add(row_month)\n'
    '            monthly_reset_events.append({"month": row_month, "timestamp": str(row["timestamp"]), "reset_equity": config.initial_equity, "active_position_carried": active is not None})\n'
    '            signal_counts["monthly_reset_events"] += 1\n'
    '        had_active_at_start = active is not None',
)
source = source.replace('"spread": stressed_spread}', '"spread": stressed_spread, "trailing_activated": False}')
source = source.replace(
    '        if active is not None:\n            active["bars_held"] += 1',
    '        if active is not None:\n            active["bars_held"] += 1',
)
source = source.replace(
    '        if had_active_at_start:\n',
    '''        if active is not None:
            activation_distance = trailing_activation_distance
            if active["side"] == "long" and float(row["high"]) > active["entry_price"] + activation_distance:
                favorable_profit = (float(row["high"]) - active["entry_price"]) * config.quantity
                step_profit = float(payload.get("trailing_step_profit_usd", 0.5))
                locked_profit = float(payload["trailing_locked_profit_usd"]) + max(0, int((favorable_profit - float(payload["trailing_activation_profit_usd"])) / step_profit + 1e-12)) * step_profit
                candidate_sl = active["entry_price"] + locked_profit / config.quantity
                if not active["trailing_activated"]:
                    active["trailing_activated"] = True
                    signal_counts["trailing_activated"] += 1
                if candidate_sl > active["stop_price"]:
                    active["stop_price"] = candidate_sl
                    signal_counts["trailing_moves"] += 1
            elif active["side"] == "short" and float(row["low"]) < active["entry_price"] - activation_distance:
                favorable_profit = (active["entry_price"] - float(row["low"])) * config.quantity
                step_profit = float(payload.get("trailing_step_profit_usd", 0.5))
                locked_profit = float(payload["trailing_locked_profit_usd"]) + max(0, int((favorable_profit - float(payload["trailing_activation_profit_usd"])) / step_profit + 1e-12)) * step_profit
                candidate_sl = active["entry_price"] - locked_profit / config.quantity
                if not active["trailing_activated"]:
                    active["trailing_activated"] = True
                    signal_counts["trailing_activated"] += 1
                if candidate_sl < active["stop_price"]:
                    active["stop_price"] = candidate_sl
                    signal_counts["trailing_moves"] += 1
        if had_active_at_start:
''',
)
source = source.replace(
    '"open_position_at_end": open_at_end, "limitations":',
    '"open_position_at_end": open_at_end, "monthly_equity_reset": {"enabled": monthly_reset_enabled, "reset_day": monthly_reset_day, "reset_count": len(monthly_reset_events), "events": monthly_reset_events}, "trailing_policy": {"activation_profit_usd": float(payload["trailing_activation_profit_usd"]), "locked_profit_usd": float(payload["trailing_locked_profit_usd"]), "gap_profit_usd": float(payload["trailing_gap_profit_usd"]), "step_profit_usd": float(payload.get("trailing_step_profit_usd", 0.5)), "ohlc_policy": "uses bar high/low favorable excursion; stepped positive lock; same-bar collision remains SL-first"}, "limitations":',
)
source = source.replace('"variant": "FINAL",', '"variant": "FINAL_TRAILING",')
exec(compile(source, str(BASE_RUNNER), "exec"), {"__name__": "__main__", "__file__": str(BASE_RUNNER)})
