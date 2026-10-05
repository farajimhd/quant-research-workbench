"""Optional declared armed profit floor; completed native facts only, no IO."""
from dataclasses import dataclass
from decimal import Decimal
from math import isfinite

from .strategy_profit_giveback import ProfitGivebackInput, ProfitGivebackWitness, profit_giveback


@dataclass(frozen=True, slots=True)
class ArmedProfitFloorPolicy:
    policy_id: str
    gain_fraction: tuple[int, int]
    maximum_signal_reference_fraction: tuple[int, int]

    def __post_init__(self):
        if type(self.policy_id) is not str or not self.policy_id.strip():
            raise ValueError('Armed floor needs exact policy identity')
        for value in (self.gain_fraction, self.maximum_signal_reference_fraction):
            if (type(value) is not tuple or len(value) != 2
                    or any(type(v) is not int or v <= 0 for v in value)
                    or value[0] >= value[1]):
                raise ValueError('Armed floor needs strict positive proper fractions')

    def payload(self):
        return dict(policy_id=self.policy_id, gain_fraction=self.gain_fraction,
            maximum_signal_reference_fraction=self.maximum_signal_reference_fraction,
            arm='existing_confirmed_native_1R_checkpoint_strictly_before_current_boundary',
            resolution_ms=5000, wholly_post_first_held=True,
            momentum='completed_macd_line < signal and 0 <= signal <= original_ask * maximum_signal_reference_fraction',
            price='completed_close_and_fresh_bid <= original_ask + original_risk * gain_fraction',
            quote_max_age_us=1000000, pending_exit='no_duplicate_exit',
            precedence='after_all_inherited_exits', time_alone='never_exits',
            missing='no_synthetic_observations')


def armed_profit_floor(value: ProfitGivebackInput, *, policy: ArmedProfitFloorPolicy):
    """Extension only; reuse original validation without changing its predicate."""
    if type(policy) is not ArmedProfitFloorPolicy:
        raise ValueError('Armed floor requires exact declared policy')
    # The inherited rule performs shared strict scalar and causal high validation.
    profit_giveback(value)
    x = value.completed
    if (not x.position_quantity or x.pending_exit or x.boundary_ms % 5000
            or x.completed_five_second_boundary_ms != x.boundary_ms
            or x.boundary_ms - 5000 < x.first_held_boundary_ms or not x.price_valid
            or type(x.completed_five_second_close_int) is not int or x.completed_five_second_close_int <= 0
            or any(type(v) not in (int, float) or not isfinite(v)
                   for v in (x.macd_line, x.macd_signal, x.bid, x.ask))
            or not 0 < x.bid <= x.ask
            or type(x.quote_age_us) is not int or not 0 <= x.quote_age_us <= 1000000):
        return None
    reference = Decimal(str(x.reference_ask))
    risk = reference - Decimal(str(x.initial_stop))
    floor = reference + risk * Decimal(policy.gain_fraction[0]) / policy.gain_fraction[1]
    maximum_signal = reference * Decimal(policy.maximum_signal_reference_fraction[0]) / policy.maximum_signal_reference_fraction[1]
    if (Decimal(value.prior_high_int) < (reference + risk) * 10000
            or Decimal(x.completed_five_second_close_int) > floor * 10000
            or Decimal(str(x.bid)) > floor or x.macd_line >= x.macd_signal
            or not Decimal(0) <= Decimal(str(x.macd_signal)) <= maximum_signal):
        return None
    return ProfitGivebackWitness(x.boundary_ms, x.first_held_boundary_ms,
        x.reference_ask, x.initial_stop, x.completed_five_second_close_int,
        float(x.macd_line), float(x.macd_signal), float(x.bid), float(x.ask),
        x.quote_age_us, value.prior_high_int, value.prior_high_through_boundary_ms)


def declared_profit_giveback(value, *, strategy_number):
    """Exact numbered contract selects optional extension for replay and sealing."""
    from .numbered_fixed_strategy import numbered_fixed_strategy
    contract = numbered_fixed_strategy(strategy_number)
    inherited = profit_giveback(value)
    if inherited is not None:
        return inherited
    policy = getattr(contract, 'armed_profit_floor_policy', None)
    return armed_profit_floor(value, policy=policy) if policy is not None else None
