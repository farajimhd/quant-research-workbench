"""Prepared installed-channel ranking adapter; never order or release admission."""
from typing import NamedTuple

import numpy as np
import polars as pl

from research.causal_strategy_features.v5.decisions import (
    DECISION_IDENTITY, MAX_EXPANDED_DECISIONS, multi_resolution_decisions,
)
from research.causal_strategy_features.v7.ranking import (
    ParticipationRankingPolicy, opportunity_scores,
)
from src.backend.backtest_native_channel_store import load_declared_native_channels
from src.market_engine.native_causal_channel_contract import NativeChannelRequest


class PreparedParticipationScores(NamedTuple):
    scores: np.ndarray
    available: np.ndarray
    mandatory_eligible: np.ndarray


def prepare_installed_participation_scores(client, request, feature_attempt_id,
        decisions, mandatory_eligible, *, policy, producer_source_hash, authority):
    """Load one declared packet and score columnarly before sequential admission.

    Caller must independently certify the consuming immutable release. This
    adapter does not reorder candidates, veto missing scores, grant cash or
    replace mandatory eligibility. Unknown scores are NaN with a separate mask.
    Returned arrays have immutable bytes ownership and preserve input order.
    """
    if (type(request) is not NativeChannelRequest or
            type(policy) is not ParticipationRankingPolicy or
            type(decisions) is not pl.DataFrame):
        raise ValueError('Exact declared native ranking inputs required')
    request.__post_init__()
    policy.__post_init__()
    if any(r not in request.policy.resolutions_ms for r, _ in policy.resolution_weights):
        raise ValueError('Ranking resolution differs from declared native inputs')
    if (type(mandatory_eligible) is not np.ndarray or
            mandatory_eligible.dtype != np.dtype(bool) or
            mandatory_eligible.shape != (decisions.height,)):
        raise ValueError('Exact original Boolean eligibility mask required')
    if (decisions.height > policy.max_rows or
            decisions.height * len(request.policy.resolutions_ms) > MAX_EXPANDED_DECISIONS):
        raise ValueError('Native ranking decision packet exceeds declared bound')
    required = dict.fromkeys(DECISION_IDENTITY, pl.String)
    required['decision_day_ms'] = pl.Int64
    if any(decisions.schema.get(k) != v for k, v in required.items()):
        raise ValueError('Native ranking decision schema differs')
    left = decisions.select(*DECISION_IDENTITY, 'decision_day_ms')
    if (any(left[c].null_count() for c in left.columns) or
            left.n_unique() != left.height or
            left.filter((pl.col('decision_day_ms') < 0) |
                (pl.col('decision_day_ms') > request.through_day_boundary_ms) |
                (pl.col('decision_day_ms') % request.policy.decision_interval_ms != 0)).height):
        raise ValueError('Native ranking identity or declared clock differs')
    mandatory = np.frombuffer(mandatory_eligible.tobytes(), dtype=bool)
    packet = load_declared_native_channels(client, request, feature_attempt_id,
        producer_source_hash=producer_source_hash, authority=authority)
    scope = pl.from_arrow(packet.coverage).select(*DECISION_IDENTITY).unique()
    if left.join(scope, on=list(DECISION_IDENTITY), how='anti').height:
        raise ValueError('Ranking decision is outside installed native coverage')
    aligned = multi_resolution_decisions(pl.from_arrow(packet.rows), left,
        decision_interval_ms=request.policy.decision_interval_ms,
        freshness_by_resolution=request.policy.freshness_by_resolution)
    scored = opportunity_scores(aligned, policy=policy)
    available = scored['score_available'].to_numpy() & mandatory
    scores = scored['opportunity_score'].fill_null(float('nan')).to_numpy().copy()
    scores[~available] = float('nan')
    return PreparedParticipationScores(
        np.frombuffer(scores.astype(np.float64).tobytes(), dtype=np.float64),
        np.frombuffer(available.tobytes(), dtype=bool), mandatory)
