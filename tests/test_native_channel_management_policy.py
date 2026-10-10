from dataclasses import replace
from types import SimpleNamespace
import json

import pytest

from tests.test_backtest_native_channel_qualification import installed, unit  # noqa: F401
from tests.test_backtest_native_channel_exit import setup
from src.trading_runtime.native_channel_management_policy import (
    RULE, POLICY_KEY, SOURCE_CONTRACT, NativeChannelManagementPolicy,
    parse_native_channel_management_policy, selected_snapshot_contracts,
)
from src.trading_runtime.journal_contract import canonical_json
from src.trading_runtime.arte_journal_writer import _CONTRACTS


def declared(installed):
    policy = NativeChannelManagementPolicy(setup(installed)[1], 16, 100)
    release = SimpleNamespace(rule_set_contracts=(RULE,), input_contracts=(SOURCE_CONTRACT,))
    payload = json.loads(canonical_json(policy.payload()))
    return policy, release, {POLICY_KEY: payload}


def test_complete_policy_selects_uninstalled_schemas_and_legacy_selects_none(installed):
    policy, release, payload = declared(installed)
    assert parse_native_channel_management_policy(release, payload) == policy
    tables = selected_snapshot_contracts(policy)
    assert len(tables) == 4 and len({t.name for t in tables}) == 4
    assert all(t.name not in _CONTRACTS for t in tables)
    assert all("storage_policy = 'live_market_ssd'" in t.ddl() for t in tables)
    assert selected_snapshot_contracts(None) == ()
    assert parse_native_channel_management_policy(SimpleNamespace(rule_set_contracts=(), input_contracts=()), {}) is None


@pytest.mark.parametrize('defect', ['rule_missing', 'source_missing', 'policy_missing', 'rule_duplicate', 'source_duplicate'])
def test_partial_declarations_fail_closed(installed, defect):
    _, release, payload = declared(installed)
    if defect == 'rule_missing': release.rule_set_contracts = ()
    if defect == 'source_missing': release.input_contracts = ()
    if defect == 'policy_missing': payload = {}
    if defect == 'rule_duplicate': release.rule_set_contracts = (RULE, RULE)
    if defect == 'source_duplicate': release.input_contracts = (SOURCE_CONTRACT, SOURCE_CONTRACT)
    with pytest.raises(ValueError): parse_native_channel_management_policy(release, payload)


@pytest.mark.parametrize('field, value', [
    ('persistence', 'direct_strategy_insert'), ('order_admission', 'skip_checkpoint'),
    ('inherited_orders', 'change_sizing'), ('overflow', 'truncate'), ('missing', 'enter'),
    ('snapshot_schema', 'legacy'), ('max_pending_requests', True), ('max_rows_per_family', 1),
])
def test_changed_execution_semantics_or_truncating_bounds_are_rejected(installed, field, value):
    _, release, payload = declared(installed)
    payload[POLICY_KEY][field] = value
    with pytest.raises(ValueError): parse_native_channel_management_policy(release, payload)


def test_actual_declared_adapter_preserves_parent_and_requires_complete_new_capability(installed):
    from src.trading_runtime.strategy_registry import initialize_numbered_fixed_strategies
    from src.trading_runtime.numbered_fixed_strategy import numbered_fixed_strategy
    initialize_numbered_fixed_strategies()
    parent = numbered_fixed_strategy(115)
    policy, _, payload = declared(installed)
    release = replace(parent.release, number=100001, executor_revision=100001,
        rule_set_contracts=(*parent.release.rule_set_contracts, RULE),
        input_contracts=(*parent.release.input_contracts, SOURCE_CONTRACT), approved_digest='')
    release = replace(release, approved_digest=release.digest())
    policies = json.loads(parent.policy_json)
    policies.update(payload)
    child = type(parent)(release.number, release.executor_strategy_id, release.evaluation_interval,
        release, canonical_json(policies))
    assert child.native_channel_management_policy == policy
    assert parent.native_channel_management_policy is None
    assert child.confirmed_original_risk_policy == parent.confirmed_original_risk_policy
    assert child.entry_spread_risk_policy == parent.entry_spread_risk_policy
    assert json.loads(child.policy_json)['followthrough_policy'] == json.loads(parent.policy_json)['followthrough_policy']
    with pytest.raises(ValueError):
        type(parent)(release.number, release.executor_strategy_id, release.evaluation_interval,
            release, parent.policy_json)
