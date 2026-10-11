"""Shared scanner rule over certified QMD History volume snapshots.

The producer owns the elapsed-volume window and split-adjusted prior close.
Neither a strategy nor Backtest derives those inputs from private bars here.
"""
from dataclasses import dataclass
from math import isfinite

import polars as pl


@dataclass(frozen=True)
class VolumeUniverseRule:
    window_seconds: int
    top_count: int
    minimum_prior_close: float
    maximum_prior_close: float

    def __post_init__(self):
        if (type(self.window_seconds) is not int or self.window_seconds < 1
                or type(self.top_count) is not int or self.top_count < 1
                or not isfinite(self.minimum_prior_close)
                or not isfinite(self.maximum_prior_close)
                or not 0 < self.minimum_prior_close <= self.maximum_prior_close):
            raise ValueError("Volume universe needs an explicit valid ranking contract")


def select_volume_universe(frame: pl.DataFrame, rule: VolumeUniverseRule) -> pl.DataFrame:
    """Score descending, listing identity ascending; current trade not required.

    All price-eligible listings must have certified rolling-volume coverage,
    including zero-volume clocks. The history reader verifies complete scope
    before this rule; an absent row cannot be interpreted as zero volume.
    Held positions remain in the coordinator's management universe separately.
    """
    fields = {"listing_id", "ticker", "boundary_at", "available_at",
              "prior_close_available_at", "adjusted_prior_close", "rolling_volume",
              "volume_window_seconds"}
    if not fields <= set(frame.columns) or frame.height > 262_144:
        raise ValueError("Volume scanner needs a bounded certified snapshot")
    for key in ("boundary_at", "available_at", "prior_close_available_at"):
        if not isinstance(frame.schema[key], pl.Datetime) or frame.schema[key].time_zone is None:
            raise ValueError("Volume scanner clocks must be timezone-aware")
    if (frame.select(pl.any_horizontal(pl.col(c).is_null() for c in fields).any()).item()
            or frame.select(pl.struct("boundary_at", "listing_id").is_duplicated().any()).item()
            or frame.select(((pl.col("available_at") > pl.col("boundary_at"))
                            | (pl.col("prior_close_available_at") >= pl.col("boundary_at"))
                            | (pl.col("volume_window_seconds") != rule.window_seconds)
                            | ~pl.col("rolling_volume").is_finite()
                            | (pl.col("rolling_volume") < 0)
                            | ~pl.col("adjusted_prior_close").is_finite()).any()).item()):
        raise ValueError("Volume snapshot has future, missing, duplicate or incompatible inputs")
    eligible = frame.filter(pl.col("adjusted_prior_close").is_between(
        rule.minimum_prior_close, rule.maximum_prior_close, closed="both"))
    ranked = eligible.filter(pl.col("rolling_volume") > 0).sort(
        ["boundary_at", "rolling_volume", "listing_id"], descending=[False, True, False])
    return (ranked.with_columns(pl.int_range(1, pl.len() + 1).over("boundary_at").alias("rank"))
            .filter(pl.col("rank") <= rule.top_count))
