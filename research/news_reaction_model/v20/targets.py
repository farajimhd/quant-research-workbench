from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any, Mapping

import numpy as np

from research.news_reaction_model.v18.targets import DIRECTION_NAMES


PRICE_REGIME_NAMES = (
    "under_1",
    "1_to_5",
    "5_to_10",
    "10_to_20",
    "20_plus_followup",
)
PUBLICATION_SESSION_NAMES = ("premarket", "regular", "afterhours", "closed")
PUBLICATION_SESSION_COUNT = len(PUBLICATION_SESSION_NAMES)

# Percent-return intervals. Neutral is a dedicated atom rather than a numeric
# interval, so a small but authoritative V18 upside/downside label cannot be
# collapsed into neutral merely because its return is below 0.5%.
DOWN_RETURN_EDGES = (
    float("-inf"),
    -100.0,
    -50.0,
    -20.0,
    -10.0,
    -5.0,
    -2.0,
    -1.0,
    -0.5,
    0.0,
)
UP_RETURN_EDGES = (
    0.0,
    0.5,
    1.0,
    2.0,
    5.0,
    10.0,
    20.0,
    50.0,
    100.0,
    float("inf"),
)
DOWN_BUCKET_COUNT = len(DOWN_RETURN_EDGES) - 1
UP_BUCKET_COUNT = len(UP_RETURN_EDGES) - 1
FLAT_BUCKET_INDEX = DOWN_BUCKET_COUNT
RETURN_BUCKET_COUNT = DOWN_BUCKET_COUNT + 1 + UP_BUCKET_COUNT
DOWN_BUCKET_INDICES = tuple(range(FLAT_BUCKET_INDEX))
UP_BUCKET_INDICES = tuple(range(FLAT_BUCKET_INDEX + 1, RETURN_BUCKET_COUNT))


def _edge_label(value: float) -> str:
    if np.isneginf(value):
        return "-inf"
    if np.isposinf(value):
        return "+inf"
    return f"{value:g}"


RETURN_BUCKET_NAMES = (
    *tuple(
        f"[{_edge_label(left)},{_edge_label(right)})"
        for left, right in zip(
            DOWN_RETURN_EDGES[:-1], DOWN_RETURN_EDGES[1:], strict=True
        )
    ),
    "neutral",
    *tuple(
    f"[{_edge_label(left)},{_edge_label(right)})"
        for left, right in zip(
            UP_RETURN_EDGES[:-1], UP_RETURN_EDGES[1:], strict=True
        )
    ),
)


def price_regime_numpy(anchor_price: np.ndarray) -> np.ndarray:
    values = np.asarray(anchor_price, dtype=np.float64)
    return np.select(
        (values < 1.0, values < 5.0, values < 10.0, values < 20.0),
        (0, 1, 2, 3),
        default=4,
    ).astype(np.int64)


def signed_opportunity_numpy(
    direction: np.ndarray,
    regression_targets: np.ndarray,
) -> np.ndarray:
    """Map V18 excursion-dominance labels to one coherent signed opportunity."""

    classes = np.asarray(direction, dtype=np.int64)
    targets = np.asarray(regression_targets, dtype=np.float64)
    if targets.ndim != 2 or targets.shape[1] != 3:
        raise ValueError(f"Expected high/low/terminal targets, received {targets.shape}.")
    if np.any((classes < 0) | (classes >= len(DIRECTION_NAMES))):
        raise ValueError("Direction values must be neutral/upside/downside.")
    return np.where(
        classes == 1,
        np.maximum(targets[:, 0], 0.0),
        np.where(classes == 2, np.minimum(targets[:, 1], 0.0), 0.0),
    )


def bucketize_numpy(signed_return: np.ndarray) -> np.ndarray:
    values = np.asarray(signed_return, dtype=np.float64)
    buckets = np.full(values.shape, FLAT_BUCKET_INDEX, dtype=np.int64)
    negative = values < 0
    positive = values > 0
    buckets[negative] = np.searchsorted(
        np.asarray(DOWN_RETURN_EDGES[1:-1], dtype=np.float64),
        values[negative],
        side="right",
    )
    buckets[positive] = (
        FLAT_BUCKET_INDEX
        + 1
        + np.searchsorted(
            np.asarray(UP_RETURN_EDGES[1:-1], dtype=np.float64),
            values[positive],
            side="right",
        )
    )
    if np.any((buckets < 0) | (buckets >= RETURN_BUCKET_COUNT)):
        raise AssertionError("Return bucketization escaped the configured support.")
    return buckets


def bucket_directions_numpy(buckets: np.ndarray) -> np.ndarray:
    values = np.asarray(buckets, dtype=np.int64)
    return np.where(
        values < FLAT_BUCKET_INDEX,
        2,
        np.where(values > FLAT_BUCKET_INDEX, 1, 0),
    ).astype(np.int64)


def _fallback_center(index: int) -> float:
    if index == FLAT_BUCKET_INDEX:
        return 0.0
    edges = DOWN_RETURN_EDGES if index < FLAT_BUCKET_INDEX else UP_RETURN_EDGES
    edge_index = index if index < FLAT_BUCKET_INDEX else index - FLAT_BUCKET_INDEX - 1
    left = edges[edge_index]
    right = edges[edge_index + 1]
    if np.isneginf(left):
        return 1.5 * right
    if np.isposinf(right):
        return 1.5 * left
    return 0.5 * (left + right)


def effective_number_weights(
    counts: np.ndarray,
    *,
    beta: float,
    minimum: float,
    maximum: float,
) -> np.ndarray:
    counts = np.asarray(counts, dtype=np.float64)
    present = counts > 0
    if not np.any(present):
        raise ValueError("At least one return bucket requires training support.")
    effective = np.zeros_like(counts)
    effective[present] = (1.0 - beta) / np.maximum(
        1.0 - np.power(beta, counts[present]), 1e-12
    )
    normalization = np.average(effective[present], weights=counts[present])
    effective[present] /= normalization
    effective[present] = np.clip(effective[present], minimum, maximum)
    return effective.astype(np.float32)


@dataclass(frozen=True, slots=True)
class TrainingStatistics:
    bucket_counts: tuple[int, ...]
    bucket_weights: tuple[float, ...]
    bucket_centers: tuple[float, ...]
    direction_counts: tuple[int, int, int]
    signed_return_median: float
    signed_return_scale: float
    direction_prior: tuple[float, float, float]
    direction_prior_log_loss: float
    bucket_prior_log_loss: float
    training_rows: int

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "TrainingStatistics":
        return cls(
            bucket_counts=tuple(int(value) for value in payload["bucket_counts"]),
            bucket_weights=tuple(float(value) for value in payload["bucket_weights"]),
            bucket_centers=tuple(float(value) for value in payload["bucket_centers"]),
            direction_counts=tuple(int(value) for value in payload["direction_counts"]),
            signed_return_median=float(payload["signed_return_median"]),
            signed_return_scale=float(payload["signed_return_scale"]),
            direction_prior=tuple(float(value) for value in payload["direction_prior"]),
            direction_prior_log_loss=float(payload["direction_prior_log_loss"]),
            bucket_prior_log_loss=float(payload["bucket_prior_log_loss"]),
            training_rows=int(payload["training_rows"]),
        )


def fit_training_statistics(
    dataset: Any,
    *,
    beta: float,
    minimum_class_weight: float,
    maximum_class_weight: float,
) -> TrainingStatistics:
    indices = np.asarray(dataset.indices, dtype=np.int64)
    direction = np.asarray(dataset.arrays["direction"][indices], dtype=np.int64)
    regression = np.asarray(
        dataset.arrays["regression_targets"][indices], dtype=np.float64
    )
    signed = signed_opportunity_numpy(direction, regression)
    buckets = bucketize_numpy(signed)
    if not np.array_equal(bucket_directions_numpy(buckets), direction):
        raise RuntimeError(
            "V20 bucket direction drift: signed opportunity no longer reproduces "
            "the authoritative V18 direction class."
        )
    bucket_counts = np.bincount(buckets, minlength=RETURN_BUCKET_COUNT)
    direction_counts = np.bincount(direction, minlength=len(DIRECTION_NAMES))
    centers = np.empty(RETURN_BUCKET_COUNT, dtype=np.float64)
    for index in range(RETURN_BUCKET_COUNT):
        selected = signed[buckets == index]
        centers[index] = (
            float(np.median(selected)) if selected.size else _fallback_center(index)
        )
    centers[FLAT_BUCKET_INDEX] = 0.0
    if np.any(centers[:FLAT_BUCKET_INDEX] >= 0) or np.any(
        centers[FLAT_BUCKET_INDEX + 1 :] <= 0
    ):
        raise RuntimeError("Return bucket representatives violate directional signs.")
    prior = direction_counts / max(int(direction_counts.sum()), 1)
    prior_log_loss = -float(np.sum(prior * np.log(np.maximum(prior, 1e-12))))
    bucket_prior = bucket_counts / max(int(bucket_counts.sum()), 1)
    bucket_prior_log_loss = -float(
        np.sum(bucket_prior * np.log(np.maximum(bucket_prior, 1e-12)))
    )
    nonzero = np.abs(signed[np.nonzero(signed)])
    scale = float(np.quantile(nonzero, 0.75)) if nonzero.size else 1.0
    return TrainingStatistics(
        bucket_counts=tuple(int(value) for value in bucket_counts),
        bucket_weights=tuple(
            float(value)
            for value in effective_number_weights(
                bucket_counts,
                beta=beta,
                minimum=minimum_class_weight,
                maximum=maximum_class_weight,
            )
        ),
        bucket_centers=tuple(float(value) for value in centers),
        direction_counts=tuple(int(value) for value in direction_counts),
        signed_return_median=float(np.median(signed)),
        signed_return_scale=max(scale, 0.5),
        direction_prior=tuple(float(value) for value in prior),
        direction_prior_log_loss=prior_log_loss,
        bucket_prior_log_loss=bucket_prior_log_loss,
        training_rows=int(indices.size),
    )


__all__ = [
    "DIRECTION_NAMES",
    "DOWN_BUCKET_INDICES",
    "FLAT_BUCKET_INDEX",
    "PRICE_REGIME_NAMES",
    "PUBLICATION_SESSION_COUNT",
    "PUBLICATION_SESSION_NAMES",
    "RETURN_BUCKET_COUNT",
    "DOWN_RETURN_EDGES",
    "RETURN_BUCKET_NAMES",
    "TrainingStatistics",
    "UP_RETURN_EDGES",
    "UP_BUCKET_INDICES",
    "bucket_directions_numpy",
    "bucketize_numpy",
    "fit_training_statistics",
    "price_regime_numpy",
    "signed_opportunity_numpy",
]
