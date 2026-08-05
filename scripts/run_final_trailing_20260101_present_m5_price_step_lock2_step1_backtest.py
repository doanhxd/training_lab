from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BASE_RUNNER = ROOT / "scripts" / "run_final_trailing_20260101_present_m5_sl12_tp5_vol003_new_config_backtest.py"
SOURCE_CONFIG = ROOT / "configs" / "strategies" / "rsiqui" / "final_trailing_20260101_present_price_step_lock2_step1_vol002_risk24_tp10.json"
OLD_CONFIG = "final_trailing_m5.json"
OLD_RAW = "XAUUSD_M5_202601020105_202608040610.csv"
NEW_RAW = "XAUUSD_M5_202601020105_202608041330.csv"
OLD_STRATEGY = "builtin_rsiqui-v3-final-trailing_gold-loose_both_vol0.03_risk36_reward15_activation2_lock4_step0.5_blackout_gmt7_M5_20260101_present"
NEW_STRATEGY = "builtin_rsiqui-v3-final-trailing_gold-loose_both_vol0.02_risk24_reward10_activation2_lock2_step1_blackout_gmt7_M5_20260101_present_price_step"

source = BASE_RUNNER.read_text(encoding="utf-8")
for literal, label in ((OLD_CONFIG, "config"), (OLD_RAW, "raw CSV"), (OLD_STRATEGY, "strategy")):
    if source.count(literal) != 1:
        raise RuntimeError(f"Base runner {label} literal changed unexpectedly")
source = source.replace(OLD_CONFIG, SOURCE_CONFIG.name)
source = source.replace(OLD_RAW, NEW_RAW)
source = source.replace(OLD_STRATEGY, NEW_STRATEGY)
exec(compile(source, str(BASE_RUNNER), "exec"), {"__name__": "__main__", "__file__": str(BASE_RUNNER)})
