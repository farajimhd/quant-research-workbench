"""Score the already installed session once, without additional source reads."""
import numpy as np
import polars as pl

from .backtest_native_channel_session_source import PreparedNativeChannelSession
from .backtest_native_participation_ranking import PreparedParticipationScores
from research.causal_strategy_features.v5.decisions import (
    DECISION_IDENTITY, MAX_EXPANDED_DECISIONS, multi_resolution_decisions,
)
from research.causal_strategy_features.v7.ranking import ParticipationRankingPolicy, opportunity_scores


def score_prepared_native_session(session, decisions, mandatory, *, policy):
    if (type(session) is not PreparedNativeChannelSession
            or type(decisions) is not pl.DataFrame
            or type(policy) is not ParticipationRankingPolicy):
        raise ValueError('Exact prepared native session, decisions and scoring policy required')
    session.__post_init__()
    policy.__post_init__()
    required = dict.fromkeys(DECISION_IDENTITY, pl.String)
    required['decision_day_ms'] = pl.Int64
    if (any(decisions.schema.get(k) != v for k, v in required.items())
            or decisions.height > policy.max_rows
            or decisions.select(*required).n_unique() != decisions.height
            or any(decisions[k].null_count() for k in required)
            or type(mandatory) is not np.ndarray or mandatory.dtype != np.dtype(bool)
            or mandatory.shape != (decisions.height,)):
        raise ValueError('Complete bounded decisions and Boolean admission mask required')
    if not set(decisions['ticker']).issubset(session.tickers):
        raise ValueError('Prepared session omits native candidate tickers')
    mandatory = np.frombuffer(mandatory.tobytes(), dtype=bool)
    scores = np.full(decisions.height, np.nan, dtype=np.float64)
    available = np.zeros(decisions.height, dtype=bool)
    covered = np.zeros(decisions.height, dtype=bool)
    keys = decisions.select(*required).with_row_index('_index')
    for sequence in session.sequences:
        request = sequence.packet.request
        inputs = request.policy
        selected = keys.filter(pl.col('ticker').is_in(request.tickers))
        if selected.is_empty():
            continue
        indexes = selected['_index'].to_numpy()
        if (np.any(covered[indexes])
                or selected.height * len(inputs.resolutions_ms) > MAX_EXPANDED_DECISIONS
                or any(r not in inputs.resolutions_ms for r, _ in policy.resolution_weights)
                or selected.filter((pl.col('decision_day_ms') < 0)
                    | (pl.col('decision_day_ms') > request.through_day_boundary_ms)
                    | (pl.col('decision_day_ms') % inputs.decision_interval_ms != 0)).height):
            raise ValueError('Prepared score packet repeats scope or differs from declared inputs')
        scope = pl.from_arrow(sequence.packet.coverage).select(*DECISION_IDENTITY).unique()
        if selected.join(scope, on=list(DECISION_IDENTITY), how='anti').height:
            raise ValueError('Native decisions differ from installed feature identities')
        aligned = multi_resolution_decisions(pl.from_arrow(sequence.packet.rows),
            selected.drop('_index'), decision_interval_ms=inputs.decision_interval_ms,
            freshness_by_resolution=inputs.freshness_by_resolution)
        result = opportunity_scores(aligned, policy=policy)
        known = result['score_available'].to_numpy() & mandatory[indexes]
        values = result['opportunity_score'].fill_null(float('nan')).to_numpy().copy()
        values[~known] = np.nan
        scores[indexes], available[indexes], covered[indexes] = values, known, True
    if not covered.all():
        raise ValueError('Prepared scoring omits native candidate keys')
    return PreparedParticipationScores(np.frombuffer(scores.tobytes(), dtype=np.float64),
        np.frombuffer(available.tobytes(), dtype=bool), mandatory)
