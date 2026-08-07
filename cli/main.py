from __future__ import annotations

import argparse
import json
from pathlib import Path

import pandas as pd

from training_lab.backtest.execution.engine import FixedLotConfig, run_backtest, run_fixed_lot_backtest
from training_lab.backtest.metrics.performance import compute_metrics
from training_lab.config import DATA_PROCESSED_DIR
from training_lab.config_loader import load_run_config, run_config_hash
from training_lab.data.loaders.csv_loader import load_raw_csv
from training_lab.data.providers.yahoo import fetch_yahoo_xauusd, write_raw_yahoo_xauusd
from training_lab.data.quality import inspect_candles
from training_lab.data.transforms.normalize import normalize_candles, write_processed_dataset
from training_lab.features.engine import build_features, write_feature_set
from training_lab.hermes.adapters.mock import generate_and_remember_strategy, generate_mock_strategy
from training_lab.hermes.adapters.llm import generate_llm_strategy
from training_lab.hermes.memory import HermesMemoryStore
from training_lab.hermes.research_agent import record_research_cycle, summarize_research_cycle
from training_lab.hermes.skills import get_skill_profile, list_skill_profiles
from training_lab.hermes.xauusd_knowledge import assess_strategy_against_policy, describe_policy
from training_lab.models import StrategySpec, ValidationConfig
from training_lab.storage.artifacts import build_leaderboard, persist_leaderboard, persist_run_artifacts, run_id_for
from training_lab.strategies.builtins.freqtrade_ewo_gold import freqtrade_ewo_config_for_preset, run_freqtrade_ewo_backtest
from training_lab.strategies.builtins.best5m import best5m_config_for_preset, run_best5m_backtest
from training_lab.strategies.builtins.e0v1e import e0v1e_config_for_preset, run_e0v1e_backtest
from training_lab.strategies.builtins.freqtrade_support import freqtrade_support_config_for_preset, run_freqtrade_support_backtest
from training_lab.strategies.builtins.gold_fusion import GoldFusionConfig, run_gold_fusion_backtest
from training_lab.strategies.builtins.h1_wick_fill import WickFillConfig, run_h1_upper_wick_fill_backtest
from training_lab.strategies.builtins.ichi_v1 import ichi_v1_config_for_preset, run_ichi_v1_backtest
from training_lab.strategies.builtins.m15_structure import FixedRiskConfig, run_m15_structure_backtest
from training_lab.strategies.builtins.rsiqui_v3 import rsiqui_v3_config_for_preset, run_rsiqui_v3_backtest
from training_lab.strategies.builtins.smart_liquidity import smart_liquidity_config_for_preset, run_smart_liquidity_backtest
from training_lab.strategies.compiler.validator import validate_strategy_spec
from training_lab.strategies.schema.spec import load_strategy_spec
from training_lab.validation.suite import validate_strategy
from training_lab.validation.fixed_lot import validate_fixed_lot_strategy


def _load_dataset(dataset_id: str) -> tuple[pd.DataFrame, dict]:
    base = DATA_PROCESSED_DIR / dataset_id
    metadata = json.loads((base / "metadata.json").read_text(encoding="utf-8"))
    return pd.read_csv(base / "candles.csv"), metadata


def _load_feature_frame(dataset_id: str, feature_set_id: str) -> pd.DataFrame:
    return pd.read_csv(DATA_PROCESSED_DIR / dataset_id / "features" / f"{feature_set_id}.csv")


def _prepare_dataset(input_path: Path, symbol: str, timeframe: str) -> dict:
    raw = load_raw_csv(input_path)
    quality = inspect_candles(raw)
    normalized = normalize_candles(raw, symbol=symbol, timeframe=timeframe)
    metadata = write_processed_dataset(normalized, input_path, symbol, timeframe)
    return {"metadata": metadata.model_dump(), "quality": quality.as_dict()}


def _build_features(dataset_id: str) -> dict:
    dataset, metadata = _load_dataset(dataset_id)
    features = build_features(dataset)
    feature_metadata = write_feature_set(dataset_id, metadata["timeframe"], features)
    return feature_metadata.model_dump()


def cmd_fetch_data(args: argparse.Namespace) -> None:
    if args.provider != "yahoo":
        raise ValueError(f"unsupported provider: {args.provider}")
    raw = fetch_yahoo_xauusd(period=args.period, interval=args.interval, ticker=args.ticker)
    path = write_raw_yahoo_xauusd(raw, period=args.period, interval=args.interval, ticker=args.ticker)
    quality = inspect_candles(raw)
    print(json.dumps({"raw_path": str(path), "provider": "yahoo", "ticker": args.ticker, "quality": quality.as_dict()}, indent=2, default=str, allow_nan=False))


def cmd_inspect_data(args: argparse.Namespace) -> None:
    raw = load_raw_csv(Path(args.input))
    quality = inspect_candles(raw, min_rows=args.min_rows)
    print(json.dumps(quality.as_dict(), indent=2, allow_nan=False))


def cmd_prepare_data(args: argparse.Namespace) -> None:
    result = _prepare_dataset(Path(args.input), args.symbol, args.timeframe)
    print(json.dumps(result, indent=2, default=str, allow_nan=False))


def cmd_build_features(args: argparse.Namespace) -> None:
    print(json.dumps(_build_features(args.dataset_id), indent=2, default=str))


def cmd_generate_strategy(args: argparse.Namespace) -> None:
    memory = HermesMemoryStore()
    if args.adapter == "llm":
        spec = generate_llm_strategy(skill_name=args.skill, memory=memory)
        if args.remember:
            memory.append_strategy(spec, skill_name=args.skill or "llm_selected", status="generated")
    elif args.remember:
        spec = generate_and_remember_strategy(skill_name=args.skill, memory=memory)
    else:
        spec = generate_mock_strategy(skill_name=args.skill, memory=memory)
    print(json.dumps(spec.model_dump(), indent=2))


def cmd_validate_spec(args: argparse.Namespace) -> None:
    spec = load_strategy_spec(Path(args.spec))
    validate_strategy_spec(spec)
    print(json.dumps({"valid": True, "strategy_name": spec.strategy_name}, indent=2))


def _execute_backtest(args: argparse.Namespace) -> dict:
    spec = load_strategy_spec(Path(args.spec))
    validate_strategy_spec(spec)
    features = _load_feature_frame(args.dataset_id, args.feature_set_id)
    run_config = load_run_config(Path(args.config) if getattr(args, "config", None) else None)
    config_hash = run_config_hash(run_config)
    trades, equity_curve = run_backtest(features, spec, run_config.backtest)
    metrics = compute_metrics(trades, equity_curve, run_config.backtest.initial_equity)
    validation = validate_strategy(features, spec, run_config.backtest, run_config.validation)
    run_id = run_id_for(spec.strategy_name, args.dataset_id, config_hash)
    memory = HermesMemoryStore()
    memory.append_strategy(
        spec=spec,
        skill_name=getattr(args, "skill", None) or "external",
        status="accepted" if validation.passed else "rejected",
        dataset_id=args.dataset_id,
        feature_set_id=args.feature_set_id,
        run_id=run_id,
        metrics=metrics,
        validation=validation,
    )
    artifact_paths = persist_run_artifacts(
        run_id=run_id,
        raw_spec=spec.model_dump(),
        normalized_spec=spec.model_dump(),
        metadata={
            "strategy_name": spec.strategy_name,
            "dataset_id": args.dataset_id,
            "feature_set_id": args.feature_set_id,
            "config_hash": config_hash,
            "run_config": run_config.model_dump(),
            "backtest_config": run_config.backtest.model_dump(),
            "metrics_summary": metrics.model_dump(),
            "validation_results": validation.model_dump(),
        },
        trades=[trade.model_dump() for trade in trades],
        equity_curve=equity_curve,
    )
    return {
        "run_id": run_id,
        "strategy_name": spec.strategy_name,
        "metrics": metrics.model_dump(),
        "validation": validation.model_dump(),
        "artifacts": artifact_paths,
    }


def cmd_run_backtest(args: argparse.Namespace) -> None:
    result = _execute_backtest(args)
    print(json.dumps(result, indent=2, default=str, allow_nan=False))


def cmd_run_fixed_lot(args: argparse.Namespace) -> None:
    spec = load_strategy_spec(Path(args.spec))
    validate_strategy_spec(spec)
    features = _load_feature_frame(args.dataset_id, args.feature_set_id)
    config = FixedLotConfig(
        initial_equity=args.initial_equity,
        volume_lots=args.volume,
        price_value_per_lot=args.price_value_per_lot,
        risk_usd=args.risk_usd,
        reward_usd=args.reward_usd,
        synthetic_spread=args.synthetic_spread,
        slippage_per_side=args.slippage_per_side,
        max_spread=args.max_spread,
        time_exit_bars=args.time_exit_bars,
    )
    trades, equity_curve = run_fixed_lot_backtest(features, spec, config)
    metrics = compute_metrics(trades, equity_curve, config.initial_equity)
    config_payload = config.__dict__ | {
        "quantity": config.quantity,
        "stop_distance": config.stop_distance,
        "take_profit_distance": config.take_profit_distance,
    }
    run_id = run_id_for(spec.strategy_name, args.dataset_id, json.dumps(config_payload, sort_keys=True))
    validation_config = ValidationConfig(
        min_trades=args.min_trades,
        max_drawdown=args.max_drawdown,
        oos_fraction=args.oos_fraction,
        walk_forward_train_fraction=args.walk_forward_train_fraction,
        walk_forward_test_fraction=args.walk_forward_test_fraction,
        min_walk_forward_windows=args.min_walk_forward_windows,
        min_profitable_windows_fraction=args.min_profitable_windows_fraction,
        spread_stress_multiplier=args.spread_stress_multiplier,
        perturbation_take_profit_multiplier=args.perturbation_reward_multiplier,
    )
    validation = validate_fixed_lot_strategy(
        features,
        spec,
        config,
        validation_config,
        {
            "provider": args.provider,
            "ticker": args.ticker,
            "missing_spread_fraction": args.missing_spread_fraction,
        },
    )
    artifact_paths = persist_run_artifacts(
        run_id=run_id,
        raw_spec=spec.model_dump(),
        normalized_spec=spec.model_dump(),
        metadata={
            "strategy_name": spec.strategy_name,
            "dataset_id": args.dataset_id,
            "feature_set_id": args.feature_set_id,
            "config_hash": "fixed_lot",
            "run_config": {"fixed_lot": config_payload},
            "backtest_config": config_payload,
            "metrics_summary": metrics.model_dump(),
            "validation_results": validation.model_dump(),
        },
        trades=[trade.model_dump() for trade in trades],
        equity_curve=equity_curve,
    )
    leaderboard = build_leaderboard(dataset_id=args.dataset_id, feature_set_id=args.feature_set_id)
    leaderboard_path = persist_leaderboard(leaderboard)
    print(
        json.dumps(
            {
                "run_id": run_id,
                "strategy_name": spec.strategy_name,
                "fixed_lot_config": config_payload,
                "metrics": metrics.model_dump(),
                "validation": validation.model_dump(),
                "artifacts": artifact_paths,
                "leaderboard_path": str(leaderboard_path),
            },
            indent=2,
            default=str,
            allow_nan=False,
        )
    )


def _execute_batch(args: argparse.Namespace) -> dict:
    results = []
    memory = HermesMemoryStore()
    for index in range(args.count):
        spec = generate_and_remember_strategy(skill_name=args.skill, memory=memory)
        temp_spec = Path(args.output_spec or f"/tmp/trading-lab-batch-spec-{index}.json")
        temp_spec.write_text(json.dumps(spec.model_dump(), indent=2), encoding="utf-8")
        result = _execute_backtest(
            argparse.Namespace(
                spec=str(temp_spec),
                dataset_id=args.dataset_id,
                feature_set_id=args.feature_set_id,
                skill=args.skill or "memory_selected",
                config=args.config,
            )
        )
        results.append(result)
    leaderboard = build_leaderboard(dataset_id=args.dataset_id, feature_set_id=args.feature_set_id)
    leaderboard_path = persist_leaderboard(leaderboard)
    return {"runs": results, "leaderboard_path": str(leaderboard_path), "leaderboard": leaderboard}


def cmd_run_batch(args: argparse.Namespace) -> None:
    print(json.dumps(_execute_batch(args), indent=2, default=str, allow_nan=False))


def cmd_run_builtin(args: argparse.Namespace) -> None:
    if args.strategy == "m15-structure":
        result = _run_builtin_m15_structure(args)
    elif args.strategy == "h1-wick-fill":
        result = _run_builtin_h1_wick_fill(args)
    elif args.strategy == "freqtrade-ewo":
        result = _run_builtin_freqtrade_ewo(args)
    elif args.strategy == "freqtrade-support":
        result = _run_builtin_freqtrade_support(args)
    elif args.strategy == "gold-fusion":
        result = _run_builtin_gold_fusion(args)
    elif args.strategy == "e0v1e":
        result = _run_builtin_e0v1e(args)
    elif args.strategy == "best5m":
        result = _run_builtin_best5m(args)
    elif args.strategy == "smart-liquidity":
        result = _run_builtin_smart_liquidity(args)
    elif args.strategy == "ichi-v1":
        result = _run_builtin_ichi_v1(args)
    elif args.strategy == "rsiqui-v3":
        result = _run_builtin_rsiqui_v3(args)
    else:
        raise ValueError(f"unsupported built-in strategy: {args.strategy}")
    print(json.dumps(result, indent=2, default=str, allow_nan=False))


def _persist_builtin_result(args: argparse.Namespace, strategy_name: str, config_payload: dict, result, validation: dict) -> dict:
    run_id = run_id_for(strategy_name, args.dataset_id, json.dumps(config_payload, sort_keys=True))
    artifact_paths = persist_run_artifacts(
        run_id=run_id,
        raw_spec={"strategy": args.strategy, "source": "built_in_research"},
        normalized_spec={"strategy": args.strategy, "config": config_payload},
        metadata={
            "strategy_name": strategy_name,
            "dataset_id": args.dataset_id,
            "feature_set_id": args.feature_set_id,
            "config_hash": "builtin",
            "run_config": {"builtin": config_payload},
            "backtest_config": config_payload,
            "metrics_summary": result.metrics.model_dump(),
            "validation_results": validation,
            "signal_counts": result.signal_counts,
        },
        trades=[trade.model_dump() for trade in result.trades],
        equity_curve=result.equity_curve,
    )
    leaderboard = build_leaderboard(dataset_id=args.dataset_id, feature_set_id=args.feature_set_id)
    leaderboard_path = persist_leaderboard(leaderboard)
    return {
        "run_id": run_id,
        "strategy_name": strategy_name,
        "metrics": result.metrics.model_dump(),
        "signal_counts": result.signal_counts,
        "validation": validation,
        "artifacts": artifact_paths,
        "leaderboard_path": str(leaderboard_path),
    }


def _basic_builtin_validation(metrics, signal_counts: dict, min_trades: int, max_drawdown: float) -> dict:
    validation = {
        "passed": metrics.net_return > 0 and metrics.total_trades >= min_trades and metrics.max_drawdown <= max_drawdown,
        "reasons": [],
        "checks": metrics.model_dump() | {"signal_counts": signal_counts},
    }
    if metrics.total_trades < min_trades:
        validation["reasons"].append("too few trades")
    if metrics.max_drawdown > max_drawdown:
        validation["reasons"].append("excessive drawdown")
    if metrics.net_return <= 0:
        validation["reasons"].append("net return is not positive")
    return validation


def _run_builtin_m15_structure(args: argparse.Namespace) -> dict:
    features = _load_feature_frame(args.dataset_id, args.feature_set_id)
    config = FixedRiskConfig(
        risk_usd=args.risk_usd,
        reward_risk=args.reward_risk,
        synthetic_spread=args.synthetic_spread,
        slippage_per_side=args.slippage_per_side,
        max_spread=args.max_spread,
        max_positions=1,
        side_filter=args.side,
        session_filter=args.session,
    )
    result = run_m15_structure_backtest(features, config)
    strategy_name = f"builtin_{args.strategy}_risk{args.risk_usd:g}_rr{args.reward_risk:g}_{args.side}_{args.session}"
    validation = _basic_builtin_validation(result.metrics, result.signal_counts, args.min_trades, args.max_drawdown)
    return _persist_builtin_result(args, strategy_name, config.__dict__, result, validation)


def _run_builtin_h1_wick_fill(args: argparse.Namespace) -> dict:
    features = _load_feature_frame(args.dataset_id, args.feature_set_id)
    config = WickFillConfig(
        risk_usd=args.risk_usd,
        reward_risk=args.reward_risk,
        synthetic_spread=args.synthetic_spread,
        slippage_per_side=args.slippage_per_side,
        max_spread=args.max_spread,
        min_body_atr=args.min_body_atr,
        min_upper_wick_body_ratio=args.min_upper_wick_body_ratio,
        min_upper_wick_atr=args.min_upper_wick_atr,
        confirmation_delay_bars=args.confirmation_delay_bars,
        max_entry_distance_atr=args.max_entry_distance_atr,
        min_target_distance=args.min_target_distance,
        max_target_distance=args.max_target_distance,
        session_filter=args.session,
    )
    result = run_h1_upper_wick_fill_backtest(features, config)
    strategy_name = f"builtin_{args.strategy}_risk{args.risk_usd:g}_rr{args.reward_risk:g}_{args.session}_body{args.min_body_atr:g}_wick{args.min_upper_wick_body_ratio:g}"
    validation = _basic_builtin_validation(result.metrics, result.signal_counts, args.min_trades, args.max_drawdown)
    return _persist_builtin_result(args, strategy_name, config.__dict__, result, validation)


def _run_builtin_freqtrade_ewo(args: argparse.Namespace) -> dict:
    features = _load_feature_frame(args.dataset_id, args.feature_set_id)
    allowed_tags = tuple(tag.strip() for tag in args.freqtrade_allowed_tags.split(",") if tag.strip())
    allowed_hours = tuple(int(hour.strip()) for hour in args.freqtrade_allowed_hours.split(",") if hour.strip())
    config = freqtrade_ewo_config_for_preset(
        args.freqtrade_preset,
        initial_equity=args.initial_equity,
        volume_lots=args.volume,
        price_value_per_lot=args.price_value_per_lot,
        risk_usd=args.risk_usd,
        reward_usd=args.reward_usd,
        synthetic_spread=args.synthetic_spread,
        slippage_per_side=args.slippage_per_side,
        max_spread=args.max_spread,
        session_filter=args.session,
        trade_side=args.freqtrade_side,
        allowed_signal_tags=allowed_tags or ("ewo", "buy_1", "ewo buy_1"),
        allowed_entry_hours=allowed_hours,
        use_fastk_breakeven_exit=args.use_fastk_exit,
    )
    result = run_freqtrade_ewo_backtest(features, config)
    tags_label = "tags" + "-".join((allowed_tags or ("all",))).replace(" ", "_")
    hours_label = "hours" + ("-".join(str(hour) for hour in allowed_hours) if allowed_hours else "all")
    strategy_name = f"builtin_{args.strategy}_{args.freqtrade_preset}_{args.freqtrade_side}_vol{args.volume:g}_risk{args.risk_usd:g}_reward{args.reward_usd:g}_{args.session}_{tags_label}_{hours_label}_fastx{int(args.use_fastk_exit)}"
    validation = _basic_builtin_validation(result.metrics, result.signal_counts, args.min_trades, args.max_drawdown)
    return _persist_builtin_result(args, strategy_name, config.__dict__, result, validation)


def _run_builtin_freqtrade_support(args: argparse.Namespace) -> dict:
    features = _load_feature_frame(args.dataset_id, args.feature_set_id)
    allowed_tags = tuple(tag.strip() for tag in args.freqtrade_allowed_tags.split(",") if tag.strip())
    if allowed_tags == ("ewo", "buy_1", "ewo buy_1"):
        allowed_tags = ()
    allowed_hours = tuple(int(hour.strip()) for hour in args.freqtrade_allowed_hours.split(",") if hour.strip())
    config = freqtrade_support_config_for_preset(
        args.freqtrade_preset,
        initial_equity=args.initial_equity,
        volume_lots=args.volume,
        price_value_per_lot=args.price_value_per_lot,
        risk_usd=args.risk_usd,
        reward_usd=args.reward_usd,
        synthetic_spread=args.synthetic_spread,
        slippage_per_side=args.slippage_per_side,
        max_spread=args.max_spread,
        session_filter=args.session,
        trade_side=args.freqtrade_side,
        allowed_signal_tags=allowed_tags,
        allowed_entry_hours=allowed_hours,
    )
    result = run_freqtrade_support_backtest(features, config)
    tags_label = "tags" + "-".join((allowed_tags or ("all",))).replace(" ", "_")
    hours_label = "hours" + ("-".join(str(hour) for hour in allowed_hours) if allowed_hours else "all")
    strategy_name = f"builtin_{args.strategy}_{args.freqtrade_preset}_{args.freqtrade_side}_vol{args.volume:g}_risk{args.risk_usd:g}_reward{args.reward_usd:g}_{args.session}_{tags_label}_{hours_label}"
    validation = _basic_builtin_validation(result.metrics, result.signal_counts, args.min_trades, args.max_drawdown)
    return _persist_builtin_result(args, strategy_name, config.__dict__, result, validation)


def _run_builtin_gold_fusion(args: argparse.Namespace) -> dict:
    features = _load_feature_frame(args.dataset_id, args.feature_set_id)
    config = GoldFusionConfig(
        initial_equity=args.initial_equity,
        volume_lots=args.volume,
        price_value_per_lot=args.price_value_per_lot,
        risk_usd=args.risk_usd,
        reward_usd=args.reward_usd,
        synthetic_spread=args.synthetic_spread,
        slippage_per_side=args.slippage_per_side,
        max_spread=args.max_spread,
    )
    result = run_gold_fusion_backtest(features, config)
    strategy_name = f"builtin_{args.strategy}_clucHA_nfi44_vol{args.volume:g}_risk{args.risk_usd:g}_reward{args.reward_usd:g}"
    validation = _basic_builtin_validation(result.metrics, result.signal_counts, args.min_trades, args.max_drawdown)
    return _persist_builtin_result(args, strategy_name, config.__dict__, result, validation)


def _run_builtin_e0v1e(args: argparse.Namespace) -> dict:
    features = _load_feature_frame(args.dataset_id, args.feature_set_id)
    allowed_hours = tuple(int(hour.strip()) for hour in args.freqtrade_allowed_hours.split(",") if hour.strip())
    config = e0v1e_config_for_preset(
        args.freqtrade_preset,
        initial_equity=args.initial_equity,
        volume_lots=args.volume,
        price_value_per_lot=args.price_value_per_lot,
        risk_usd=args.risk_usd,
        reward_usd=args.reward_usd,
        synthetic_spread=args.synthetic_spread,
        slippage_per_side=args.slippage_per_side,
        max_spread=args.max_spread,
        session_filter=args.session,
        trade_side=args.freqtrade_side,
        allowed_entry_hours=allowed_hours,
    )
    result = run_e0v1e_backtest(features, config)
    hours_label = "hours" + ("-".join(str(hour) for hour in allowed_hours) if allowed_hours else "all")
    strategy_name = f"builtin_{args.strategy}_{args.freqtrade_preset}_{args.freqtrade_side}_vol{args.volume:g}_risk{args.risk_usd:g}_reward{args.reward_usd:g}_{args.session}_{hours_label}"
    validation = _basic_builtin_validation(result.metrics, result.signal_counts, args.min_trades, args.max_drawdown)
    return _persist_builtin_result(args, strategy_name, config.__dict__, result, validation)


def _run_builtin_best5m(args: argparse.Namespace) -> dict:
    features = _load_feature_frame(args.dataset_id, args.feature_set_id)
    allowed_hours = tuple(int(hour.strip()) for hour in args.freqtrade_allowed_hours.split(",") if hour.strip())
    config = best5m_config_for_preset(
        args.freqtrade_preset,
        initial_equity=args.initial_equity,
        volume_lots=args.volume,
        price_value_per_lot=args.price_value_per_lot,
        risk_usd=args.risk_usd,
        reward_usd=args.reward_usd,
        synthetic_spread=args.synthetic_spread,
        slippage_per_side=args.slippage_per_side,
        max_spread=args.max_spread,
        session_filter=args.session,
        trade_side=args.side,
        allowed_entry_hours=allowed_hours,
    )
    result = run_best5m_backtest(features, config)
    hours_label = "hours" + ("-".join(str(hour) for hour in allowed_hours) if allowed_hours else "all")
    strategy_name = f"builtin_{args.strategy}_{args.freqtrade_preset}_{args.side}_vol{args.volume:g}_risk{args.risk_usd:g}_reward{args.reward_usd:g}_{args.session}_{hours_label}"
    validation = _basic_builtin_validation(result.metrics, result.signal_counts, args.min_trades, args.max_drawdown)
    return _persist_builtin_result(args, strategy_name, config.__dict__, result, validation)


def _run_builtin_smart_liquidity(args: argparse.Namespace) -> dict:
    features = _load_feature_frame(args.dataset_id, args.feature_set_id)
    allowed_hours = tuple(int(hour.strip()) for hour in args.freqtrade_allowed_hours.split(",") if hour.strip())
    config = smart_liquidity_config_for_preset(
        args.freqtrade_preset,
        initial_equity=args.initial_equity,
        volume_lots=args.volume,
        price_value_per_lot=args.price_value_per_lot,
        risk_usd=args.risk_usd,
        reward_usd=args.reward_usd,
        synthetic_spread=args.synthetic_spread,
        slippage_per_side=args.slippage_per_side,
        max_spread=args.max_spread,
        session_filter=args.session,
        trade_side=args.side,
        allowed_entry_hours=allowed_hours,
    )
    result = run_smart_liquidity_backtest(features, config)
    hours_label = "hours" + ("-".join(str(hour) for hour in allowed_hours) if allowed_hours else "all")
    strategy_name = f"builtin_{args.strategy}_{args.freqtrade_preset}_{args.side}_vol{args.volume:g}_risk{args.risk_usd:g}_reward{args.reward_usd:g}_{args.session}_{hours_label}"
    validation = _basic_builtin_validation(result.metrics, result.signal_counts, args.min_trades, args.max_drawdown)
    return _persist_builtin_result(args, strategy_name, config.__dict__, result, validation)


def _run_builtin_ichi_v1(args: argparse.Namespace) -> dict:
    features = _load_feature_frame(args.dataset_id, args.feature_set_id)
    allowed_hours = tuple(int(hour.strip()) for hour in args.freqtrade_allowed_hours.split(",") if hour.strip())
    config = ichi_v1_config_for_preset(
        args.freqtrade_preset,
        initial_equity=args.initial_equity,
        volume_lots=args.volume,
        price_value_per_lot=args.price_value_per_lot,
        risk_usd=args.risk_usd,
        reward_usd=args.reward_usd,
        synthetic_spread=args.synthetic_spread,
        slippage_per_side=args.slippage_per_side,
        max_spread=args.max_spread,
        session_filter=args.session,
        allowed_entry_hours=allowed_hours,
    )
    result = run_ichi_v1_backtest(features, config)
    hours_label = "hours" + ("-".join(str(hour) for hour in allowed_hours) if allowed_hours else "all")
    strategy_name = f"builtin_{args.strategy}_{args.freqtrade_preset}_long_vol{args.volume:g}_risk{args.risk_usd:g}_reward{args.reward_usd:g}_{args.session}_{hours_label}"
    validation = _basic_builtin_validation(result.metrics, result.signal_counts, args.min_trades, args.max_drawdown)
    return _persist_builtin_result(args, strategy_name, config.__dict__, result, validation)


def _run_builtin_rsiqui_v3(args: argparse.Namespace) -> dict:
    features = _load_feature_frame(args.dataset_id, args.feature_set_id)
    allowed_hours = tuple(int(hour.strip()) for hour in args.freqtrade_allowed_hours.split(",") if hour.strip())
    config = rsiqui_v3_config_for_preset(
        args.freqtrade_preset,
        initial_equity=args.initial_equity,
        volume_lots=args.volume,
        price_value_per_lot=args.price_value_per_lot,
        risk_usd=args.risk_usd,
        reward_usd=args.reward_usd,
        synthetic_spread=args.synthetic_spread,
        spread_multiplier=args.spread_multiplier,
        slippage_per_side=args.slippage_per_side,
        commission_per_trade_usd=args.commission_per_trade_usd,
        max_spread=args.max_spread,
        session_filter=args.session,
        trade_side=args.side,
        allowed_entry_hours=allowed_hours,
    )
    result = run_rsiqui_v3_backtest(features, config)
    hours_label = "hours" + ("-".join(str(hour) for hour in allowed_hours) if allowed_hours else "all")
    strategy_name = f"builtin_{args.strategy}_{args.freqtrade_preset}_{args.side}_vol{args.volume:g}_risk{args.risk_usd:g}_reward{args.reward_usd:g}_{args.session}_{hours_label}_spreadx{args.spread_multiplier:g}_slip{args.slippage_per_side:g}_comm{args.commission_per_trade_usd:g}"
    validation = _basic_builtin_validation(result.metrics, result.signal_counts, args.min_trades, args.max_drawdown)
    return _persist_builtin_result(args, strategy_name, config.__dict__, result, validation)


def cmd_research(args: argparse.Namespace) -> None:
    if args.input:
        raw_path = Path(args.input)
        fetch_result = None
    else:
        raw = fetch_yahoo_xauusd(period=args.period, interval=args.interval, ticker=args.ticker)
        raw_path = write_raw_yahoo_xauusd(raw, period=args.period, interval=args.interval, ticker=args.ticker)
        fetch_result = {"raw_path": str(raw_path), "provider": "yahoo", "ticker": args.ticker, "quality": inspect_candles(raw).as_dict()}
    prepared = _prepare_dataset(raw_path, args.symbol, args.timeframe)
    dataset_id = prepared["metadata"]["dataset_id"]
    features = _build_features(dataset_id)
    batch = _execute_batch(
        argparse.Namespace(
            dataset_id=dataset_id,
            feature_set_id=features["feature_set_id"],
            output_spec=None,
            skill=args.skill,
            count=args.count,
            config=args.config,
        )
    )
    print(
        json.dumps(
            {"fetch": fetch_result, "prepared": prepared, "features": features, "batch": batch},
            indent=2,
            default=str,
            allow_nan=False,
        )
    )


def cmd_auto_research(args: argparse.Namespace) -> None:
    results: list[dict] = []
    for skill_name in args.skills:
        results.append(
            {
                "type": "skill_batch",
                "skill": skill_name,
                "result": _execute_batch(
                    argparse.Namespace(
                        dataset_id=args.dataset_id,
                        feature_set_id=args.feature_set_id,
                        output_spec=None,
                        skill=skill_name,
                        count=args.count_per_skill,
                        config=args.config,
                    )
                ),
            }
        )
    if args.include_m15_structure:
        for side in ["long", "both"]:
            results.append(
                {
                    "type": "builtin",
                    "strategy": "m15-structure",
                    "side": side,
                    "result": _run_builtin_m15_structure(
                        argparse.Namespace(
                            strategy="m15-structure",
                            dataset_id=args.dataset_id,
                            feature_set_id=args.feature_set_id,
                            risk_usd=args.risk_usd,
                            reward_risk=args.reward_risk,
                            synthetic_spread=args.synthetic_spread,
                            slippage_per_side=args.slippage_per_side,
                            max_spread=args.max_spread,
                            side=side,
                            session="all",
                            min_trades=args.min_trades,
                            max_drawdown=args.max_drawdown,
                        )
                    ),
                }
            )
    if args.include_wick_fill:
        wick_variants = [
            {"min_body_atr": 0.35, "min_upper_wick_body_ratio": 1.0, "min_upper_wick_atr": 0.35, "confirmation_delay_bars": 2},
            {"min_body_atr": 0.2, "min_upper_wick_body_ratio": 0.7, "min_upper_wick_atr": 0.25, "confirmation_delay_bars": 2},
            {"min_body_atr": 0.2, "min_upper_wick_body_ratio": 0.7, "min_upper_wick_atr": 0.25, "confirmation_delay_bars": 1},
        ]
        for variant in wick_variants:
            results.append(
                {
                    "type": "builtin",
                    "strategy": "h1-wick-fill",
                    "variant": variant,
                    "result": _run_builtin_h1_wick_fill(
                        argparse.Namespace(
                            strategy="h1-wick-fill",
                            dataset_id=args.dataset_id,
                            feature_set_id=args.feature_set_id,
                            risk_usd=args.risk_usd,
                            reward_risk=args.reward_risk,
                            synthetic_spread=args.synthetic_spread,
                            slippage_per_side=args.slippage_per_side,
                            max_spread=args.max_spread,
                            session="all",
                            min_body_atr=variant["min_body_atr"],
                            min_upper_wick_body_ratio=variant["min_upper_wick_body_ratio"],
                            min_upper_wick_atr=variant["min_upper_wick_atr"],
                            confirmation_delay_bars=variant["confirmation_delay_bars"],
                            max_entry_distance_atr=1.25,
                            min_target_distance=0.5,
                            max_target_distance=20.0,
                            min_trades=args.min_trades,
                            max_drawdown=args.max_drawdown,
                        )
                    ),
                }
            )
    leaderboard = build_leaderboard(dataset_id=args.dataset_id, feature_set_id=args.feature_set_id)
    leaderboard_path = persist_leaderboard(leaderboard)
    summary = summarize_research_cycle(args.dataset_id, args.feature_set_id, leaderboard)
    note = record_research_cycle(HermesMemoryStore(), summary)
    print(
        json.dumps(
            {
                "results": results,
                "leaderboard_path": str(leaderboard_path),
                "summary": summary.as_dict(),
                "memory_note": note.model_dump(),
            },
            indent=2,
            default=str,
            allow_nan=False,
        )
    )


def cmd_report(args: argparse.Namespace) -> None:
    if args.leaderboard:
        leaderboard = build_leaderboard(dataset_id=args.dataset_id, feature_set_id=args.feature_set_id)
        leaderboard_path = persist_leaderboard(leaderboard)
        print(json.dumps({"leaderboard_path": str(leaderboard_path), "leaderboard": leaderboard}, indent=2, allow_nan=False))
        return
    report = json.loads(Path(args.report).read_text(encoding="utf-8"))
    print(json.dumps(report, indent=2, allow_nan=False))


def cmd_hermes_skills(_: argparse.Namespace) -> None:
    payload = [
        {
            "name": profile.name,
            "description": profile.description,
            "preferred_features": list(profile.preferred_features),
        }
        for profile in list_skill_profiles()
    ]
    print(json.dumps(payload, indent=2))


def cmd_hermes_memory(args: argparse.Namespace) -> None:
    memory = HermesMemoryStore()
    payload = {
        "strategies": [record.model_dump() for record in memory.list_strategies(limit=args.limit)],
        "best_strategies": [record.model_dump() for record in memory.best_strategies(limit=min(args.limit, 5))],
        "notes": [note.model_dump() for note in memory.list_notes(limit=args.limit)],
    }
    print(json.dumps(payload, indent=2, default=str))


def cmd_hermes_note(args: argparse.Namespace) -> None:
    memory = HermesMemoryStore()
    note = memory.append_note(topic=args.topic, content=args.content, tags=args.tags or [])
    print(json.dumps(note.model_dump(), indent=2, default=str))


def cmd_hermes_knowledge(args: argparse.Namespace) -> None:
    payload: dict = {"policy": describe_policy()}
    if args.spec:
        spec = load_strategy_spec(Path(args.spec))
        payload["strategy_assessment"] = assess_strategy_against_policy(
            spec,
            {
                "provider": args.provider,
                "ticker": args.ticker,
                "missing_spread_fraction": args.missing_spread_fraction,
            },
        )
    print(json.dumps(payload, indent=2, default=str, allow_nan=False))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="trading-lab")
    subparsers = parser.add_subparsers(dest="command", required=True)

    fetch = subparsers.add_parser("fetch-data")
    fetch.add_argument("--provider", choices=["yahoo"], default="yahoo")
    fetch.add_argument("--ticker", default="GC=F")
    fetch.add_argument("--period", default="730d")
    fetch.add_argument("--interval", default="1h")
    fetch.set_defaults(func=cmd_fetch_data)

    inspect = subparsers.add_parser("inspect-data")
    inspect.add_argument("--input", required=True)
    inspect.add_argument("--min-rows", type=int, default=2_000)
    inspect.set_defaults(func=cmd_inspect_data)

    prepare = subparsers.add_parser("prepare-data")
    prepare.add_argument("--input", required=True)
    prepare.add_argument("--symbol", default="XAUUSD")
    prepare.add_argument("--timeframe", default="1h")
    prepare.set_defaults(func=cmd_prepare_data)

    build = subparsers.add_parser("build-features")
    build.add_argument("--dataset-id", required=True)
    build.set_defaults(func=cmd_build_features)

    generate = subparsers.add_parser("generate-strategy")
    generate.add_argument("--skill", choices=[profile.name for profile in list_skill_profiles()])
    generate.add_argument("--adapter", choices=["mock", "llm"], default="mock")
    generate.add_argument("--remember", action="store_true")
    generate.set_defaults(func=cmd_generate_strategy)

    validate = subparsers.add_parser("validate-spec")
    validate.add_argument("--spec", required=True)
    validate.set_defaults(func=cmd_validate_spec)

    backtest = subparsers.add_parser("run-backtest")
    backtest.add_argument("--spec", required=True)
    backtest.add_argument("--dataset-id", required=True)
    backtest.add_argument("--feature-set-id", required=True)
    backtest.add_argument("--config")
    backtest.set_defaults(func=cmd_run_backtest)

    fixed_lot = subparsers.add_parser("run-fixed-lot")
    fixed_lot.add_argument("--spec", required=True)
    fixed_lot.add_argument("--dataset-id", required=True)
    fixed_lot.add_argument("--feature-set-id", required=True)
    fixed_lot.add_argument("--initial-equity", type=float, default=10_000.0)
    fixed_lot.add_argument("--volume", type=float, default=0.01)
    fixed_lot.add_argument("--price-value-per-lot", type=float, default=100.0)
    fixed_lot.add_argument("--risk-usd", type=float, default=10.0)
    fixed_lot.add_argument("--reward-usd", type=float, default=10.0)
    fixed_lot.add_argument("--synthetic-spread", type=float, default=0.5)
    fixed_lot.add_argument("--slippage-per-side", type=float, default=0.05)
    fixed_lot.add_argument("--max-spread", type=float, default=0.6)
    fixed_lot.add_argument("--time-exit-bars", type=int)
    fixed_lot.add_argument("--min-trades", type=int, default=50)
    fixed_lot.add_argument("--max-drawdown", type=float, default=0.2)
    fixed_lot.add_argument("--oos-fraction", type=float, default=0.3)
    fixed_lot.add_argument("--walk-forward-train-fraction", type=float, default=0.5)
    fixed_lot.add_argument("--walk-forward-test-fraction", type=float, default=0.1)
    fixed_lot.add_argument("--min-walk-forward-windows", type=int, default=3)
    fixed_lot.add_argument("--min-profitable-windows-fraction", type=float, default=0.6)
    fixed_lot.add_argument("--spread-stress-multiplier", type=float, default=1.5)
    fixed_lot.add_argument("--perturbation-reward-multiplier", type=float, default=0.9)
    fixed_lot.add_argument("--provider", default="unknown")
    fixed_lot.add_argument("--ticker", default="unknown")
    fixed_lot.add_argument("--missing-spread-fraction", type=float, default=0.0)
    fixed_lot.set_defaults(func=cmd_run_fixed_lot)

    batch = subparsers.add_parser("run-batch")
    batch.add_argument("--dataset-id", required=True)
    batch.add_argument("--feature-set-id", required=True)
    batch.add_argument("--output-spec")
    batch.add_argument("--skill", choices=[profile.name for profile in list_skill_profiles()])
    batch.add_argument("--count", type=int, default=3)
    batch.add_argument("--config")
    batch.set_defaults(func=cmd_run_batch)

    builtin = subparsers.add_parser("run-builtin")
    builtin.add_argument("--strategy", choices=["m15-structure", "h1-wick-fill", "freqtrade-ewo", "freqtrade-support", "gold-fusion", "e0v1e", "best5m", "smart-liquidity", "ichi-v1", "rsiqui-v3"], required=True)
    builtin.add_argument("--dataset-id", required=True)
    builtin.add_argument("--feature-set-id", required=True)
    builtin.add_argument("--risk-usd", type=float, default=10.0)
    builtin.add_argument("--reward-usd", type=float, default=15.0)
    builtin.add_argument("--reward-risk", type=float, default=1.0)
    builtin.add_argument("--initial-equity", type=float, default=10_000.0)
    builtin.add_argument("--volume", type=float, default=0.01)
    builtin.add_argument("--price-value-per-lot", type=float, default=100.0)
    builtin.add_argument("--synthetic-spread", type=float, default=0.5)
    builtin.add_argument("--spread-multiplier", type=float, default=1.0)
    builtin.add_argument("--slippage-per-side", type=float, default=0.05)
    builtin.add_argument("--commission-per-trade-usd", type=float, default=0.0)
    builtin.add_argument("--max-spread", type=float, default=0.6)
    builtin.add_argument("--side", choices=["both", "long", "short"], default="both")
    builtin.add_argument("--session", choices=["all", "asia", "london", "new_york"], default="all")
    builtin.add_argument("--min-body-atr", type=float, default=0.35)
    builtin.add_argument("--min-upper-wick-body-ratio", type=float, default=1.0)
    builtin.add_argument("--min-upper-wick-atr", type=float, default=0.35)
    builtin.add_argument("--confirmation-delay-bars", type=int, default=2)
    builtin.add_argument("--max-entry-distance-atr", type=float, default=1.25)
    builtin.add_argument("--min-target-distance", type=float, default=1.0)
    builtin.add_argument("--max-target-distance", type=float, default=12.0)
    builtin.add_argument("--min-trades", type=int, default=50)
    builtin.add_argument("--max-drawdown", type=float, default=0.2)
    builtin.add_argument("--use-fastk-exit", action="store_true")
    builtin.add_argument("--freqtrade-preset", choices=["original", "gold-balanced", "gold-loose", "gene-gold"], default="original")
    builtin.add_argument("--freqtrade-side", choices=["long", "short"], default="long")
    builtin.add_argument("--freqtrade-allowed-tags", default="ewo,buy_1,ewo buy_1")
    builtin.add_argument("--freqtrade-allowed-hours", default="")
    builtin.set_defaults(func=cmd_run_builtin)

    research = subparsers.add_parser("research")
    research.add_argument("--input")
    research.add_argument("--provider", choices=["yahoo"], default="yahoo")
    research.add_argument("--ticker", default="GC=F")
    research.add_argument("--period", default="730d")
    research.add_argument("--interval", default="1h")
    research.add_argument("--symbol", default="XAUUSD")
    research.add_argument("--timeframe", default="1h")
    research.add_argument("--skill", choices=[profile.name for profile in list_skill_profiles()])
    research.add_argument("--count", type=int, default=10)
    research.add_argument("--config")
    research.set_defaults(func=cmd_research)

    auto_research = subparsers.add_parser("auto-research")
    auto_research.add_argument("--dataset-id", required=True)
    auto_research.add_argument("--feature-set-id", required=True)
    auto_research.add_argument("--config")
    auto_research.add_argument("--skills", nargs="+", choices=[profile.name for profile in list_skill_profiles()], default=["intraday_momentum", "trend_pullback", "volatility_reversion"])
    auto_research.add_argument("--count-per-skill", type=int, default=2)
    auto_research.add_argument("--include-m15-structure", action="store_true")
    auto_research.add_argument("--include-wick-fill", action="store_true")
    auto_research.add_argument("--risk-usd", type=float, default=15.0)
    auto_research.add_argument("--reward-risk", type=float, default=1.0)
    auto_research.add_argument("--synthetic-spread", type=float, default=0.5)
    auto_research.add_argument("--slippage-per-side", type=float, default=0.05)
    auto_research.add_argument("--max-spread", type=float, default=0.6)
    auto_research.add_argument("--min-trades", type=int, default=50)
    auto_research.add_argument("--max-drawdown", type=float, default=0.2)
    auto_research.set_defaults(func=cmd_auto_research)

    report = subparsers.add_parser("report")
    report.add_argument("--report")
    report.add_argument("--leaderboard", action="store_true")
    report.add_argument("--dataset-id")
    report.add_argument("--feature-set-id")
    report.set_defaults(func=cmd_report)

    skills = subparsers.add_parser("hermes-skills")
    skills.set_defaults(func=cmd_hermes_skills)

    memory = subparsers.add_parser("hermes-memory")
    memory.add_argument("--limit", type=int, default=20)
    memory.set_defaults(func=cmd_hermes_memory)

    note = subparsers.add_parser("hermes-note")
    note.add_argument("--topic", required=True)
    note.add_argument("--content", required=True)
    note.add_argument("--tags", nargs="*")
    note.set_defaults(func=cmd_hermes_note)

    knowledge = subparsers.add_parser("hermes-knowledge")
    knowledge.add_argument("--spec")
    knowledge.add_argument("--provider")
    knowledge.add_argument("--ticker")
    knowledge.add_argument("--missing-spread-fraction", type=float, default=0.0)
    knowledge.set_defaults(func=cmd_hermes_knowledge)

    return parser


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()
    args.func(args)
