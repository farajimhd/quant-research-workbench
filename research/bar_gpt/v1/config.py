from __future__ import annotations

from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from research.bar_gpt.v1.cohort import BAR_GPT_COHORT_2TB_TABLE
from research.bar_gpt.v1.features import MODEL_FEATURE_NAMES
from research.bar_gpt.v1.targets import TARGET_NAMES


INTRADAY_TIMEFRAMES_US: tuple[int, ...] = (
    1_000_000,
    5_000_000,
    30_000_000,
    60_000_000,
    300_000_000,
    900_000_000,
    3_600_000_000,
)
CALENDAR_TIMEFRAMES: tuple[str, ...] = ("1D", "1W", "1MO")
DEFAULT_HORIZONS_US: tuple[int, ...] = (
    5_000_000,
    30_000_000,
    60_000_000,
    300_000_000,
    900_000_000,
    3_600_000_000,
)

@dataclass(slots=True)
class BarGPTConfig:
    feature_dim: int = len(MODEL_FEATURE_NAMES)
    target_dim: int = len(TARGET_NAMES)
    d_model: int = 512
    n_layers: int = 10
    n_heads: int = 8
    n_kv_heads: int = 4
    ff_multiplier: float = 8.0 / 3.0
    dropout: float = 0.0
    rope_base: float = 10_000.0
    max_timeframes: int = 16
    max_horizons: int = 32
    horizon_rank: int = 96
    quantiles: tuple[float, ...] = (0.1, 0.5, 0.9)

    def validate(self) -> None:
        if self.d_model % self.n_heads:
            raise ValueError("d_model must be divisible by n_heads")
        if self.n_heads % self.n_kv_heads:
            raise ValueError("n_heads must be divisible by n_kv_heads")
        if (self.d_model // self.n_heads) % 2:
            raise ValueError("attention head dimension must be even for RoPE")
        if not self.quantiles:
            raise ValueError("at least one quantile is required")


@dataclass(slots=True)
class DataConfig:
    database: str = "market_sip_compact"
    one_second_table: str = BAR_GPT_COHORT_2TB_TABLE
    daily_table: str = "macro_bars_by_time_symbol"
    base_timeframe_us: int = 1_000_000
    intraday_timeframes_us: tuple[int, ...] = INTRADAY_TIMEFRAMES_US
    calendar_timeframes: tuple[str, ...] = CALENDAR_TIMEFRAMES
    horizons_us: tuple[int, ...] = DEFAULT_HORIZONS_US
    context_bars_1s: int = 8_192
    origin_bars_1s: int = 4_096
    maximum_target_horizon_us: int = 3_600_000_000
    loader_workers: int = 8
    ready_queue_blocks: int = 4

    @property
    def right_support_bars_1s(self) -> int:
        return (self.maximum_target_horizon_us + self.base_timeframe_us - 1) // self.base_timeframe_us


@dataclass(slots=True)
class TrainConfig:
    output_root: Path = Path(r"D:\TradingML\runtimes\bar_gpt\v1\train")
    run_name: str = "bar-gpt-v1"
    epochs: int = 1
    learning_rate: float = 3e-4
    weight_decay: float = 0.1
    grad_clip_norm: float = 1.0
    amp_dtype: str = "bf16"
    compile_model: bool = True
    seed: int = 17
    wandb_project: str = "bar-gpt-v1"


def to_dict(config: Any) -> dict[str, Any]:
    if hasattr(config, "__dataclass_fields__"):
        return asdict(config)
    return dict(config)
