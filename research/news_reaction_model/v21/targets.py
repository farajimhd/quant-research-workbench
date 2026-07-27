from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any, Mapping

import numpy as np

from research.news_reaction_model.v18.targets import DIRECTION_NAMES
from research.news_reaction_model.v20.targets import (
    PRICE_REGIME_NAMES,
    PUBLICATION_SESSION_COUNT,
    PUBLICATION_SESSION_NAMES,
    effective_number_weights,
    price_regime_numpy,
    RETURN_BUCKET_NAMES,
    signed_opportunity_numpy,
)


MAGNITUDE_EDGES = (
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
MAGNITUDE_BUCKET_COUNT = len(MAGNITUDE_EDGES) - 1
SIDE_NAMES = ("upside", "downside")
SIDE_COUNT = len(SIDE_NAMES)
FLAT_BUCKET_INDEX = MAGNITUDE_BUCKET_COUNT
RETURN_BUCKET_COUNT = 2 * MAGNITUDE_BUCKET_COUNT + 1


def _edge_label(value: float) -> str:
    return "+inf" if np.isposinf(value) else f"{value:g}"


MAGNITUDE_BUCKET_NAMES = tuple(
    f"[{_edge_label(left)},{_edge_label(right)})"
    for left, right in zip(MAGNITUDE_EDGES[:-1], MAGNITUDE_EDGES[1:], strict=True)
)


def magnitude_bucketize_numpy(magnitude: np.ndarray) -> np.ndarray:
    values = np.asarray(magnitude, dtype=np.float64)
    if np.any(values < 0):
        raise ValueError("Magnitude targets must be non-negative.")
    buckets = np.searchsorted(
        np.asarray(MAGNITUDE_EDGES[1:-1], dtype=np.float64),
        values,
        side="right",
    ).astype(np.int64)
    if np.any((buckets < 0) | (buckets >= MAGNITUDE_BUCKET_COUNT)):
        raise AssertionError("Magnitude bucketization escaped configured support.")
    return buckets


def direction_side_numpy(direction: np.ndarray) -> np.ndarray:
    values = np.asarray(direction, dtype=np.int64)
    return np.where(values == 1, 0, np.where(values == 2, 1, -1)).astype(np.int64)


def _fallback_magnitude_center(index: int) -> float:
    left = MAGNITUDE_EDGES[index]
    right = MAGNITUDE_EDGES[index + 1]
    return 1.5 * left if np.isposinf(right) else 0.5 * (left + right)


def _entropy(probabilities: np.ndarray) -> float:
    present = probabilities > 0
    return -float(
        np.sum(probabilities[present] * np.log(probabilities[present]))
    )


@dataclass(frozen=True, slots=True)
class TrainingStatistics:
    direction_counts: tuple[int, int, int]
    direction_weights: tuple[float, float, float]
    direction_prior: tuple[float, float, float]
    direction_prior_log_loss: float
    magnitude_counts: tuple[tuple[int, ...], tuple[int, ...]]
    magnitude_weights: tuple[tuple[float, ...], tuple[float, ...]]
    magnitude_centers: tuple[tuple[float, ...], tuple[float, ...]]
    magnitude_prior: tuple[tuple[float, ...], tuple[float, ...]]
    magnitude_prior_log_loss: tuple[float, float]
    magnitude_median: tuple[float, float]
    magnitude_scale: tuple[float, float]
    joint_prior_log_loss: float
    signed_return_median: float
    training_rows: int

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "TrainingStatistics":
        return cls(
            direction_counts=tuple(int(value) for value in payload["direction_counts"]),
            direction_weights=tuple(
                float(value) for value in payload["direction_weights"]
            ),
            direction_prior=tuple(float(value) for value in payload["direction_prior"]),
            direction_prior_log_loss=float(payload["direction_prior_log_loss"]),
            magnitude_counts=tuple(
                tuple(int(value) for value in side)
                for side in payload["magnitude_counts"]
            ),
            magnitude_weights=tuple(
                tuple(float(value) for value in side)
                for side in payload["magnitude_weights"]
            ),
            magnitude_centers=tuple(
                tuple(float(value) for value in side)
                for side in payload["magnitude_centers"]
            ),
            magnitude_prior=tuple(
                tuple(float(value) for value in side)
                for side in payload["magnitude_prior"]
            ),
            magnitude_prior_log_loss=tuple(
                float(value) for value in payload["magnitude_prior_log_loss"]
            ),
            magnitude_median=tuple(
                float(value) for value in payload["magnitude_median"]
            ),
            magnitude_scale=tuple(
                float(value) for value in payload["magnitude_scale"]
            ),
            joint_prior_log_loss=float(payload["joint_prior_log_loss"]),
            signed_return_median=float(payload["signed_return_median"]),
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
    side = direction_side_numpy(direction)
    magnitude = np.abs(signed)
    direction_counts = np.bincount(direction, minlength=len(DIRECTION_NAMES))
    direction_prior = direction_counts / max(int(direction_counts.sum()), 1)
    direction_weights = effective_number_weights(
        direction_counts,
        beta=beta,
        minimum=minimum_class_weight,
        maximum=maximum_class_weight,
    )

    counts: list[tuple[int, ...]] = []
    weights: list[tuple[float, ...]] = []
    centers: list[tuple[float, ...]] = []
    priors: list[tuple[float, ...]] = []
    prior_losses: list[float] = []
    medians: list[float] = []
    scales: list[float] = []
    joint_nll = -np.log(np.maximum(direction_prior[direction], 1e-12))
    for side_index in range(SIDE_COUNT):
        selected = side == side_index
        selected_magnitude = magnitude[selected]
        selected_buckets = magnitude_bucketize_numpy(selected_magnitude)
        side_counts = np.bincount(
            selected_buckets, minlength=MAGNITUDE_BUCKET_COUNT
        )
        side_prior = side_counts / max(int(side_counts.sum()), 1)
        side_centers = np.empty(MAGNITUDE_BUCKET_COUNT, dtype=np.float64)
        for bucket_index in range(MAGNITUDE_BUCKET_COUNT):
            values = selected_magnitude[selected_buckets == bucket_index]
            side_centers[bucket_index] = (
                float(np.median(values))
                if values.size
                else _fallback_magnitude_center(bucket_index)
            )
        if np.any(side_centers <= 0):
            raise RuntimeError("Directional magnitude centers must be positive.")
        counts.append(tuple(int(value) for value in side_counts))
        weights.append(
            tuple(
                float(value)
                for value in effective_number_weights(
                    side_counts,
                    beta=beta,
                    minimum=minimum_class_weight,
                    maximum=maximum_class_weight,
                )
            )
        )
        centers.append(tuple(float(value) for value in side_centers))
        priors.append(tuple(float(value) for value in side_prior))
        prior_losses.append(_entropy(side_prior))
        medians.append(float(np.median(selected_magnitude)))
        scales.append(
            max(float(np.quantile(selected_magnitude, 0.75)), 0.5)
        )
        selected_rows = np.flatnonzero(selected)
        joint_nll[selected_rows] += -np.log(
            np.maximum(side_prior[selected_buckets], 1e-12)
        )
    if not np.array_equal(
        np.where(signed > 0, 1, np.where(signed < 0, 2, 0)),
        direction,
    ):
        raise RuntimeError(
            "V21 hierarchy no longer reproduces authoritative V18 directions."
        )
    return TrainingStatistics(
        direction_counts=tuple(int(value) for value in direction_counts),
        direction_weights=tuple(float(value) for value in direction_weights),
        direction_prior=tuple(float(value) for value in direction_prior),
        direction_prior_log_loss=_entropy(direction_prior),
        magnitude_counts=tuple(counts),
        magnitude_weights=tuple(weights),
        magnitude_centers=tuple(centers),
        magnitude_prior=tuple(priors),
        magnitude_prior_log_loss=tuple(prior_losses),
        magnitude_median=tuple(medians),
        magnitude_scale=tuple(scales),
        joint_prior_log_loss=float(np.mean(joint_nll)),
        signed_return_median=float(np.median(signed)),
        training_rows=int(indices.size),
    )


__all__ = [
    "DIRECTION_NAMES",
    "FLAT_BUCKET_INDEX",
    "MAGNITUDE_BUCKET_COUNT",
    "MAGNITUDE_BUCKET_NAMES",
    "MAGNITUDE_EDGES",
    "PRICE_REGIME_NAMES",
    "PUBLICATION_SESSION_COUNT",
    "PUBLICATION_SESSION_NAMES",
    "RETURN_BUCKET_COUNT",
    "RETURN_BUCKET_NAMES",
    "SIDE_COUNT",
    "SIDE_NAMES",
    "TrainingStatistics",
    "direction_side_numpy",
    "fit_training_statistics",
    "magnitude_bucketize_numpy",
    "price_regime_numpy",
    "signed_opportunity_numpy",
]
