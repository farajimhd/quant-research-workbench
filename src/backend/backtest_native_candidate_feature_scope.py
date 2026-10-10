"""Full certified candidate identities for producer-owned native features.

This bridge supplies dependency scope, never eligibility or order authority.
It consumes every parent candidate, including rejected candidates, without a
research cohort, labels, trade outcomes or a feature-availability filter.
"""
import numpy as np
import polars as pl

from .backtest_declared_native_fixed_plan import DeclaredEntrySourcePlan
from .backtest_market_data import SESSION_OPEN_OFFSET_MS
from research.causal_strategy_features.v5.decisions import DECISION_IDENTITY


def declared_candidate_feature_decisions(source, *, max_rows):
    """Project the exact source-ordered population onto source-midnight clocks.

    The consuming immutable release must pin max_rows and its feature policy.
    Producer coverage and installed readback remain separate preflight gates;
    this projection cannot certify them or narrow the parent opportunity set.
    Parent source and financial masks are unchanged. Call once in preparation.
    """
    if type(source) is not DeclaredEntrySourcePlan:
        raise ValueError('Exact declared native entry source required')
    if type(max_rows) is not int or max_rows < 1:
        raise ValueError('Explicit positive candidate feature row bound required')
    source.__post_init__()
    parent = source.parent
    keys = parent.momentum.keys
    if len(keys) > max_rows:
        raise ValueError('Complete candidate feature population exceeds declared bound')
    if len(parent.market.sessions) != 1:
        raise ValueError('Candidate feature scope requires one certified source day')
    day = parent.market.sessions[0]
    attempts = source.source_attempts[0]
    if len(attempts) != len(keys):
        raise ValueError('Candidate feature bars attempts differ from source population')
    # Identity gathering is bounded preparation; clock checks and scope joins
    # operate columnarly. No sequential strategy or financial callback runs.
    decisions = pl.DataFrame({
        'build_id': pl.Series([parent.market.build_id] * len(keys), dtype=pl.String),
        'session_date': pl.Series([day] * len(keys), dtype=pl.String),
        'ticker': pl.Series([key[0] for key in keys], dtype=pl.String),
        'attempt_id': pl.Series(attempts, dtype=pl.String),
        'decision_day_ms': pl.Series(
            np.fromiter((key[1] for key in keys), dtype=np.int64, count=len(keys))
            + SESSION_OPEN_OFFSET_MS, dtype=pl.Int64),
    }).select(*DECISION_IDENTITY, 'decision_day_ms')
    if (any(decisions[c].null_count() for c in decisions.columns)
            or decisions.n_unique() != decisions.height
            or decisions.filter((pl.col('decision_day_ms') <= SESSION_OPEN_OFFSET_MS)
                | (pl.col('decision_day_ms') > 86400000)
                | (pl.col('decision_day_ms') % int(parent.capabilities.execution_interval[:-2]) != 0)).height):
        raise ValueError('Candidate feature identities or completed clocks differ')
    return decisions


def require_complete_candidate_feature_scope(source, decisions, *, max_rows):
    """Reject omissions, reordering, foreign attempts and hindsight substitutions."""
    if type(decisions) is not pl.DataFrame:
        raise ValueError('Exact candidate feature dataframe required')
    expected = declared_candidate_feature_decisions(source, max_rows=max_rows)
    if decisions.schema != expected.schema or not decisions.equals(expected):
        raise ValueError('Feature decisions differ from complete certified candidate population')
    return expected
