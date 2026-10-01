"""Pure candidate profit protection from completed producer and manager facts.

Release registration, normalized witness persistence and order submission are
separate authorities. This module performs no reads, indicator work or writes.
"""
from dataclasses import dataclass
from decimal import Decimal
from math import isfinite

from .strategy_followthrough_failure import FollowThroughFailureInput, followthrough_failure

POLICY_ID = 'strategy-thirty-one-original-risk-profit-giveback-v1'


@dataclass(frozen=True, slots=True)
class ProfitGivebackInput:
    completed: FollowThroughFailureInput
    prior_high_int: int
    prior_high_through_boundary_ms: int


@dataclass(frozen=True, slots=True)
class ProfitGivebackWitness:
    """Persist every predicate fact separately from the inherited loss witness."""
    boundary_ms: int
    first_held_boundary_ms: int
    reference_ask: float
    initial_stop: float
    completed_close_int: int
    macd_line: float
    macd_signal: float
    bid: float
    ask: float
    quote_age_us: int
    prior_high_int: int
    prior_high_through_boundary_ms: int


def profit_giveback_policy_payload() -> dict:
    return {
        'policy_id': POLICY_ID,
        'arm': 'prior_completed_100ms_position_high >= original_ask + original_risk',
        'floor': 'original_ask + original_risk / 2',
        'original_risk': 'original_ask - original_initial_stop',
        'momentum': 'completed_5s_macd_line < completed_5s_macd_signal',
        'price': 'completed_5s_close_and_fresh_current_bid <= floor',
        'high_authority': 'existing_position_high_before_current_manager_bucket',
        'first_fill_bucket': 'excluded',
        'current_bucket_arming': False,
        'quote_max_age_us': 1_000_000,
        'pending_exit': 'no_duplicate_exit',
        'time_alone': 'never_exits',
        'missing': 'no_synthetic_observations',
        'parent_failure_policy': 'retain_strategy30',
    }


def profit_giveback(value: ProfitGivebackInput) -> ProfitGivebackWitness | None:
    """One original risk reached, half-risk gain given back, momentum negative.

    The high is the existing accumulator as of a strictly earlier manager
    boundary. The caller must capture it before consuming the current 100ms
    bar, rather than passing the accumulator after its current update.
    """
    if type(value) is not ProfitGivebackInput:
        raise ValueError('Profit protection needs exact typed input')
    x = value.completed
    # Reuse causal position/type validation. The returned parent loss signal
    # does not select or replace this candidate's different profit threshold.
    followthrough_failure(x)
    if (type(value.prior_high_int) is not int or value.prior_high_int <= 0
            or type(value.prior_high_through_boundary_ms) is not int
            or value.prior_high_through_boundary_ms % 100
            or not x.first_held_boundary_ms <= value.prior_high_through_boundary_ms < x.boundary_ms):
        raise ValueError('Profit protection high lacks prior causal manager authority')
    if x.position_quantity == 0 or x.pending_exit or x.boundary_ms % 5000:
        return None
    if (x.completed_five_second_boundary_ms != x.boundary_ms
            or x.boundary_ms - 5000 < x.first_held_boundary_ms or not x.price_valid
            or type(x.completed_five_second_close_int) is not int or x.completed_five_second_close_int <= 0
            or any(type(v) not in (int, float) or not isfinite(v)
                   for v in (x.macd_line, x.macd_signal, x.bid, x.ask))
            or not 0 < x.bid <= x.ask
            or type(x.quote_age_us) is not int or not 0 <= x.quote_age_us <= 1_000_000):
        return None
    # Producer prices are decimal integers. Decimal conversion avoids a
    # binary-float subtraction rejecting a price exactly on the threshold.
    reference = Decimal(str(x.reference_ask))
    risk = reference - Decimal(str(x.initial_stop))
    arm, floor = reference + risk, reference + risk / 2
    if (Decimal(value.prior_high_int) < arm * 10000
            or Decimal(x.completed_five_second_close_int) > floor * 10000
            or Decimal(str(x.bid)) > floor or x.macd_line >= x.macd_signal):
        return None
    return ProfitGivebackWitness(
        x.boundary_ms, x.first_held_boundary_ms, x.reference_ask, x.initial_stop,
        x.completed_five_second_close_int, float(x.macd_line), float(x.macd_signal),
        float(x.bid), float(x.ask), x.quote_age_us,
        value.prior_high_int, value.prior_high_through_boundary_ms)
