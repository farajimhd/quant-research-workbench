from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any, Mapping

import numpy as np

from research.news_reaction_model.v18.targets import (
    DIRECTION_NAMES,
    FLOW_NAMES,
    PATH_NAMES,
    REGRESSION_NAMES,
)


PRICE_REGIME_NAMES = (
    "under_1",
    "1_to_5",
    "5_to_10",
    "10_to_20",
    "20_plus_followup",
)
PUBLICATION_SESSION_NAMES = ("premarket", "regular", "afterhours", "closed")
PUBLICATION_SESSION_COUNT = len(PUBLICATION_SESSION_NAMES)
REGRESSION_COMPONENT_NAMES = ("terminal", "upper_gap", "lower_gap")


def price_regime_numpy(anchor_price: np.ndarray) -> np.ndarray:
    values = np.asarray(anchor_price, dtype=np.float64)
    return np.select(
        (values < 1.0, values < 5.0, values < 10.0, values < 20.0),
        (0, 1, 2, 3),
        default=4,
    ).astype(np.int64)


def regression_components_numpy(targets: np.ndarray) -> np.ndarray:
    values = np.asarray(targets, dtype=np.float64)
    high, low, terminal = values[:, 0], values[:, 1], values[:, 2]
    return np.stack(
        (
            terminal,
            np.maximum(high - terminal, 0.0),
            np.maximum(terminal - low, 0.0),
        ),
        axis=1,
    )


def effective_number_weights(
    counts: np.ndarray,
    *,
    beta: float,
    minimum: float,
    maximum: float,
) -> np.ndarray:
    counts = np.asarray(counts, dtype=np.float64)
    if np.any(counts <= 0):
        raise ValueError(f"Every training class requires support; received {counts.tolist()}.")
    effective = (1.0 - beta) / np.maximum(1.0 - np.power(beta, counts), 1e-12)
    weights = effective / np.average(effective, weights=counts)
    weights = np.clip(weights, minimum, maximum)
    return weights.astype(np.float32)


@dataclass(frozen=True, slots=True)
class TrainingStatistics:
    direction_counts: tuple[int, ...]
    path_counts: tuple[int, ...]
    flow_counts: tuple[int, ...]
    direction_weights: tuple[float, ...]
    path_weights: tuple[float, ...]
    flow_weights: tuple[float, ...]
    regression_scales: tuple[tuple[tuple[float, ...], ...], ...]
    regression_training_median: tuple[float, float, float]
    training_rows: int

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "TrainingStatistics":
        return cls(
            direction_counts=tuple(int(value) for value in payload["direction_counts"]),
            path_counts=tuple(int(value) for value in payload["path_counts"]),
            flow_counts=tuple(int(value) for value in payload["flow_counts"]),
            direction_weights=tuple(float(value) for value in payload["direction_weights"]),
            path_weights=tuple(float(value) for value in payload["path_weights"]),
            flow_weights=tuple(float(value) for value in payload["flow_weights"]),
            regression_scales=tuple(
                tuple(tuple(float(value) for value in cell) for cell in regime)
                for regime in payload["regression_scales"]
            ),
            regression_training_median=tuple(
                float(value) for value in payload["regression_training_median"]
            ),
            training_rows=int(payload["training_rows"]),
        )


def fit_training_statistics(
    dataset: Any,
    *,
    beta: float,
    minimum_class_weight: float,
    maximum_class_weight: float,
    scale_quantile: float,
    scale_floor_pct: float,
    minimum_scale_rows: int,
) -> TrainingStatistics:
    indices = np.asarray(dataset.indices, dtype=np.int64)
    source = np.asarray(dataset.arrays["source_index"][indices], dtype=np.int64)
    regimes = price_regime_numpy(dataset.arrays["anchor_price"][indices])
    sessions = np.asarray(
        dataset.v15["time_features"][source, :PUBLICATION_SESSION_COUNT],
        dtype=np.float32,
    ).argmax(axis=1)
    regression = np.asarray(dataset.arrays["regression_targets"][indices], dtype=np.float64)
    components = regression_components_numpy(regression)

    counts = {
        "direction": np.bincount(
            np.asarray(dataset.arrays["direction"][indices], dtype=np.int64),
            minlength=len(DIRECTION_NAMES),
        ),
        "path": np.bincount(
            np.asarray(dataset.arrays["path"][indices], dtype=np.int64),
            minlength=len(PATH_NAMES),
        ),
        "flow": np.bincount(
            np.asarray(dataset.arrays["flow"][indices], dtype=np.int64),
            minlength=len(FLOW_NAMES),
        ),
    }
    global_scales = np.maximum(
        np.quantile(np.abs(components), scale_quantile, axis=0),
        scale_floor_pct,
    )
    scales = np.empty(
        (len(PRICE_REGIME_NAMES), len(PUBLICATION_SESSION_NAMES), 3),
        dtype=np.float64,
    )
    for regime in range(len(PRICE_REGIME_NAMES)):
        for session in range(len(PUBLICATION_SESSION_NAMES)):
            mask = (regimes == regime) & (sessions == session)
            if int(mask.sum()) < minimum_scale_rows:
                scales[regime, session] = global_scales
            else:
                scales[regime, session] = np.maximum(
                    np.quantile(np.abs(components[mask]), scale_quantile, axis=0),
                    scale_floor_pct,
                )

    def weights(name: str) -> tuple[float, ...]:
        return tuple(
            float(value)
            for value in effective_number_weights(
                counts[name],
                beta=beta,
                minimum=minimum_class_weight,
                maximum=maximum_class_weight,
            )
        )

    return TrainingStatistics(
        direction_counts=tuple(int(value) for value in counts["direction"]),
        path_counts=tuple(int(value) for value in counts["path"]),
        flow_counts=tuple(int(value) for value in counts["flow"]),
        direction_weights=weights("direction"),
        path_weights=weights("path"),
        flow_weights=weights("flow"),
        regression_scales=tuple(
            tuple(tuple(float(value) for value in cell) for cell in regime)
            for regime in scales
        ),
        regression_training_median=tuple(
            float(value) for value in np.median(regression, axis=0)
        ),
        training_rows=int(indices.size),
    )


__all__ = [
    "DIRECTION_NAMES",
    "FLOW_NAMES",
    "PATH_NAMES",
    "PRICE_REGIME_NAMES",
    "PUBLICATION_SESSION_NAMES",
    "REGRESSION_COMPONENT_NAMES",
    "REGRESSION_NAMES",
    "TrainingStatistics",
    "fit_training_statistics",
    "price_regime_numpy",
    "regression_components_numpy",
]
