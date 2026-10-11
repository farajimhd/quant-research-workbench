"""One-time certified candidate ordering, with no financial admission authority."""
from dataclasses import dataclass
from types import MappingProxyType

import numpy as np
import polars as pl

from .backtest_native_candidate_feature_scope import declared_candidate_feature_decisions
from .backtest_native_participation_ranking import PreparedParticipationScores
from .backtest_market_data import SESSION_OPEN_OFFSET_MS
from src.trading_runtime.participation_acquisition_policy import ParticipationAcquisitionPolicy


@dataclass(frozen=True, slots=True)
class PreparedAcquisitionOrder:
    source: object
    source_token: str
    policy: ParticipationAcquisitionPolicy
    ranks: object

    def rank(self, ticker, boundary_ms):
        try:
            return self.ranks[(ticker, boundary_ms)]
        except KeyError as exc:
            raise ValueError('Acquisition candidate is outside the prepared certified population') from exc


def prepare_participation_acquisition_order(source, scores, *, policy):
    """Keep every source key; score availability never creates eligibility.

    Filtering and ordering use Polars once before replay. The bounded final
    mapping only serves constant-time sequential dispatch; it performs no
    source queries, feature calculation, sizing or cash reservations.
    """
    if type(policy) is not ParticipationAcquisitionPolicy or type(scores) is not PreparedParticipationScores:
        raise ValueError('Exact declared policy and prepared scores required')
    policy.__post_init__()
    rows = declared_candidate_feature_decisions(source, max_rows=policy.scoring.max_rows)
    for values, dtype in ((scores.scores, np.float64), (scores.available, bool),
                          (scores.mandatory_eligible, bool)):
        if (type(values) is not np.ndarray or values.dtype != np.dtype(dtype)
                or values.shape != (rows.height,) or values.flags.writeable):
            raise ValueError('Prepared acquisition scores require immutable complete arrays')
    if (not np.array_equal(scores.mandatory_eligible, source.eligible_mask)
            or np.any(scores.available & ~scores.mandatory_eligible)
            or np.any(scores.available & ~np.isfinite(scores.scores))
            or np.any(~scores.available & ~np.isnan(scores.scores))):
        raise ValueError('Prepared score availability or mandatory admission differs')
    ranked = rows.select('ticker', 'decision_day_ms').with_columns(
        pl.Series('_available', scores.available),
        pl.Series('_score', scores.scores).fill_nan(None),
    ).sort(['decision_day_ms', '_available', '_score', 'ticker'],
           descending=[False, True, True, False], nulls_last=True)
    if ranked.select('ticker', 'decision_day_ms').n_unique() != rows.height:
        raise ValueError('Candidate order contains repeated ticker clocks')
    ranks = MappingProxyType({(ticker, day_ms - SESSION_OPEN_OFFSET_MS): index
        for index, (ticker, day_ms) in enumerate(
            ranked.select('ticker', 'decision_day_ms').iter_rows())})
    return PreparedAcquisitionOrder(source, source.token, policy, ranks)
