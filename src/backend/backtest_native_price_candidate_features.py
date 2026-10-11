"""Full native price-break population on certified completed feature clocks."""
import numpy as np
import polars as pl

from .backtest_market_data import SESSION_OPEN_OFFSET_MS
from .backtest_strategy_certified_price_break import CertifiedInitialPriceBreakPlan
from research.causal_strategy_features.v5.decisions import DECISION_IDENTITY


def native_price_candidate_feature_decisions(plan, *, max_rows):
    """Project every momentum candidate, including rejected and unknown rows.

    CertifiedFirstPriceSource revalidates every bar attempt against candidate
    coverage. No outcomes, trades, current portfolio or feature availability
    select this population. Preparation owns the bounded identity projection.
    """
    if type(plan) is not CertifiedInitialPriceBreakPlan:
        raise ValueError('Exact native certified price-break plan required')
    if type(max_rows) is not int or max_rows < 1:
        raise ValueError('Explicit positive native feature row bound required')
    plan.source.__post_init__()
    plan.__post_init__()
    keys = plan.momentum.keys
    if len(keys) > max_rows:
        raise ValueError('Complete native candidate population exceeds declared bound')
    market = plan.source.market
    rows = pl.DataFrame({
        'build_id': pl.Series([market.build_id] * len(keys), dtype=pl.String),
        'session_date': pl.Series([market.sessions[0]] * len(keys), dtype=pl.String),
        'ticker': pl.Series([ticker for ticker, _ in keys], dtype=pl.String),
        'attempt_id': pl.Series(plan.source.source_attempts, dtype=pl.String),
        'decision_day_ms': pl.Series(np.fromiter((clock for _, clock in keys),
            dtype=np.int64, count=len(keys)) + SESSION_OPEN_OFFSET_MS, dtype=pl.Int64),
    }).select(*DECISION_IDENTITY, 'decision_day_ms')
    if (any(rows[c].null_count() for c in rows.columns)
            or rows.n_unique() != rows.height
            or rows.filter((pl.col('decision_day_ms') <= SESSION_OPEN_OFFSET_MS)
                | (pl.col('decision_day_ms') > 86400000)
                | (pl.col('decision_day_ms') % 100 != 0)).height):
        raise ValueError('Native candidate identity or completed 100ms clock differs')
    return rows
