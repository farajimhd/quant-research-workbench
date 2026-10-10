"""Explicit channel management capability; no strategy-number selection or grants."""
from dataclasses import dataclass

from .journal_contract import canonical_json


RULE = 'certified-native-completed-channel-management@1'
POLICY_KEY = 'native_channel_management_policy'
SOURCE_CONTRACT = 'native-causal-channel-source@1'


@dataclass(frozen=True, slots=True)
class NativeChannelManagementPolicy:
    exit_policy: object
    max_pending_requests: int
    max_rows_per_family: int

    def __post_init__(self):
        from src.backend.backtest_native_channel_exit_policy import NativeChannelExitPolicy
        if (type(self.exit_policy) is not NativeChannelExitPolicy
                or type(self.max_pending_requests) is not int
                or not 0 < self.max_pending_requests <= self.exit_policy.quote.max_rows
                or type(self.max_rows_per_family) is not int
                or not 0 < self.max_rows_per_family < 2**32):
            raise ValueError('Exact channel exit declaration and bounded pending inventory required')
        self.exit_policy.__post_init__()
        required = self.exit_policy.sequence.sequence.minimum_bars + (
            self.exit_policy.sequence.reference_mode != 'none')
        if self.max_rows_per_family < self.max_pending_requests * required:
            raise ValueError('Pending family bound cannot truncate declared support or inventory')

    def payload(self):
        self.__post_init__()
        return dict(rule=RULE, exit_policy=self.exit_policy.payload(),
            max_pending_requests=self.max_pending_requests, max_rows_per_family=self.max_rows_per_family,
            snapshot_schema='trading_strategy_one_manager_snapshot_v5',
            source=SOURCE_CONTRACT, persistence='journal_worker_children_then_root_then_keeper_head',
            order_admission='independently_attested_manager_and_broker_checkpoint_before_shared_OMS',
            inherited_orders='retain_cash_sizing_costs_and_protective_OCA_ordering',
            overflow='fail_closed_without_truncation', missing='no_new_exit')


def parse_native_channel_management_policy(release, policies):
    count = release.rule_set_contracts.count(RULE)
    supplied = POLICY_KEY in policies
    if count == 0 and not supplied:
        return None
    if count != 1 or not supplied or release.input_contracts.count(SOURCE_CONTRACT) != 1:
        raise ValueError('Channel management requires one rule, source and complete policy declaration')
    value = policies[POLICY_KEY]
    from .native_channel_qualification import _require_policy_json
    from src.backend.backtest_native_channel_exit_policy import parse_native_channel_exit_policy
    _require_policy_json(value)
    if type(value) is not dict or set(value) != {
            'rule', 'exit_policy', 'max_pending_requests', 'max_rows_per_family', 'snapshot_schema',
            'source', 'persistence', 'order_admission', 'inherited_orders', 'overflow', 'missing'}:
        raise ValueError('Complete channel management semantics required')
    policy = NativeChannelManagementPolicy(parse_native_channel_exit_policy(value['exit_policy']),
        value['max_pending_requests'], value['max_rows_per_family'])
    if canonical_json(value) != canonical_json(policy.payload()):
        raise ValueError('Channel persistence, admission or inherited financial semantics differ')
    return policy


def selected_snapshot_contracts(policy):
    if policy is None:
        return ()
    if type(policy) is not NativeChannelManagementPolicy:
        raise ValueError('Exact declared native channel management capability required')
    policy.__post_init__()
    from .arte_native_channel_pending_schema import TABLES
    from .native_channel_manager_snapshot_schema import parent_contract
    return (*TABLES, parent_contract())


def selected_exit_contracts(policy):
    """Require normalized exit companions only for the declared capability."""
    if policy is None:
        return ()
    if type(policy) is not NativeChannelManagementPolicy:
        raise ValueError('Exact declared native channel management capability required')
    policy.__post_init__()
    from .arte_native_channel_exit_schema import TABLES
    return TABLES


def selected_writer_contracts(policy):
    # Exit journals are not manager snapshots and never enter manager dispatch.
    return (*selected_snapshot_contracts(policy),*selected_exit_contracts(policy))
