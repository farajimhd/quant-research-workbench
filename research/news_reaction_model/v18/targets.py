from __future__ import annotations

from dataclasses import asdict, dataclass
from enum import IntEnum
from typing import Any, Mapping, Sequence

import numpy as np

from research.news_reaction_model.v18 import TARGET_VERSION


RAW_METRIC_NAMES = (
    "anchor_price",
    "high_return",
    "low_return",
    "terminal_return",
    "high_time_fraction",
    "low_time_fraction",
    "vwap_return",
    "peak_to_trough_return",
    "trough_to_peak_return",
    "buy_notional_share",
    "sell_notional_share",
    "unknown_notional_share",
    "observation_count",
    "duration_seconds",
)
REGRESSION_NAMES = ("high_return_pct", "low_return_pct", "terminal_return_pct")


class Direction(IntEnum):
    NEUTRAL = 0
    UPSIDE = 1
    DOWNSIDE = 2


class Path(IntEnum):
    NO_MOVE = 0
    SUSTAINED = 1
    SPIKE_FADE = 2
    FLUSH_RECOVERY = 3
    REVERSAL = 4
    VOLATILE_MIXED = 5


class Flow(IntEnum):
    BALANCED = 0
    DEMAND_DOMINANT = 1
    SUPPLY_DOMINANT = 2


DIRECTION_NAMES = tuple(value.name.lower() for value in Direction)
PATH_NAMES = tuple(value.name.lower() for value in Path)
FLOW_NAMES = tuple(value.name.lower() for value in Flow)


@dataclass(frozen=True, slots=True)
class TargetThresholds:
    meaningful_return: float
    retained_move_ratio: float = 0.60
    fade_ratio: float = 0.35
    flow_imbalance: float = 0.12
    minimum_observations: int = 3

    def as_dict(self) -> dict[str, Any]:
        return {**asdict(self), "target_version": TARGET_VERSION}

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "TargetThresholds":
        return cls(
            meaningful_return=float(value["meaningful_return"]),
            retained_move_ratio=float(value["retained_move_ratio"]),
            fade_ratio=float(value["fade_ratio"]),
            flow_imbalance=float(value["flow_imbalance"]),
            minimum_observations=int(value["minimum_observations"]),
        )


def fit_thresholds(
    raw_metrics: np.ndarray,
    mask: np.ndarray,
    *,
    quantile: float = 0.35,
    floor: float = 0.001,
) -> TargetThresholds:
    values = np.asarray(raw_metrics, dtype=np.float64)
    valid = np.asarray(mask, dtype=np.bool_)
    if values.ndim != 2 or values.shape[1] != len(RAW_METRIC_NAMES):
        raise ValueError(f"Unexpected raw metric shape {values.shape}.")
    magnitude = np.maximum(
        np.abs(values[:, RAW_METRIC_NAMES.index("high_return")]),
        np.abs(values[:, RAW_METRIC_NAMES.index("low_return")]),
    )
    selected = magnitude[valid & np.isfinite(magnitude)]
    if not selected.size:
        raise ValueError("No training episode targets are available for threshold fitting.")
    return TargetThresholds(max(float(np.quantile(selected, quantile)), floor))


def classify(
    metrics: Sequence[float],
    contract: TargetThresholds,
) -> tuple[Direction, Path, Flow, np.ndarray]:
    values = dict(zip(RAW_METRIC_NAMES, (float(value) for value in metrics), strict=True))
    high = max(values["high_return"], 0.0)
    low = min(values["low_return"], 0.0)
    terminal = values["terminal_return"]
    up = high
    down = abs(low)
    threshold = contract.meaningful_return
    if up < threshold and down < threshold:
        direction = Direction.NEUTRAL
    elif up > down:
        direction = Direction.UPSIDE
    elif down > up:
        direction = Direction.DOWNSIDE
    elif terminal > 0:
        direction = Direction.UPSIDE
    elif terminal < 0:
        direction = Direction.DOWNSIDE
    else:
        direction = (
            Direction.UPSIDE
            if values["high_time_fraction"] > values["low_time_fraction"]
            else Direction.DOWNSIDE
        )
    high_first = values["high_time_fraction"] <= values["low_time_fraction"]
    if direction is Direction.NEUTRAL:
        path = Path.NO_MOVE
    elif terminal > threshold and low < -threshold and not high_first:
        path = Path.REVERSAL
    elif terminal < -threshold and high > threshold and high_first:
        path = Path.REVERSAL
    elif direction is Direction.UPSIDE and terminal >= contract.retained_move_ratio * max(up, threshold):
        path = Path.SUSTAINED
    elif direction is Direction.DOWNSIDE and abs(terminal) >= contract.retained_move_ratio * max(down, threshold):
        path = Path.SUSTAINED
    elif high_first and up >= threshold and terminal <= contract.fade_ratio * up:
        path = Path.SPIKE_FADE
    elif not high_first and down >= threshold and terminal >= -contract.fade_ratio * down:
        path = Path.FLUSH_RECOVERY
    else:
        path = Path.VOLATILE_MIXED
    imbalance = values["buy_notional_share"] - values["sell_notional_share"]
    if imbalance >= contract.flow_imbalance:
        flow = Flow.DEMAND_DOMINANT
    elif imbalance <= -contract.flow_imbalance:
        flow = Flow.SUPPLY_DOMINANT
    else:
        flow = Flow.BALANCED
    regression = np.asarray(
        [100.0 * values["high_return"], 100.0 * values["low_return"], 100.0 * terminal],
        dtype=np.float32,
    )
    return direction, path, flow, regression
