"""Prepared immutable Strategy 34 declaration; executor/publication remain closed."""
from copy import deepcopy

from .strategy_registry import NumberedStrategyRelease
from . import strategy_thirty_three_release as parent_policy

PARENT_REVISION_ID = 'strategy-one-33:66f5cdae-3cbb-4fdd-af99-a72d22e77e6c'
PARENT_PAYLOAD_HASH = '1dcf2e4d52de04e710880fc1be221ae68fafb4ed5a441691edd183e8c092815b'
INHERITED_POLICIES = deepcopy(parent_policy.INHERITED_POLICIES)
PROFIT_PROTECTION_POLICY = deepcopy(parent_policy.PROFIT_PROTECTION_POLICY)
CONFIRMED_AH_FAILURE_POLICY = {
    'policy_id': 'strategy.confirmed-ah-risk-failure.v1',
    'eligible_session': 'afterhours',
    'held_start_authority': 'native_first_held_boundary',
    'maximum_held_age_ms': 60_000,
    'maximum_held_age_inclusive': True,
    'loss_threshold': 'original_ask_minus_one_quarter_original_stop_distance',
    'price_confirmation': 'completed_5s_close_and_fresh_bid_at_or_below_threshold',
    'momentum_confirmation': 'completed_5s_and_completed_10s_macd_line_below_signal',
    'whole_held_candles': True,
    'maximum_10s_observation_age_ms_exclusive': 10_000,
    'maximum_quote_age_us_inclusive': 1_000_000,
    'pending_exit': 'reject',
    'missing_or_invalid_observation': 'reject',
    'exit_priority': 'inherited_failure_then_inherited_profit_then_confirmed_ah_failure',
    'native_witness_contract': 'trading_confirmed_ah_failure_v4',
}
BEHAVIOR = (
    'Strategy 34 inherits the exact pinned Strategy 33 entries, inputs, sizing, '
    'costs, structural protection, failure and profit exits. After inherited '
    'exits, an additional AH first-minute exit requires a completed whole-held '
    '5s close and fresh bid at or below entry minus one quarter original risk, '
    'with bearish completed whole-held 5s and latest 10s MACD. Missing, forming '
    'or stale observations and pending exits reject the additional rule. '
    'A separate complete normalized witness preserves both producer timeframes. '
    'V7 prior-day checkpoint and regular-session warm-up for after-hours remain '
    'inherited. Backtest only; public resume and live execution remain closed.'
)


def release_contract() -> NumberedStrategyRelease:
    """Seal declaration content; this does not install an executable revision."""
    parent = parent_policy.release_contract()
    values = dict(
        number=34, executor_strategy_id=parent.executor_strategy_id,
        executor_revision=34, evaluation_interval=parent.evaluation_interval,
        input_contracts=parent.input_contracts,
        rule_set_contracts=parent.rule_set_contracts + (CONFIRMED_AH_FAILURE_POLICY['policy_id'],),
        behavior_specification=BEHAVIOR,
    )
    draft = NumberedStrategyRelease(**values, approved_digest='')
    release = NumberedStrategyRelease(**values, approved_digest=draft.digest())
    release.verify()
    return release
