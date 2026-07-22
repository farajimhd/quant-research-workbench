from __future__ import annotations

import csv
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np


@dataclass(frozen=True, slots=True)
class AnchorPriceBucket:
    key: str
    label: str
    minimum_inclusive: float | None
    maximum_exclusive: float | None

    def mask(self, anchor_prices: np.ndarray) -> np.ndarray:
        mask = np.isfinite(anchor_prices) & (anchor_prices > 0)
        if self.minimum_inclusive is not None:
            mask &= anchor_prices >= self.minimum_inclusive
        if self.maximum_exclusive is not None:
            mask &= anchor_prices < self.maximum_exclusive
        return mask


# These are anchor-price bands, not company market-cap classifications.
# Boundaries are mutually exclusive: <$1, $1-<$20, $20-<$100, and $100+.
ANCHOR_PRICE_BUCKETS = (
    AnchorPriceBucket("penny_under_1", "Penny (<$1)", None, 1.0),
    AnchorPriceBucket("small_1_to_20", "Small price ($1-<$20)", 1.0, 20.0),
    AnchorPriceBucket("mid_20_to_100", "Mid price ($20-<$100)", 20.0, 100.0),
    AnchorPriceBucket("large_100_plus", "Large price ($100+)", 100.0, None),
)


@dataclass(slots=True)
class SidePnlLedger:
    positions: int = 0
    profitable: int = 0
    losing: int = 0
    breakeven: int = 0
    one_share_pnl: float = 0.0

    def add(self, pnl: np.ndarray) -> None:
        values = np.asarray(pnl, dtype=np.float64).reshape(-1)
        values = values[np.isfinite(values)]
        self.positions += int(values.size)
        self.profitable += int((values > 0).sum())
        self.losing += int((values < 0).sum())
        self.breakeven += int((values == 0).sum())
        self.one_share_pnl += float(values.sum())

    def summary(self) -> dict[str, Any]:
        return {
            "positions": self.positions,
            "one_share_pnl": self.one_share_pnl,
            "mean_one_share_pnl": self.one_share_pnl / max(self.positions, 1),
            "profitable": self.profitable,
            "losing": self.losing,
            "breakeven": self.breakeven,
            "win_rate": self.profitable / max(self.positions, 1),
        }


@dataclass(slots=True)
class BucketPnlLedger:
    labels: int = 0
    abstained: int = 0
    long: SidePnlLedger = field(default_factory=SidePnlLedger)
    short: SidePnlLedger = field(default_factory=SidePnlLedger)

    def add(self, side: np.ndarray, pnl: np.ndarray) -> None:
        sides = np.asarray(side, dtype=np.int8).reshape(-1)
        values = np.asarray(pnl, dtype=np.float64).reshape(-1)
        valid = np.isfinite(values)
        sides, values = sides[valid], values[valid]
        self.labels += int(values.size)
        self.abstained += int((sides == 0).sum())
        self.long.add(values[sides == 1])
        self.short.add(values[sides == -1])

    def summary(self) -> dict[str, Any]:
        long = self.long.summary()
        short = self.short.summary()
        active = self.long.positions + self.short.positions
        total_pnl = self.long.one_share_pnl + self.short.one_share_pnl
        return {
            "labels": self.labels,
            "active_positions": active,
            "abstained": self.abstained,
            "one_share_pnl": total_pnl,
            "mean_one_share_pnl_per_active": total_pnl / max(active, 1),
            "long": long,
            "short": short,
        }


@dataclass(slots=True)
class AnchorPricePnlBreakdown:
    buckets: dict[str, BucketPnlLedger] = field(
        default_factory=lambda: {bucket.key: BucketPnlLedger() for bucket in ANCHOR_PRICE_BUCKETS}
    )
    unclassified_labels: int = 0

    def add(self, side: np.ndarray, pnl: np.ndarray, anchor_prices: np.ndarray) -> None:
        sides = np.asarray(side, dtype=np.int8).reshape(-1)
        values = np.asarray(pnl, dtype=np.float64).reshape(-1)
        anchors = np.asarray(anchor_prices, dtype=np.float64).reshape(-1)
        if not (sides.size == values.size == anchors.size):
            raise ValueError("side, pnl, and anchor_prices must have identical lengths")

        classified = np.zeros(anchors.size, dtype=bool)
        for bucket in ANCHOR_PRICE_BUCKETS:
            selected = bucket.mask(anchors)
            self.buckets[bucket.key].add(sides[selected], values[selected])
            classified |= selected
        self.unclassified_labels += int((~classified).sum())

    def summary(self) -> dict[str, Any]:
        result: dict[str, Any] = {
            "basis": "pre-news anchor price in USD; bands are price ranges, not market capitalization",
            "unclassified_labels": self.unclassified_labels,
            "buckets": {},
        }
        for bucket in ANCHOR_PRICE_BUCKETS:
            result["buckets"][bucket.key] = {
                "label": bucket.label,
                "minimum_inclusive": bucket.minimum_inclusive,
                "maximum_exclusive": bucket.maximum_exclusive,
                **self.buckets[bucket.key].summary(),
            }
        return result


ANCHOR_PRICE_PNL_CSV_FIELDS = (
    "horizon", "price_bucket", "price_label", "minimum_inclusive", "maximum_exclusive",
    "labels", "active_positions", "abstained", "one_share_pnl", "mean_one_share_pnl_per_active",
    "long_positions", "long_one_share_pnl", "long_mean_one_share_pnl", "long_win_rate",
    "short_positions", "short_one_share_pnl", "short_mean_one_share_pnl", "short_win_rate",
)


def write_anchor_price_pnl_csv(
    path: Path,
    breakdowns: list[tuple[str, AnchorPricePnlBreakdown]],
) -> None:
    """Write the common V6/V7 price-band report from one versioned schema."""
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=ANCHOR_PRICE_PNL_CSV_FIELDS)
        writer.writeheader()
        for horizon, breakdown in breakdowns:
            summary_by_bucket = breakdown.summary()["buckets"]
            for bucket in ANCHOR_PRICE_BUCKETS:
                row = summary_by_bucket[bucket.key]
                writer.writerow({
                    "horizon": horizon, "price_bucket": bucket.key, "price_label": row["label"],
                    "minimum_inclusive": row["minimum_inclusive"], "maximum_exclusive": row["maximum_exclusive"],
                    "labels": row["labels"], "active_positions": row["active_positions"], "abstained": row["abstained"],
                    "one_share_pnl": row["one_share_pnl"], "mean_one_share_pnl_per_active": row["mean_one_share_pnl_per_active"],
                    "long_positions": row["long"]["positions"], "long_one_share_pnl": row["long"]["one_share_pnl"],
                    "long_mean_one_share_pnl": row["long"]["mean_one_share_pnl"], "long_win_rate": row["long"]["win_rate"],
                    "short_positions": row["short"]["positions"], "short_one_share_pnl": row["short"]["one_share_pnl"],
                    "short_mean_one_share_pnl": row["short"]["mean_one_share_pnl"], "short_win_rate": row["short"]["win_rate"],
                })
