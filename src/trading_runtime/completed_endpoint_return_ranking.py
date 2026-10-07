"""Declared columnar opportunity order after mandatory native eligibility.

No broker, acquisition, cash, certification or strategy MACD condition is changed.
The coordinator must apply this only after all broker events at the boundary.
"""
from dataclasses import dataclass
from enum import Enum

import numpy as np
import polars as pl

from src.market_engine.completed_endpoint_return_contract import CompletedReturnProjection


class ReturnQualification(str, Enum):
    POSITIVE_RETURN_REQUIRED = 'positive_return_required'
    OPTIONAL_RETURN_RANK = 'optional_return_rank'


@dataclass(frozen=True, slots=True)
class CompletedReturnRankingPolicy:
    qualification: ReturnQualification

    def __post_init__(self):
        if type(self.qualification) is not ReturnQualification:
            raise ValueError('Ranking requires declared typed qualification')


def rank_completed_candidates(projection, mandatory_eligible, policy):
    """Return original row indexes ordered within each completed clock.

    Mandatory native admission/VWAP/quotes/liquidity/structural eligibility must
    be provided explicitly. Optional missingness is unranked last only in the
    separately declared OPTIONAL_RETURN_RANK alternative; never a fabricated 0.
    Sequential Portfolio/OMS remain the only acquisition/execution authority.
    """
    if type(projection) is not CompletedReturnProjection or type(policy) is not CompletedReturnRankingPolicy:
        raise ValueError('Ranking lacks certified projection or declared policy')
    projection.__post_init__()
    policy.__post_init__()
    if (type(mandatory_eligible) is not np.ndarray or mandatory_eligible.dtype != np.dtype(bool)
            or mandatory_eligible.shape != (projection.rows.num_rows,)):
        raise ValueError('Ranking needs explicit Boolean mandatory eligibility per source key')
    rows = pl.from_arrow(projection.rows).with_row_index('source_row').with_columns(
        pl.Series('mandatory_eligible', mandatory_eligible))
    rows = rows.filter(pl.col('mandatory_eligible'))
    if policy.qualification is ReturnQualification.POSITIVE_RETURN_REQUIRED:
        rows = rows.filter((pl.col('return_available') == 1) & (pl.col('return5') > 0))
    return rows.sort(['decision_boundary_ms', 'return_available', 'price_scaled_momentum', 'ticker'],
        descending=[False, True, True, False], nulls_last=True)['source_row'].to_numpy()
