"""Explicit installed capabilities for sealed numbered fixed Backtests.

This registry is not release approval. The configuration reader must verify
the immutable publication before selecting a contract. No live/event route
may use these capabilities.
"""
from dataclasses import dataclass
from types import MappingProxyType

from .strategy_one_contract import STRATEGY_ID


DECLARED_FIXED_ADAPTER = 'declared-numbered-fixed-policy-adapter@1'


def declared_fixed_rule(number: int, rule_id: str | None = None) -> bool:
    """Recognize a separately sealed declaration; numeric identity selects no rule."""
    if type(number) is not int or (rule_id is not None and type(rule_id) is not str):
        return False
    from .strategy_registry import numbered_strategy
    try:
        release = numbered_strategy(number)
    except ValueError as exc:
        if str(exc) == f'Strategy {number} is not published':
            return False
        raise
    count = release.input_contracts.count(DECLARED_FIXED_ADAPTER)
    if count == 0:
        return False
    if count != 1:
        raise ValueError('Declared fixed adapter input is duplicated')
    return rule_id is None or rule_id in release.rule_set_contracts


def declared_fixed_exit_reason(reason: str, rule_id: str) -> bool:
    """Recognize only an exact factory reason backed by a sealed semantic rule.

    Parent intent rows carry no numbered identity. Recognition is independent
    of witness presence so missing companions cannot disappear from coverage.
    """
    if type(reason) is not str or type(rule_id) is not str:
        return False
    import re
    match = re.fullmatch(
        r'strategy_([1-9][0-9]{0,9})_(profit_giveback|confirmed_ah_failure|liquidity_fade_failure)',
        reason, flags=re.ASCII)
    if match is None:
        return False
    supported = {
        'strategy-thirty-one-original-risk-profit-giveback-v1',
        'strategy.confirmed-ah-risk-failure.v1',
        'strategy-thirty-five-completed-liquidity-fade-v1',
    }
    if rule_id not in supported:
        return False
    number = int(match.group(1))
    if number > 2**32 - 1:
        return False
    if not declared_fixed_rule(number, rule_id):
        return False
    if rule_id == 'strategy-thirty-one-original-risk-profit-giveback-v1':
        from .strategy_profit_giveback_exit import profit_giveback_reason
        expected = profit_giveback_reason(number)
    elif rule_id == 'strategy.confirmed-ah-risk-failure.v1':
        from .strategy_confirmed_ah_failure_exit import confirmed_ah_reason
        expected = confirmed_ah_reason(number)
    else:
        from .strategy_liquidity_fade_exit import liquidity_fade_reason
        expected = liquidity_fade_reason(number)
    return reason == expected


SESSION_POLICY = MappingProxyType({
    "timezone": "America/New_York",
    "windows": (
        MappingProxyType({"start": "04:00", "entry_cutoff": "09:25", "liquidation_start": "09:29", "end": "09:30"}),
        MappingProxyType({"start": "16:00", "entry_cutoff": "19:50", "liquidation_start": "19:55", "end": "20:00"}),
    ),
    "cancel_pending_acquisition_at_cutoff": True,
    "fills": "later_certified_liquidity_only",
    "residual_at_end": "fail",
})


def session_policy_payload() -> dict:
    """Return a mutable JSON projection without exposing installed policy."""
    return {**SESSION_POLICY, "windows": [dict(window) for window in SESSION_POLICY["windows"]]}


def activation_policy_payload() -> dict:
    """Strategy 3 entry-origin policy; held-position management is unchanged."""
    return {
        "clock": "milliseconds_since_04:00_America/New_York",
        "premarket_open_ms": 0,
        "afterhours_open_ms": 43_200_000,
        "comparison": "current_session_open_ms < episode_start_ms <= boundary_ms",
        "scope": "new_entry",
    }


def add_policy_payload() -> dict:
    """Strategy 4 ablates only additional position purchases."""
    return {"allows_adds": False, "scope": "disable_adds_only"}


def trailing_policy_payload() -> dict:
    """Strategy 5 retains entry protection and structural stop ratchets."""
    return {"initial_stop": "completed_30s_bar_low", "completed_30s_low_trailing": False,
            "three_resistance_step_stop": True, "scope": "disable_subsequent_30s_low_trailing_only"}


def restored_trailing_policy_payload() -> dict:
    """Strategy 7 restores the completed-low trailing branch with a frozen target."""
    return {"initial_stop": "completed_30s_bar_low", "completed_30s_low_trailing": True,
            "three_resistance_step_stop": True, "scope": "restore_subsequent_30s_low_trailing_only"}


def target_policy_payload() -> dict:
    """Strategy 6 retains the initial full-position profit target."""
    return {"initial_target": "ordinal_resistance_target", "target_escalation": False,
            "scope": "retain_initial_target_only"}


def entry_price_policy_payload() -> dict:
    """Strategy 8 caps acquisition at the proposal ask without changing persistence."""
    return {"maximum_buy_price": "proposal_reference_ask", "scope": "entry_and_reentry_only",
            "persist_until_cancelled": True, "partial_fill_policy": "complete_remainder"}


def followthrough_policy_payload() -> dict:
    """Strategy 9 consumes a complete post-fill five-second failure witness."""
    return {"resolution_ms": 5000, "loss_fraction_of_initial_stop_distance": 0.5,
            "entry_reference": "original_proposal_ask", "momentum": "macd_line < macd_signal",
            "price": "completed_close_and_fresh_bid <= (reference_ask + initial_stop) / 2",
            "first_bucket": "whole_bar_after_first_held_boundary", "maximum_quote_age_us": 1_000_000,
            "missing_input": "skip_current_proposal", "scope": "held_position_exit",
            "fill": "later_certified_liquidity_only"}


@dataclass(frozen=True, slots=True)
class NumberedFixedStrategyContract:
    strategy_number: int
    strategy_id: str = STRATEGY_ID
    execution_interval: str = "100ms"

    @property
    def drawdown_measure_policy(self):
        # The original Strategy1 has no NumberedStrategyRelease manifest.
        if self.strategy_number == 1:
            return None
        from .strategy_registry import numbered_strategy
        from .drawdown_measure_authority import drawdown_policy_from_release
        return drawdown_policy_from_release(numbered_strategy(self.strategy_number))

    @property
    def entry_momentum_growth_policy(self):
        # Strategy 1 is the original unnumbered-release baseline.
        if self.strategy_number == 1:
            return None
        from .strategy_registry import numbered_strategy
        release = numbered_strategy(self.strategy_number)
        ids = tuple(v for v in release.rule_set_contracts if v.startswith('entry-momentum-first-'))
        if not ids:
            return None
        if len(ids) != 1:
            raise ValueError('Momentum contract requires one declared growth policy')
        from .entry_momentum_growth import EntryMomentumGrowthPolicy
        import re
        matched = re.fullmatch(r'entry-momentum-first-(5|10)-current-(5|10)-percent@1', ids[0])
        if matched is None or 'declared-first-current-momentum-source@1' not in release.input_contracts:
            raise ValueError('Momentum declaration lacks exact producer input contract')
        return EntryMomentumGrowthPolicy(ids[0], (1, 100//int(matched[1])), (1, 100//int(matched[2])))

    @property
    def entry_spread_risk_policy(self):
        if self.strategy_number == 53:
            from .strategy_fifty_three_release import ENTRY_SPREAD_RISK_POLICY
            return ENTRY_SPREAD_RISK_POLICY
        if self.strategy_number == 54:
            from .strategy_fifty_four_release import ENTRY_SPREAD_RISK_POLICY
            return ENTRY_SPREAD_RISK_POLICY
        if self.strategy_number == 57:
            from .strategy_fifty_seven_release import ENTRY_SPREAD_RISK_POLICY
            return ENTRY_SPREAD_RISK_POLICY
        if self.strategy_number == 55:
            from .strategy_fifty_five_release import ENTRY_SPREAD_RISK_POLICY
            return ENTRY_SPREAD_RISK_POLICY
        if self.strategy_number == 58:
            from .strategy_fifty_eight_release import ENTRY_SPREAD_RISK_POLICY
            return ENTRY_SPREAD_RISK_POLICY
        if self.strategy_number == 56:
            from .strategy_fifty_six_release import ENTRY_SPREAD_RISK_POLICY
            return ENTRY_SPREAD_RISK_POLICY
        return None

    @property
    def entry_spread_risk_quote_source_contract(self):
        if self.entry_spread_risk_policy is None:
            return None
        from .strategy_registry import numbered_strategy
        sources = tuple(v for v in numbered_strategy(self.strategy_number).input_contracts
                        if v.startswith('declared-entry-spread-risk-quote-source@'))
        if len(sources) != 1:
            raise ValueError('Entry cost release requires one exact quote-source declaration')
        return sources[0]

    @property
    def entry_spread_risk_intent_recovery_contract(self):
        if self.entry_spread_risk_policy is None:
            return None
        from .strategy_registry import numbered_strategy
        sources = tuple(v for v in numbered_strategy(self.strategy_number).input_contracts
                        if v.startswith('entry-spread-risk-intent-recovery@'))
        if not sources:
            return 'entry-spread-risk-intent-recovery@1'
        if len(sources) != 1 or sources[0] != 'entry-spread-risk-intent-recovery@2':
            raise ValueError('Entry cost recovery declaration is ambiguous or unsupported')
        return sources[0]

    @property
    def armed_profit_floor_policy(self):
        if self.strategy_number == 52:
            from .strategy_fifty_two_release import ARMED_PROFIT_FLOOR_POLICY
            return ARMED_PROFIT_FLOOR_POLICY
        return None

    @property
    def early_original_risk_policy(self):
        """A declared release rule, consumed generically by position management."""
        if self.strategy_number in (50, 52, 53, 54, 55, 56, 57, 58, 59, 60, 61):
            from .strategy_fifty_release import EARLY_FAILURE_POLICY
            return EARLY_FAILURE_POLICY
        if self.strategy_number == 48:
            from .strategy_forty_eight_release import EARLY_FAILURE_POLICY
            return EARLY_FAILURE_POLICY
        if self.strategy_number in (46, 47):
            from .strategy_forty_six_release import EARLY_FAILURE_POLICY
            return EARLY_FAILURE_POLICY
        return None

    @property
    def allows_session_exit(self) -> bool:
        return self.strategy_number in (2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15, 16, 17, 18, 19, 20, 21, 22, 23, 24, 25, 26, 27, 28, 29, 30, 31, 32, 33, 34, 35, 36, 37, 38, 39, 40, 41, 42, 46, 47, 48, 50, 52, 53, 54, 55, 56, 57, 58, 59, 60, 61)

    @property
    def allows_adds(self) -> bool:
        return self.strategy_number not in (4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15, 16, 17, 18, 19, 20, 21, 22, 23, 24, 25, 26, 27, 28, 29, 30, 31, 32, 33, 34, 35, 36, 37, 38, 39, 40, 41, 42, 46, 47, 48, 50, 52, 53, 54, 55, 56, 57, 58, 59, 60, 61)

    @property
    def allows_completed_30s_trailing(self) -> bool:
        return self.strategy_number not in (5, 6, 8, 9, 10, 11, 12, 13, 14, 15, 16, 17, 18, 19, 20, 21, 22, 23, 24, 25, 26, 27, 28, 29, 30, 31, 32, 33, 34, 35, 36, 37, 38, 39, 40, 41, 42, 46, 47, 48, 50, 52, 53, 54, 55, 56, 57, 58, 59, 60, 61)

    @property
    def allows_target_escalation(self) -> bool:
        return self.strategy_number not in (6, 7, 8, 9, 10, 11, 12, 13, 14, 15, 16, 17, 18, 19, 20, 21, 22, 23, 24, 25, 26, 27, 28, 29, 30, 31, 32, 33, 34, 35, 36, 37, 38, 39, 40, 41, 42, 46, 47, 48, 50, 52, 53, 54, 55, 56, 57, 58, 59, 60, 61)

    @property
    def caps_entry_at_reference_ask(self) -> bool:
        return self.strategy_number in (8, 9, 10, 11, 12, 13, 14, 15, 16, 17, 18, 19, 20, 21, 22, 23, 24, 25, 26, 27, 28, 29, 30, 31, 32, 33, 34, 35, 36, 37, 38, 39, 40, 41, 42, 46, 47, 48, 50, 52, 53, 54, 55, 56, 57, 58, 59, 60, 61)

    @property
    def allows_followthrough_failure_exit(self) -> bool:
        return self.strategy_number in (9, 10, 11, 12, 13, 14, 15, 16, 17, 18, 19, 20, 21, 22, 23, 24, 25, 26, 27, 28, 29, 30, 31, 32, 33, 34, 35, 36, 37, 38, 39, 40, 41, 42, 46, 47, 48, 50, 52, 53, 54, 55, 56, 57, 58, 59, 60, 61)

    def entry_allowed(self, boundary_ms: int) -> bool:
        return (self.strategy_number == 1 or 0 < boundary_ms < 19_500_000
                or 43_200_000 < boundary_ms < 57_000_000)

    def activation_allowed(self, boundary_ms: int, episode_start_ms: int) -> bool:
        """Strategy 3 requires an episode born in this extended session."""
        if self.strategy_number not in (3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15, 16, 17, 18, 19, 20, 21, 22, 23, 24, 25, 26, 27, 28, 29, 30, 31, 32, 33, 34, 35, 36, 37, 38, 39, 40, 41, 42, 46, 47, 48, 50, 52, 53, 54, 55, 56, 57, 58, 59, 60, 61):
            return True
        return (0 < episode_start_ms <= boundary_ms < 19_500_000
                or 43_200_000 < episode_start_ms <= boundary_ms < 57_000_000)

    def acquisition_cutoff(self, boundary_ms: int) -> bool:
        return self.strategy_number in (2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15, 16, 17, 18, 19, 20, 21, 22, 23, 24, 25, 26, 27, 28, 29, 30, 31, 32, 33, 34, 35, 36, 37, 38, 39, 40, 41, 42, 46, 47, 48, 50, 52, 53, 54, 55, 56, 57, 58, 59, 60, 61) and (
            19_500_000 <= boundary_ms <= 19_800_000 or 57_000_000 <= boundary_ms <= 57_600_000)

    def liquidation_due(self, boundary_ms: int) -> bool:
        return self.strategy_number in (2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15, 16, 17, 18, 19, 20, 21, 22, 23, 24, 25, 26, 27, 28, 29, 30, 31, 32, 33, 34, 35, 36, 37, 38, 39, 40, 41, 42, 46, 47, 48, 50, 52, 53, 54, 55, 56, 57, 58, 59, 60, 61) and (
            19_740_000 <= boundary_ms <= 19_800_000 or 57_300_000 <= boundary_ms <= 57_600_000)


@dataclass(frozen=True, slots=True)
class DeclaredFixedStrategyContract(NumberedFixedStrategyContract):
    """Installed adapter capabilities come from an exact immutable release payload.

    The serialized policies are copied canonical content, never a caller-owned
    mutable dictionary. Legacy contracts do not enter this adapter.
    """
    release: object = None
    policy_json: str = ''

    def __post_init__(self):
        from .strategy_registry import NumberedStrategyRelease
        from .journal_contract import canonical_json
        import json
        if type(self.release) is not NumberedStrategyRelease:
            raise ValueError('Declared fixed contract needs an exact typed release')
        self.release.verify()
        if (type(self.strategy_number) is not int or self.strategy_number != self.release.number
                or self.strategy_id != self.release.executor_strategy_id
                or self.execution_interval != self.release.evaluation_interval
                or self.release.input_contracts.count(DECLARED_FIXED_ADAPTER) != 1
                or type(self.policy_json) is not str):
            raise ValueError('Declared fixed contract identity or input differs')
        payload = json.loads(self.policy_json)
        if type(payload) is not dict or canonical_json(payload) != self.policy_json:
            raise ValueError('Declared fixed policy payload is not canonical')
        required = ('session_policy', 'activation_policy', 'add_policy', 'trailing_policy',
                    'target_policy', 'entry_price_policy', 'followthrough_policy',
                    'entry_spread_risk_policy')
        if any(type(payload.get(key)) is not dict for key in required):
            raise ValueError('Declared fixed capability payload is incomplete')
        bindings = (
            ('session_policy', 'strategy-two-extended-session-policy-v1', session_policy_payload),
            ('activation_policy', 'strategy-three-current-session-activation-v1', activation_policy_payload),
            ('add_policy', 'strategy-four-no-add-v1', add_policy_payload),
            ('trailing_policy', 'strategy-five-no-completed-30s-low-trailing-v1', trailing_policy_payload),
            ('target_policy', 'strategy-six-initial-target-only-v1', target_policy_payload),
            ('entry_price_policy', 'strategy-eight-reference-ask-entry-cap-v1', entry_price_policy_payload),
        )
        for key, rule, producer in bindings:
            if rule not in self.release.rule_set_contracts or payload[key] != producer():
                raise ValueError('Declared fixed capability policy differs from exact supported rule')
        # Canonical serialization distinguishes bool/int aliases in producer fields.
        if any(canonical_json(payload[key]) != canonical_json(producer())
               for key, _, producer in bindings):
            raise ValueError('Declared fixed capability scalar types differ')
        self.entry_spread_risk_policy

    def _policy(self, key):
        import json
        return json.loads(self.policy_json)[key]

    def _has(self, rule):
        return rule in self.release.rule_set_contracts

    @property
    def _declared_allows_session_exit(self):
        return self._has('strategy-two-extended-session-policy-v1')

    @property
    def _declared_allows_adds(self):
        return self._policy('add_policy')['allows_adds']

    @property
    def _declared_allows_completed_30s_trailing(self):
        return self._policy('trailing_policy')['completed_30s_low_trailing']

    @property
    def _declared_allows_target_escalation(self):
        return self._policy('target_policy')['target_escalation']

    @property
    def _declared_caps_entry_at_reference_ask(self):
        return self._policy('entry_price_policy')['maximum_buy_price'] == 'proposal_reference_ask'

    @property
    def _declared_allows_followthrough_failure_exit(self):
        return self._has('strategy-nine-followthrough-failure-v1')

    @property
    def _declared_entry_spread_risk_policy(self):
        from .entry_spread_risk import EntrySpreadRiskPolicy
        payload = self._policy('entry_spread_risk_policy')
        policy = EntrySpreadRiskPolicy(payload['policy_id'], tuple(payload['maximum_spread_original_risk']))
        if payload != policy.payload() or not self._has(policy.policy_id):
            raise ValueError('Declared entry spread policy payload differs from its rule')
        return policy

    def _windows(self):
        def clock(value):
            hours, minutes = map(int, value.split(':'))
            return ((hours * 60 + minutes) - 4 * 60) * 60_000
        return tuple(tuple(clock(window[key]) for key in
                           ('start', 'entry_cutoff', 'liquidation_start', 'end'))
                     for window in self._policy('session_policy')['windows'])

    def _declared_entry_allowed(self, boundary_ms):
        return any(start < boundary_ms < cutoff for start, cutoff, _, _ in self._windows())

    def _declared_activation_allowed(self, boundary_ms, episode_start_ms):
        return any(start < episode_start_ms <= boundary_ms < cutoff
                   for start, cutoff, _, _ in self._windows())

    def _declared_acquisition_cutoff(self, boundary_ms):
        return any(cutoff <= boundary_ms <= end for _, cutoff, _, end in self._windows())

    def _declared_liquidation_due(self, boundary_ms):
        return any(liquidation <= boundary_ms <= end for _, _, liquidation, end in self._windows())


    # Preserve legacy named-symbol proof cardinality; public API stays identical.
    allows_session_exit = _declared_allows_session_exit
    allows_adds = _declared_allows_adds
    allows_completed_30s_trailing = _declared_allows_completed_30s_trailing
    allows_target_escalation = _declared_allows_target_escalation
    caps_entry_at_reference_ask = _declared_caps_entry_at_reference_ask
    allows_followthrough_failure_exit = _declared_allows_followthrough_failure_exit
    entry_spread_risk_policy = _declared_entry_spread_risk_policy
    entry_allowed = _declared_entry_allowed
    activation_allowed = _declared_activation_allowed
    acquisition_cutoff = _declared_acquisition_cutoff
    liquidation_due = _declared_liquidation_due

def numbered_fixed_strategy(number: int) -> NumberedFixedStrategyContract:
    if type(number) is int and number == 51:
        from .strategy_fifty_one_contract import strategy_fifty_one_contract
        return strategy_fifty_one_contract()
    if type(number) is int and number == 49:
        from .strategy_forty_nine_contract import strategy_forty_nine_contract
        return strategy_forty_nine_contract()
    if type(number) is not int or number not in (1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15, 16, 17, 18, 19, 20, 21, 22, 23, 24, 25, 26, 27, 28, 29, 30, 31, 32, 33, 34, 35, 36, 37, 38, 39, 40, 41, 42, 46, 47, 48, 50, 52, 53, 54, 55, 56, 57, 58, 59, 60, 61):
        if declared_fixed_rule(number):
            from .strategy_registry import numbered_strategy, fixed_strategy_executor
            release = numbered_strategy(number)
            contract = fixed_strategy_executor(release.executor_strategy_id, number).contract_factory()
            if type(contract) is not DeclaredFixedStrategyContract or contract.release != release:
                raise ValueError('Declared fixed factory differs from installed release')
            return contract
        raise ValueError("No installed numbered fixed Backtest contract")
    return NumberedFixedStrategyContract(number)


def resolve_numbered_fixed_strategy(strategy_id: str, revision: int) -> NumberedFixedStrategyContract:
    if strategy_id != STRATEGY_ID:
        raise ValueError("Numbered fixed Backtest strategy identity differs")
    return numbered_fixed_strategy(revision)


def is_numbered_fixed_strategy(strategy_id: str, revision: int) -> bool:
    return strategy_id == STRATEGY_ID and type(revision) is int and (revision in (1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15, 16, 17, 18, 19, 20, 21, 22, 23, 24, 25, 26, 27, 28, 29, 30, 31, 32, 33, 34, 35, 36, 37, 38, 39, 40, 41, 42, 46, 47, 48, 49, 50, 51, 52, 53, 54, 55, 56, 57, 58, 59, 60, 61) or declared_fixed_rule(revision, 'strategy-fourteen-numbered-admission-v1'))


_SESSION_EXIT_REASONS = MappingProxyType({
    2: "strategy_two_session_exit", 3: "strategy_three_session_exit",
    4: "strategy_four_session_exit", 5: "strategy_five_session_exit",
    6: "strategy_six_session_exit", 7: "strategy_seven_session_exit",
    8: "strategy_eight_session_exit", 9: "strategy_nine_session_exit",
    10: "strategy_ten_session_exit", 11: "strategy_eleven_session_exit",
    12: "strategy_twelve_session_exit", 13: "strategy_thirteen_session_exit",
    14: "strategy_fourteen_session_exit", 15: "strategy_fifteen_session_exit",
    16: "strategy_sixteen_session_exit", 17: "strategy_seventeen_session_exit",
    18: "strategy_eighteen_session_exit",
    19: "strategy_nineteen_session_exit",
    20: "strategy_twenty_session_exit",
    21: "strategy_twenty_one_session_exit",
    22: "strategy_twenty_two_session_exit",
    23: "strategy_twenty_three_session_exit",
    24: "strategy_twenty_four_session_exit",
    25: "strategy_twenty_five_session_exit",
    26: "strategy_twenty_six_session_exit",
    27: "strategy_twenty_seven_session_exit",
    28: "strategy_twenty_eight_session_exit",
    29: "strategy_twenty_nine_session_exit",
    30: "strategy_thirty_session_exit",
    31: "strategy_thirty_one_session_exit",
    32: "strategy_thirty_two_session_exit",
    33: "strategy_thirty_three_session_exit",
    34: "strategy_thirty_four_session_exit",
    35: "strategy_thirty_five_session_exit",
    36: "strategy_thirty_six_session_exit",
    37: "strategy_thirty_seven_session_exit",
    38: "strategy_thirty_eight_session_exit",
    39: "strategy_thirty_nine_session_exit",
    40: "strategy_forty_session_exit", 41: "strategy_forty_one_session_exit", 42: "strategy_forty_two_session_exit",
    46: "strategy_forty_six_session_exit",
    47: "strategy_forty_seven_session_exit",
    48: "strategy_forty_eight_session_exit",
    49: "strategy_forty_nine_session_exit",
 50: "strategy_fifty_session_exit", 51: "strategy_fifty_one_session_exit", 52: 'strategy_fifty_two_session_exit', 53: "strategy_fifty_three_session_exit", 54: "strategy_fifty_four_session_exit", 55: 'strategy_fifty_five_session_exit', 56: 'strategy_fifty_six_session_exit', 57: 'strategy_fifty_seven_session_exit', 58: 'strategy_fifty_eight_session_exit', 59: 'strategy_fifty_nine_session_exit', 60: 'strategy_sixty_session_exit', 61: 'strategy_sixty_one_session_exit'})


def numbered_session_exit_reason(strategy_number: int) -> str:
    """One exact reason for proposal, runtime admission and typed persistence.

    The installed contract remains the admission authority. A reason mapping
    cannot admit a future number or turn Strategy 1 into a session-exit policy.
    """
    contract = numbered_fixed_strategy(strategy_number)
    if not contract.allows_session_exit:
        raise ValueError("Numbered strategy has no session-exit reason")
    if strategy_number not in _SESSION_EXIT_REASONS and type(contract) is DeclaredFixedStrategyContract:
        return f'strategy_{strategy_number}_session_exit'
    return _SESSION_EXIT_REASONS[strategy_number]
