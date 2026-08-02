from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BASE_RUNNER = ROOT / "scripts" / "run_rsiqui_final_20260101_present_m5_sl24_tp9_vol003_eq700_spread04_backtest.py"
SOURCE_CONFIG = ROOT / "configs" / "strategies" / "rsiqui" / "final_m5_backtest_20240101_present_sl25_tp25_vol005_eq500_spread05_comm016_monthly_reset.json"
OLD_CONFIG_LITERAL = 'final_m5_backtest_20260101_present_sl24_tp9_vol003_eq700_spread04.json'
OLD_RAW_LITERAL = 'XAUUSD_M5_202601020105_202607312345.csv'
NEW_RAW_LITERAL = 'XAUUSD_M5_202401012300_202607312345.csv'
OLD_STRATEGY_LITERAL = 'builtin_rsiqui-v3-final_gold-loose_both_vol0.03_risk24_reward9_spreadcap0.4_closeconfirm_blackout_gmt7_M5_20260101_present'
NEW_STRATEGY_LITERAL = 'builtin_rsiqui-v3-final_gold-loose_both_vol0.05_risk25_reward25_spreadcap0.5_comm0.16_monthly_reset_20240101_present_closeconfirm_blackout_gmt7_M5'

source = BASE_RUNNER.read_text(encoding="utf-8")
for literal, label in ((OLD_CONFIG_LITERAL, "config"), (OLD_RAW_LITERAL, "raw CSV"), (OLD_STRATEGY_LITERAL, "strategy")):
    if source.count(literal) != 1:
        raise RuntimeError(f"Base runner {label} literal changed unexpectedly")
source = source.replace(OLD_CONFIG_LITERAL, SOURCE_CONFIG.name)
source = source.replace(OLD_RAW_LITERAL, NEW_RAW_LITERAL)
source = source.replace(OLD_STRATEGY_LITERAL, NEW_STRATEGY_LITERAL)
source = source.replace(
    'REQUESTED_START_UTC = pd.Timestamp("2026-01-01T00:00:00Z")',
    'REQUESTED_START_UTC = pd.Timestamp("2024-01-01T00:00:00Z")',
    1,
)
source = source.replace(
    '"requested_period_utc": "2026-01-01T00:00:00Z..now"',
    '"requested_period_utc": "2024-01-01T00:00:00Z..now"',
    1,
)
source = source.replace(
    '"optimization_policy": "none",',
    '"optimization_policy": "none",\n        "monthly_equity_reset": bool(payload.get("monthly_equity_reset", False)),\n        "monthly_reset_day": int(payload.get("monthly_reset_day", 1)),\n        "entry_block_when_position_active": True,',
    1,
)
source = source.replace(
    'equity = config.initial_equity\n    equity_curve: list[float] = []',
    'equity = config.initial_equity\n    monthly_reset_enabled = bool(payload.get("monthly_equity_reset", False))\n    monthly_reset_day = int(payload.get("monthly_reset_day", 1))\n    monthly_reset_months: set[str] = set()\n    monthly_reset_events: list[dict] = []\n    equity_curve: list[float] = []',
    1,
)
source = source.replace(
    '        had_active_at_start = active is not None',
    '        row_month = row["timestamp"].strftime("%Y-%m")\n        if monthly_reset_enabled and row_month not in monthly_reset_months:\n            equity = config.initial_equity\n            monthly_reset_months.add(row_month)\n            monthly_reset_events.append({"month": row_month, "timestamp": str(row["timestamp"]), "reset_equity": config.initial_equity, "active_position_carried": active is not None})\n        had_active_at_start = active is not None',
    1,
)
source = source.replace(
    '"open_position_at_end": open_at_end, "limitations":',
    '"open_position_at_end": open_at_end, "monthly_equity_reset": {"enabled": monthly_reset_enabled, "reset_day": monthly_reset_day, "reset_count": len(monthly_reset_events), "events": monthly_reset_events}, "entry_block_when_position_active": True, "limitations":',
    1,
)
exec(compile(source, str(BASE_RUNNER), "exec"), {"__name__": "__main__", "__file__": str(BASE_RUNNER)})
