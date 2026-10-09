"""Closed declaration for operation-owned entry and decision-local read reuse."""
from dataclasses import dataclass

RULE = 'fixed-lot-issued-entry-decision-read-reuse@1'
INPUT = 'declared-fixed-lot-management-read-reuse@1'


@dataclass(frozen=True, slots=True)
class FixedLotManagementReusePolicy:
    version: int = 1
    max_entries: int = 32

    def __post_init__(self):
        if type(self.version) is not int or self.version != 1 or type(self.max_entries) is not int or not 1 <= self.max_entries <= 32:
            raise ValueError('Explicit bounded version1 management reuse required')

    def payload(self):
        self.__post_init__()
        return dict(version=self.version, max_entries=self.max_entries, rule=RULE,
            input_contract=INPUT, entry='issued complete native replay; exact typed snapshot and dependencies',
            reads='one owner/request/decision; exact exclusive lease and committed head',
            mutation='changed content, ownership, frontier or methods reject',
            commands='invalidate before execution; independent post-command confirmation',
            cold='independent complete verification; no warm admission')


def declared_management_reuse_policy(release, value):
    from .strategy_registry import NumberedStrategyRelease
    if type(release) is not NumberedStrategyRelease:
        raise ValueError('Exact immutable management reuse release required')
    release.verify()
    inputs, rules = release.input_contracts.count(INPUT), release.rule_set_contracts.count(RULE)
    if not inputs and not rules and value is None:
        return None
    if inputs != 1 or rules != 1 or type(value) is not dict:
        raise ValueError('Management reuse needs exact paired declarations')
    policy = FixedLotManagementReusePolicy(value.get('version'), value.get('max_entries'))
    expected = policy.payload()
    if set(value) != set(expected) or any(type(value[k]) is not type(v) or value[k] != v for k,v in expected.items()):
        raise ValueError('Management reuse canonical payload differs')
    return policy


def installed_management_reuse_policy(source):
    if not source.installed_json:
        return None
    strategy = source.installed_payload['strategy']
    contract = strategy.get('numbered_release', {}).get('contract', {})
    value = strategy['parameters'].get('management_reuse_policy')
    if value is None and INPUT not in contract.get('input_contracts', ()) and RULE not in contract.get('rule_set_contracts', ()):
        return None
    from src.backend.backtest_fixed_structural_lot_source import require_native_fixed_structural_lot_source
    from .strategy_registry import numbered_strategy, fixed_strategy_executor
    from .fixed_structural_lot_reuse_contract import require_declared_fixed_structural_lot_contract
    require_native_fixed_structural_lot_source(source)
    source.require_installed_admission()
    release = numbered_strategy(strategy['strategy_number'])
    if contract != release.canonical_payload():
        raise ValueError('Management reuse differs from installed immutable release')
    policy = declared_management_reuse_policy(release, value)
    factory = fixed_strategy_executor(release.executor_strategy_id, release.executor_revision).contract_factory()
    require_declared_fixed_structural_lot_contract(factory, release)
    if factory.management_reuse_policy != policy:
        raise ValueError('Management reuse differs from registered typed factory')
    return policy
