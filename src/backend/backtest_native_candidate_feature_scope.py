"""Full certified candidate identities for producer-owned native features.

This bridge supplies dependency scope, never eligibility or order authority.
It consumes every parent candidate, including rejected candidates, without a
research cohort, labels, trade outcomes or a feature-availability filter.
"""
import numpy as np
import polars as pl
from dataclasses import dataclass

from .backtest_declared_native_fixed_plan import DeclaredEntrySourcePlan
from .backtest_market_data import SESSION_OPEN_OFFSET_MS
from research.causal_strategy_features.v5.decisions import DECISION_IDENTITY
from research.causal_strategy_features.v7.ranking import ParticipationRankingPolicy
from src.market_engine.native_causal_channel_contract import (
    NativeChannelRequest, require_hash, require_uuid,
)
from src.market_engine.native_channel_insert_authority import NativeChannelInsertAuthority
from .backtest_native_participation_ranking import (
    PreparedParticipationScores, prepare_installed_participation_scores,
)


@dataclass(frozen=True, slots=True)
class DeclaredParticipationRead:
    request: NativeChannelRequest
    feature_attempt_id: str
    producer_source_hash: str
    authority: NativeChannelInsertAuthority

    def __post_init__(self):
        if (type(self.request) is not NativeChannelRequest
                or type(self.authority) is not NativeChannelInsertAuthority):
            raise ValueError('Exact native request and installed feature fence required')
        self.request.__post_init__()
        require_uuid(self.feature_attempt_id)
        require_hash(self.producer_source_hash)


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


def prepare_declared_native_participation_scores(client, source, reads, *, policy):
    """Read installed packets once and restore exact full parent candidate order.

    All dependencies and complete disjoint candidate coverage are checked before
    any feature SELECT. Missing scores retain NaN and an availability mask;
    they never remove a candidate. This is preparation, not selected-release,
    execution, ranking, Portfolio or OMS admission authority.
    """
    if type(policy) is not ParticipationRankingPolicy:
        raise ValueError('Exact declared participation ranking policy required')
    policy.__post_init__()
    decisions = declared_candidate_feature_decisions(source, max_rows=policy.max_rows)
    if (type(reads) is not tuple or not reads or len(reads) > policy.max_rows
            or any(type(read) is not DeclaredParticipationRead for read in reads)):
        raise ValueError('Bounded complete declared native feature packet roster required')
    mandatory = np.frombuffer(source.eligible_mask.tobytes(), dtype=bool)
    source_token = source.token
    keys = decisions.with_row_index('_source_index')
    expected_tickers = set(decisions['ticker'])
    covered = set()
    packets = []
    for read in reads:
        read.__post_init__()
        request = read.request
        tickers = set(request.tickers)
        if (request.market != source.parent.market
                or request.policy != reads[0].request.policy
                or read.producer_source_hash != reads[0].producer_source_hash
                or request.session_date != source.parent.market.sessions[0]
                or request.policy.decision_interval_ms != int(source.parent.capabilities.execution_interval[:-2])
                or tickers & covered or not tickers.issubset(expected_tickers)):
            raise ValueError('Native feature packet crosses or repeats certified candidate scope')
        selected = keys.filter(pl.col('ticker').is_in(request.tickers))
        if (selected['decision_day_ms'].max() > request.through_day_boundary_ms
                or any(request.unit(ticker).attempt_id != attempt for ticker, attempt in
                       selected.select('ticker', 'attempt_id').unique().iter_rows())):
            raise ValueError('Native feature packet horizon or bars attempt differs')
        covered.update(tickers)
        packets.append((read, selected))
    if covered != expected_tickers:
        raise ValueError('Native feature packets omit certified candidate tickers')
    scores = np.full(decisions.height, np.nan, dtype=np.float64)
    available = np.zeros(decisions.height, dtype=bool)
    for read, selected in packets:
        indexes = selected['_source_index'].to_numpy()
        result = prepare_installed_participation_scores(client, read.request,
            read.feature_attempt_id, selected.drop('_source_index'), mandatory[indexes],
            policy=policy, producer_source_hash=read.producer_source_hash,
            authority=read.authority)
        scores[indexes], available[indexes] = result.scores, result.available
    # A transport callback cannot silently mutate the parent during preparation.
    source.__post_init__()
    if (source.token != source_token or not np.array_equal(source.eligible_mask, mandatory)
            or not declared_candidate_feature_decisions(source, max_rows=policy.max_rows).equals(decisions)):
        raise ValueError('Parent eligibility changed during native feature preparation')
    return PreparedParticipationScores(np.frombuffer(scores.tobytes(), dtype=np.float64),
        np.frombuffer(available.tobytes(), dtype=bool), mandatory)
