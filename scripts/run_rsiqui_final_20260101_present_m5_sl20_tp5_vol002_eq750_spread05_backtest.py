from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BASE_RUNNER = ROOT / "scripts" / "run_rsiqui_final_20260101_present_m5_sl20_tp5_vol004_eq750_spread05_backtest.py"
SOURCE_CONFIG = ROOT / "configs" / "strategies" / "rsiqui" / "backtests" / "final_m5_backtest_20260101_present_sl20_tp5_vol002_eq750_spread05.json"
OLD_CONFIG_LITERAL = 'final_m5_backtest_20260101_present_sl20_tp5_vol004_eq750_spread05.json'
OLD_STRATEGY_LITERAL = 'builtin_rsiqui-v3-final_gold-loose_both_vol0.04_risk20_reward5_spreadcap0.5_closeconfirm_blackout_gmt7_M5_20260101_present'
NEW_STRATEGY_LITERAL = 'builtin_rsiqui-v3-final_gold-loose_both_vol0.02_risk20_reward5_spreadcap0.5_closeconfirm_blackout_gmt7_M5_20260101_present'

source = BASE_RUNNER.read_text(encoding="utf-8")
if source.count(OLD_CONFIG_LITERAL) != 1:
    raise RuntimeError("Base runner config literal changed unexpectedly")
if source.count(OLD_STRATEGY_LITERAL) != 1:
    raise RuntimeError("Base runner strategy literal changed unexpectedly")
source = source.replace(OLD_CONFIG_LITERAL, str(SOURCE_CONFIG.relative_to(ROOT / "configs" / "strategies" / "rsiqui")))
source = source.replace(OLD_STRATEGY_LITERAL, NEW_STRATEGY_LITERAL)
exec(compile(source, str(BASE_RUNNER), "exec"), {"__name__": "__main__", "__file__": str(BASE_RUNNER)})
