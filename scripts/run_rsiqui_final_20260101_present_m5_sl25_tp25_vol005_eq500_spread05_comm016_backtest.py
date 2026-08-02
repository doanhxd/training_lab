from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BASE_RUNNER = ROOT / "scripts" / "run_rsiqui_final_20260101_present_m5_sl24_tp9_vol003_eq700_spread04_backtest.py"
SOURCE_CONFIG = ROOT / "configs" / "strategies" / "rsiqui" / "final_m5_backtest_20260101_present_sl25_tp25_vol005_eq500_spread05_comm016.json"
OLD_CONFIG_LITERAL = 'final_m5_backtest_20260101_present_sl24_tp9_vol003_eq700_spread04.json'
OLD_STRATEGY_LITERAL = 'builtin_rsiqui-v3-final_gold-loose_both_vol0.03_risk24_reward9_spreadcap0.4_closeconfirm_blackout_gmt7_M5_20260101_present'
NEW_STRATEGY_LITERAL = 'builtin_rsiqui-v3-final_gold-loose_both_vol0.05_risk25_reward25_spreadcap0.5_comm0.16_closeconfirm_blackout_gmt7_M5_20260101_present'

source = BASE_RUNNER.read_text(encoding="utf-8")
if source.count(OLD_CONFIG_LITERAL) != 1:
    raise RuntimeError("Base runner config literal changed unexpectedly")
if source.count(OLD_STRATEGY_LITERAL) != 1:
    raise RuntimeError("Base runner strategy literal changed unexpectedly")
source = source.replace(OLD_CONFIG_LITERAL, SOURCE_CONFIG.name)
source = source.replace(OLD_STRATEGY_LITERAL, NEW_STRATEGY_LITERAL)
exec(compile(source, str(BASE_RUNNER), "exec"), {"__name__": "__main__", "__file__": str(BASE_RUNNER)})
