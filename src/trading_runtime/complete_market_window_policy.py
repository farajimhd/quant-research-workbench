"""Exact declaration for prepared complete market windows; no source authority."""
from dataclasses import dataclass

INPUT = 'declared-complete-market-window-input@1'
RULE = 'declared-complete-market-window-rule@1'


@dataclass(frozen=True, slots=True)
class CompleteMarketWindowPolicy:
    schema_version: int
    window_span_ms: int
    max_wire_bytes: int
    max_response_rows: int
    max_resident_bytes: int
    max_window_rows: int
    read_ahead_groups: int

    def __post_init__(self):
        limits = ((self.schema_version, 1), (self.window_span_ms, 300000),
                  (self.max_wire_bytes, 16 * 1024 * 1024),
                  (self.max_response_rows, 100000),
                  (self.max_resident_bytes, 256 * 1024 * 1024),
                  (self.max_window_rows, 100000), (self.read_ahead_groups, 4096))
        if (any(type(value) is not int or not 0 < value <= ceiling for value, ceiling in limits)
                or self.window_span_ms % 100):
            raise ValueError('Complete market window policy needs exact bounded integers and 100ms clocks')

    def payload(self):
        self.__post_init__()
        return dict(schema_version=self.schema_version, input_contract=INPUT, rule=RULE,
            window_span_ms=self.window_span_ms, max_wire_bytes=self.max_wire_bytes,
            max_response_rows=self.max_response_rows, max_resident_bytes=self.max_resident_bytes,
            max_window_rows=self.max_window_rows, read_ahead_groups=self.read_ahead_groups,
            responses='complete HTTP EOF before any window row escapes; at most four responses',
            budgets='per-response wire, rows and accounted retained bytes; aggregate window rows; OS RSS and temporary parser allocations not certified',
            clocks='exclusive prior committed boundary; inclusive completed window end; sequential release',
            source='caller-certified immutable native market and execution price plans only',
            failure='abort partial response or exhausted budget; no retry, truncation or spool',
            authority='declaration only; installed source, factory and financial owners independently admit execution')


def parse_complete_market_window_policy(value):
    if type(value) is not dict:
        raise ValueError('Complete market window declaration must be an exact object')
    names = tuple(CompleteMarketWindowPolicy.__dataclass_fields__)
    if not set(names) <= set(value):
        raise ValueError('Complete market window declaration omits explicit bounds')
    policy = CompleteMarketWindowPolicy(**{name: value[name] for name in names})
    expected = policy.payload()
    if (set(value) != set(expected)
            or any(type(value[key]) is not type(item) or value[key] != item
                   for key, item in expected.items())):
        raise ValueError('Complete market window declared semantics differ')
    return policy


def declared_complete_market_window_policy(release, value):
    from .strategy_registry import NumberedStrategyRelease
    if type(release) is not NumberedStrategyRelease:
        raise ValueError('Complete market windows require an exact immutable release declaration')
    release.verify()
    inputs, rules = release.input_contracts.count(INPUT), release.rule_set_contracts.count(RULE)
    if inputs == rules == 0 and value is None:
        return None
    if inputs != 1 or rules != 1:
        raise ValueError('Complete market window input and rule must be paired exactly once')
    return parse_complete_market_window_policy(value)


def installed_complete_market_window_policy(source):
    """Select through an issued installed source, never a caller declaration.

    Unselected sources retain their original reader. A claimed policy fails
    closed unless source admission, the release and exact factory all agree.
    """
    if not source.installed_json:
        return None
    strategy = source.installed_payload['strategy']
    contract = strategy.get('numbered_release', {}).get('contract', {})
    parameters = strategy['parameters']
    claimed = ('complete_market_window_policy' in parameters
        or INPUT in contract.get('input_contracts', ())
        or RULE in contract.get('rule_set_contracts', ()))
    if not claimed:
        return None
    from src.backend.backtest_fixed_structural_lot_source import require_native_fixed_structural_lot_source
    from .strategy_registry import numbered_strategy, fixed_strategy_executor
    from .fixed_structural_lot_reuse_contract import require_declared_fixed_structural_lot_contract
    from .fixed_structural_lot_complete_market_contract import FixedStructuralLotCompleteMarketStrategyContract
    require_native_fixed_structural_lot_source(source)
    source.require_installed_admission()
    release = numbered_strategy(strategy['strategy_number'])
    if contract != release.canonical_payload():
        raise ValueError('Complete market windows differ from issued installed release')
    policy = declared_complete_market_window_policy(release, parameters.get('complete_market_window_policy'))
    factory = fixed_strategy_executor(release.executor_strategy_id, release.executor_revision).contract_factory()
    require_declared_fixed_structural_lot_contract(factory, release)
    wanted = FixedStructuralLotCompleteMarketStrategyContract
    from .selected_exit_publication_policy import INPUT as EXIT_INPUT, RULE as EXIT_RULE
    if EXIT_INPUT in release.input_contracts or EXIT_RULE in release.rule_set_contracts:
        from .fixed_structural_lot_selected_exit_contract import FixedStructuralLotSelectedExitStrategyContract
        wanted = FixedStructuralLotSelectedExitStrategyContract
    if (type(factory) is not wanted
            or factory.complete_market_policy != policy):
        raise ValueError('Complete market windows differ from exact installed factory')
    return policy
