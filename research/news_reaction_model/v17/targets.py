from __future__ import annotations

from dataclasses import asdict, dataclass
from enum import IntEnum
from typing import Any, Mapping, Sequence

import numpy as np

from research.news_reaction_model.v17 import RESPONSE_WINDOWS


TARGET_VERSION = "news_market_response_targets_v17_direction3_v2"
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
    "relative_volume",
    "market_adjusted_terminal_return",
    "observation_count",
    "duration_seconds",
)


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


class Persistence(IntEnum):
    NO_RESPONSE = 0
    EVENT_PHASE_ONLY = 1
    NEXT_SESSION = 2
    MULTI_SESSION = 3
    REVERSAL = 4
    DELAYED = 5


DIRECTION_NAMES = tuple(value.name.lower() for value in Direction)
PATH_NAMES = tuple(value.name.lower() for value in Path)
FLOW_NAMES = tuple(value.name.lower() for value in Flow)
PERSISTENCE_NAMES = tuple(value.name.lower() for value in Persistence)


@dataclass(frozen=True, slots=True)
class TargetThresholds:
    """Frozen thresholds fitted only on the 2019-2025 training partition."""

    meaningful_return: tuple[float, ...]
    retained_move_ratio: float = 0.60
    fade_ratio: float = 0.35
    flow_imbalance: float = 0.12
    minimum_observations: int = 3

    def __post_init__(self) -> None:
        if len(self.meaningful_return) != len(RESPONSE_WINDOWS):
            raise ValueError("meaningful_return must contain one value per response window.")
        if any(value <= 0 or not np.isfinite(value) for value in self.meaningful_return):
            raise ValueError("meaningful-return thresholds must be finite and positive.")

    def as_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["response_windows"] = list(RESPONSE_WINDOWS)
        payload["target_version"] = TARGET_VERSION
        return payload

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "TargetThresholds":
        if tuple(payload.get("response_windows", ())) != RESPONSE_WINDOWS:
            raise ValueError("Threshold response-window contract does not match V17.")
        return cls(
            meaningful_return=tuple(float(value) for value in payload["meaningful_return"]),
            retained_move_ratio=float(payload["retained_move_ratio"]),
            fade_ratio=float(payload["fade_ratio"]),
            flow_imbalance=float(payload["flow_imbalance"]),
            minimum_observations=int(payload["minimum_observations"]),
        )


def fit_thresholds(
    raw_metrics: np.ndarray,
    masks: np.ndarray,
    *,
    quantile: float = 0.35,
    floor: float = 0.001,
) -> TargetThresholds:
    """Fit activity thresholds from training rows only.

    The caller owns the chronological split. Passing validation rows is an
    explicit contract violation because it would leak 2026 outcome scale into
    label construction.
    """
    metrics = np.asarray(raw_metrics, dtype=np.float64)
    valid = np.asarray(masks, dtype=np.bool_)
    if metrics.ndim != 3 or metrics.shape[1:] != (
        len(RESPONSE_WINDOWS),
        len(RAW_METRIC_NAMES),
    ):
        raise ValueError(f"Unexpected raw metric shape {metrics.shape}.")
    if valid.shape != metrics.shape[:2]:
        raise ValueError("Target mask axes do not match raw metrics.")
    high = np.abs(metrics[:, :, RAW_METRIC_NAMES.index("high_return")])
    low = np.abs(metrics[:, :, RAW_METRIC_NAMES.index("low_return")])
    magnitude = np.maximum(high, low)
    thresholds: list[float] = []
    for index in range(len(RESPONSE_WINDOWS)):
        values = magnitude[valid[:, index] & np.isfinite(magnitude[:, index]), index]
        if values.size == 0:
            raise ValueError(f"No training outcomes for {RESPONSE_WINDOWS[index]}.")
        thresholds.append(max(float(np.quantile(values, quantile)), float(floor)))
    return TargetThresholds(tuple(thresholds))


def classify_window(
    metrics: Sequence[float],
    *,
    threshold: float,
    contract: TargetThresholds,
) -> tuple[Direction, Path, Flow]:
    values = dict(zip(RAW_METRIC_NAMES, (float(value) for value in metrics), strict=True))
    high = max(values["high_return"], 0.0)
    low = min(values["low_return"], 0.0)
    terminal = values["terminal_return"]
    up = high
    down = abs(low)
    meaningful_up = up >= threshold
    meaningful_down = down >= threshold
    if not meaningful_up and not meaningful_down:
        direction = Direction.NEUTRAL
    elif up > down:
        direction = Direction.UPSIDE
    elif down > up:
        direction = Direction.DOWNSIDE
    elif terminal > 0.0:
        direction = Direction.UPSIDE
    elif terminal < 0.0:
        direction = Direction.DOWNSIDE
    else:
        # Exact excursion and terminal ties are rare. Resolve them toward the
        # later extremum so a large symmetric response is not mislabeled as
        # neutral merely because neither absolute excursion is larger.
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
    elif (not high_first) and down >= threshold and terminal >= -contract.fade_ratio * down:
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
    return direction, path, flow


def classify_persistence(
    directions: Sequence[int],
    masks: Sequence[bool],
) -> Persistence:
    valid = [
        Direction(int(direction))
        for direction, mask in zip(directions, masks, strict=True)
        if bool(mask)
    ]
    if not valid or all(value is Direction.NEUTRAL for value in valid):
        return Persistence.NO_RESPONSE
    event = next(
        (
            Direction(int(directions[index]))
            for index in range(3)
            if masks[index] and Direction(int(directions[index])) is not Direction.NEUTRAL
        ),
        Direction.NEUTRAL,
    )
    next_day = Direction(int(directions[3])) if masks[3] else Direction.NEUTRAL
    week = Direction(int(directions[4])) if masks[4] else Direction.NEUTRAL
    directional = {Direction.UPSIDE, Direction.DOWNSIDE}
    if event is Direction.NEUTRAL and (next_day in directional or week in directional):
        return Persistence.DELAYED
    if event in directional and (
        (next_day in directional and next_day is not event)
        or (week in directional and week is not event)
    ):
        return Persistence.REVERSAL
    if event in directional and week is event:
        return Persistence.MULTI_SESSION
    if event in directional and next_day is event:
        return Persistence.NEXT_SESSION
    return Persistence.EVENT_PHASE_ONLY
