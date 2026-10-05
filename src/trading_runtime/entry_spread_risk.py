"""Declared entry friction over canonical prices; no IO or financial sizing."""
from dataclasses import dataclass
from decimal import Decimal
from math import gcd
import numpy as np
from datetime import datetime, timezone


def exact_epoch_us(instant):
    if type(instant) is not datetime or instant.tzinfo is None:
        raise ValueError('Entry cost clock must be timezone aware')
    delta = instant.astimezone(timezone.utc) - datetime(1970, 1, 1, tzinfo=timezone.utc)
    return ((delta.days * 86400 + delta.seconds) * 1_000_000 + delta.microseconds)


@dataclass(frozen=True, slots=True)
class EntrySpreadRiskPolicy:
    policy_id: str
    maximum_spread_original_risk: tuple[int, int]

    def __post_init__(self):
        ratio = self.maximum_spread_original_risk
        if (type(self.policy_id) is not str or not self.policy_id
                or type(ratio) is not tuple or len(ratio) != 2
                or any(type(v) is not int or not 0 < v <= 1024 for v in ratio)
                or ratio[0] > ratio[1] or gcd(*ratio) != 1):
            raise ValueError('Entry spread risk needs a canonical declared ratio')

    def payload(self):
        return dict(policy_id=self.policy_id,
                    maximum_spread_original_risk=list(self.maximum_spread_original_risk),
                    price_unit='canonical_1/10000', equality_allowed=True,
                    maximum_quote_age_us=1_000_000,
                    unavailable_quote='reject_candidate', missing_source='fail_certification',
                    reference='current_original_proposal_ask', stop='original_requested_stop',
                    scope='entry_and_reentry_before_portfolio',
                    anchors='unchanged_full_first_setup_price_activity_episode_veto',
                    comparison='denominator*(ask_int-bid_int)<=numerator*(ask_int-original_stop_int)')


def canonical_price_int(value):
    if type(value) not in (int, float, Decimal):
        raise ValueError('Entry cost price must be canonical numeric')
    number = Decimal(str(value)) * 10_000
    if not number.is_finite() or number <= 0 or number % 1:
        raise ValueError('Entry cost price is outside canonical 1/10000 grid')
    return int(number)


def entry_spread_risk_mask(policy, bid_int, ask_int, original_stop_int):
    if type(policy) is not EntrySpreadRiskPolicy:
        raise ValueError('Entry cost needs an exact declared policy')
    arrays = tuple(np.asarray(v) for v in (bid_int, ask_int, original_stop_int))
    bid, ask, stop = arrays
    if (ask.ndim != 1 or any(v.shape != ask.shape or v.dtype != np.int64 for v in arrays)
            or any(np.any(v < 0) or np.any(v > np.iinfo(np.int64).max // 1024) for v in arrays)):
        raise ValueError('Entry cost needs aligned bounded canonical integer columns')
    numerator, denominator = policy.maximum_spread_original_risk
    return ((stop > 0) & (stop < bid) & (bid <= ask)
            & (denominator * (ask - bid) <= numerator * (ask - stop)))


def entry_spread_risk_allowed(policy, *, bid_int, ask_int, original_stop_int):
    if any(type(v) is not int for v in (bid_int, ask_int, original_stop_int)):
        raise ValueError('Entry cost scalar needs exact canonical integers')
    return bool(entry_spread_risk_mask(policy, *(
        np.asarray([v], dtype=np.int64) for v in (bid_int, ask_int, original_stop_int)))[0])
