"""Installed native channel qualification during bounded entry preparation.

This filter cannot authorize an order or certify a candidate population. A new
immutable strategy must select its rule and retain original candidate, VWAP,
liquidity, structural, Portfolio and OMS admission contracts.
"""
import numpy as np
import polars as pl

from research.causal_strategy_features.v5.decisions import (
    DECISION_IDENTITY, MAX_EXPANDED_DECISIONS, multi_resolution_decisions,
)
from src.backend.backtest_native_channel_store import load_declared_native_channels
from src.market_engine.native_causal_channel_contract import NativeChannelRequest
from src.trading_runtime.native_channel_qualification import (
    NativeChannelQualificationPolicy, qualify_native_channels,
)


def qualify_installed_native_channel_decisions(client, request, feature_attempt_id,
        decisions, mandatory_eligible, *, policy, producer_source_hash, authority):
    """SELECT declared inputs once, align columnarly, and intersect eligibility.

    Decisions use source-midnight milliseconds and exact bars-attempt identity.
    Caller-owned financial clocks and the complete causal parent population
    remain mandatory. Extra columns, including research labels, are discarded.
    Call at entry preparation, never once per broker row. Returned Boolean
    storage is detached and immutable; input masks and row order are preserved.
    """
    if (type(request) is not NativeChannelRequest or
            type(policy) is not NativeChannelQualificationPolicy or
            type(decisions) is not pl.DataFrame):
        raise ValueError('Typed installed native qualification inputs required')
    request.__post_init__()
    policy.__post_init__()
    if policy.inputs != request.policy:
        raise ValueError('Qualification input policy differs from installed request')
    if (type(mandatory_eligible) is not np.ndarray or
            mandatory_eligible.dtype != np.dtype(bool) or
            mandatory_eligible.shape != (decisions.height,)):
        raise ValueError('Exact original Boolean eligibility mask required')
    if decisions.height * len(request.policy.resolutions_ms) > MAX_EXPANDED_DECISIONS:
        raise ValueError('Installed qualification decision packet exceeds bound')
    required = dict.fromkeys(DECISION_IDENTITY, pl.String)
    required['decision_day_ms'] = pl.Int64
    if any(decisions.schema.get(k) != v for k, v in required.items()):
        raise ValueError('Installed qualification decision schema differs')
    left = decisions.select(*DECISION_IDENTITY, 'decision_day_ms')
    if (any(left[c].null_count() for c in left.columns) or
            left.n_unique() != left.height or
            left.filter((pl.col('decision_day_ms') < 0) |
                (pl.col('decision_day_ms') > request.through_day_boundary_ms) |
                (pl.col('decision_day_ms') % request.policy.decision_interval_ms != 0)).height):
        raise ValueError('Installed qualification identity or declared clock differs')
    # Detach before any transport call; a caller cannot change its mask through
    # an alias while installed source checks run.
    mandatory = np.frombuffer(mandatory_eligible.tobytes(), dtype=bool)
    packet = load_declared_native_channels(client, request, feature_attempt_id,
        producer_source_hash=producer_source_hash, authority=authority)
    scope = pl.from_arrow(packet.coverage).select(*DECISION_IDENTITY).unique()
    if left.join(scope, on=list(DECISION_IDENTITY), how='anti').height:
        raise ValueError('Decision identity is outside installed native coverage')
    aligned = multi_resolution_decisions(pl.from_arrow(packet.rows), left,
        decision_interval_ms=request.policy.decision_interval_ms,
        freshness_by_resolution=request.policy.freshness_by_resolution)
    qualified = qualify_native_channels(aligned, mandatory, policy)
    return np.frombuffer(qualified.tobytes(), dtype=bool)
