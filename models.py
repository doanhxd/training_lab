from __future__ import annotations

from datetime import UTC, datetime
from typing import Literal

from pydantic import BaseModel, Field, field_validator, model_validator


class Candle(BaseModel):
    timestamp: datetime
    open: float
    high: float
    low: float
    close: float
    volume: float | None = None
    spread: float | None = None
    symbol: str = "XAUUSD"

    @field_validator("timestamp")
    @classmethod
    def normalize_timestamp(cls, value: datetime) -> datetime:
        if value.tzinfo is None:
            return value.replace(tzinfo=UTC)
        return value.astimezone(UTC)

    @model_validator(mode="after")
    def validate_ohlc(self) -> "Candle":
        if self.high < max(self.open, self.close) or self.low > min(self.open, self.close):
            raise ValueError("OHLC values are inconsistent")
        if self.low > self.high:
            raise ValueError("low cannot exceed high")
        return self


class DatasetMetadata(BaseModel):
    dataset_id: str
    symbol: str = "XAUUSD"
    timeframe: str
    row_count: int
    source_path: str
    processed_path: str
    created_at: datetime
    timezone: str = "UTC"


class FeatureSetMetadata(BaseModel):
    dataset_id: str
    feature_set_id: str
    timeframe: str
    feature_version: str
    row_count: int
    created_at: datetime
    feature_path: str


class StrategySideSpec(BaseModel):
    long: str | None = None
    short: str | None = None


class StrategyExitSpec(BaseModel):
    stop_loss: str
    take_profit: str | None = None
    time_exit: str | None = None


class StrategyRiskSpec(BaseModel):
    position_size: str


class StrategySpec(BaseModel):
    market: Literal["XAUUSD"]
    timeframe: str
    strategy_name: str
    thesis: str
    entry: StrategySideSpec
    exit: StrategyExitSpec
    filters: list[str] = Field(default_factory=list)
    risk: StrategyRiskSpec
    assumptions: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_sides(self) -> "StrategySpec":
        if not self.entry.long and not self.entry.short:
            raise ValueError("at least one entry side is required")
        return self


class BacktestConfig(BaseModel):
    initial_equity: float = 10_000.0
    synthetic_spread: float = 0.3
    slippage_per_side: float = 0.0
    fill_policy: Literal["next_bar_open"] = "next_bar_open"
    risk_fraction: float = 0.005


class ValidationConfig(BaseModel):
    min_trades: int = 30
    max_drawdown: float = 0.25
    oos_fraction: float = 0.3
    walk_forward_train_fraction: float = 0.5
    walk_forward_test_fraction: float = 0.1
    min_walk_forward_windows: int = 3
    min_profitable_windows_fraction: float = 0.6
    spread_stress_multiplier: float = 1.5
    perturbation_take_profit_multiplier: float = 0.9


class RunConfig(BaseModel):
    backtest: BacktestConfig = Field(default_factory=BacktestConfig)
    validation: ValidationConfig = Field(default_factory=ValidationConfig)


class TradeRecord(BaseModel):
    side: Literal["long", "short"]
    entry_time: datetime
    exit_time: datetime
    entry_price: float
    exit_price: float
    quantity: float
    pnl: float
    bars_held: int


class BacktestMetrics(BaseModel):
    net_return: float
    max_drawdown: float
    sharpe_ratio: float
    sortino_ratio: float
    profit_factor: float | None
    win_rate: float
    expectancy: float
    total_trades: int
    average_holding_period: float
    exposure_time: float


class ValidationSummary(BaseModel):
    passed: bool
    reasons: list[str] = Field(default_factory=list)
    checks: dict[str, float | bool | str | None] = Field(default_factory=dict)
