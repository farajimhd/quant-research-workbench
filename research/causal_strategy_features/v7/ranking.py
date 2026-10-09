"""Declared causal opportunity scores; no admission or execution authority."""
from dataclasses import dataclass
from math import isfinite

import polars as pl

from research.causal_strategy_features.v5.decisions import DECISION_IDENTITY
from src.backend.backtest_market_data import FIXED_RESOLUTIONS_MS


@dataclass(frozen=True, slots=True)
class ParticipationRankingPolicy:
    resolution_weights: tuple[tuple[int, float], ...]
    volume_weight: float
    trade_weight: float
    wick_penalty: float
    minimum_known_resolutions: int
    max_rows: int

    def __post_init__(self):
        if (type(self.resolution_weights) is not tuple or not self.resolution_weights
                or any(type(p) is not tuple or len(p) != 2 for p in self.resolution_weights)):
            raise ValueError('Explicit ordered resolution weights required')
        resolutions = tuple(p[0] for p in self.resolution_weights)
        weights = tuple(p[1] for p in self.resolution_weights)
        if (any(type(r) is not int or r not in FIXED_RESOLUTIONS_MS for r in resolutions)
                or resolutions != tuple(sorted(set(resolutions)))
                or any(type(w) is not float or not isfinite(w) or w <= 0 for w in weights)
                or any(type(w) is not float or not isfinite(w) or w < 0
                       for w in (self.volume_weight, self.trade_weight, self.wick_penalty))
                or self.volume_weight + self.trade_weight <= 0
                or type(self.minimum_known_resolutions) is not int
                or not 1 <= self.minimum_known_resolutions <= len(resolutions)
                or type(self.max_rows) is not int or self.max_rows < 1):
            raise ValueError('Finite declared ranking weights and coverage bounds required')


def opportunity_scores(frame, *, policy):
    """Score completed channels without labels, row loops or cash decisions.

    Within each declared resolution: volume_weight*log1p(relative volume) +
    trade_weight*log1p(relative trades) - wick_penalty*upper_wick/range.
    Combine known scores by their declared resolution weights. Partial coverage
    is visible and allowed only by the explicit minimum-known declaration.
    Upstream owns certification, freshness, identity and feature production.
    """
    if type(policy) is not ParticipationRankingPolicy:
        raise ValueError('Exact declared participation ranking policy required')
    policy.__post_init__()
    if frame.height > policy.max_rows:
        raise ValueError('Opportunity score packet exceeds declared bound')
    identity = (*DECISION_IDENTITY, 'decision_day_ms')
    selected = frame.select(*identity, *[
        f'channels_{resolution}ms' for resolution, _ in policy.resolution_weights])
    if any(selected[c].null_count() for c in identity) or selected.select(identity).n_unique() != selected.height:
        raise ValueError('Missing or duplicate opportunity decision identity')
    scores = []
    for resolution, _ in policy.resolution_weights:
        field = lambda name: pl.col(f'channels_{resolution}ms').struct.field(name)
        if selected.filter(field('feature_row_available') & (
                field('available_day_boundary_ms').is_null() |
                (field('available_day_boundary_ms') > pl.col('decision_day_ms')))).height:
            raise ValueError('Opportunity feature has missing or future availability clock')
        volume, trades = field('relative_execution_volume'), field('relative_trade_count')
        width = field('high_return') - field('low_return')
        wick = field('upper_wick_return')
        known = (field('feature_row_available') & field('candle_available') &
                 volume.is_finite() & trades.is_finite() & width.is_finite() &
                 wick.is_finite() & (volume >= 0) & (trades >= 0) &
                 (width > 0) & (wick >= 0) & (wick <= width)).fill_null(False)
        score = (policy.volume_weight * volume.log1p() +
                 policy.trade_weight * trades.log1p() - policy.wick_penalty * wick / width)
        scores.append(pl.when(known & score.is_finite()).then(score)
                      .alias(f'score_{resolution}ms'))
    result = selected.with_columns(scores).select(*identity, *[
        f'score_{resolution}ms' for resolution, _ in policy.resolution_weights])
    names = [f'score_{resolution}ms' for resolution, _ in policy.resolution_weights]
    result = result.with_columns(
        pl.sum_horizontal([pl.col(n).is_not_null().cast(pl.UInt32) for n in names])
          .alias('known_resolution_count'))
    numerator = pl.sum_horizontal([
        pl.col(n).fill_null(0) * w for n, (_, w) in zip(names, policy.resolution_weights)])
    denominator = pl.sum_horizontal([
        pl.when(pl.col(n).is_not_null()).then(w).otherwise(0.)
        for n, (_, w) in zip(names, policy.resolution_weights)])
    known = pl.col('known_resolution_count') >= policy.minimum_known_resolutions
    combined = numerator / denominator
    return result.with_columns(
        (known & combined.is_finite()).fill_null(False).alias('score_available'),
        pl.when(known & combined.is_finite()).then(combined).alias('opportunity_score'))
