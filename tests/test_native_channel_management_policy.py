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


def test_family_bound_covers_support_for_entire_declared_pending_inventory(installed):
    exit_policy = setup(installed)[1]
    sequence = replace(exit_policy.sequence, sequence=replace(exit_policy.sequence.sequence, minimum_bars=2))
    exit_policy = replace(exit_policy, sequence=sequence)
    with pytest.raises(ValueError, match='truncate declared support'):
        NativeChannelManagementPolicy(exit_policy, 16, 16)
    assert NativeChannelManagementPolicy(exit_policy, 16, 32).max_rows_per_family == 32


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


def test_dispatch_selection_requires_typed_channel_and_original_risk_authority(installed):
    from src.trading_runtime.arte_typed_insert_dispatch import _manager_tables, _manager_root_invalid
    from src.trading_runtime.numbered_fixed_strategy import numbered_fixed_strategy
    from src.trading_runtime.strategy_registry import initialize_numbered_fixed_strategies
    initialize_numbered_fixed_strategies()
    risk = numbered_fixed_strategy(115).confirmed_original_risk_policy
    channel = declared(installed)[0]
    channel_tables = {t.name for t in selected_snapshot_contracts(channel)}
    assert not channel_tables & _manager_tables(risk)
    assert channel_tables <= _manager_tables(risk, channel)
    with pytest.raises(ValueError): _manager_tables(None, channel)
    with pytest.raises(ValueError): _manager_tables(risk, channel.payload())
    root = 'trading_strategy_one_manager_snapshot_v5'
    assert not _manager_root_invalid({root}, risk, channel)
    assert _manager_root_invalid({root}, risk)
    for legacy in ('v2', 'v3', 'v4'):
        old = 'trading_strategy_one_manager_snapshot_' + legacy
        assert _manager_root_invalid({root, old}, risk, channel)
        assert _manager_root_invalid({old}, risk, channel)


def test_channel_dispatch_and_keeper_head_use_selected_versioned_root(installed):
    from tests.test_arte_typed_insert_dispatch import (
        Keeper, Client, compact_running_prefix, BATCH_ID, MANAGER_HASH,
    )
    from src.trading_runtime.arte_typed_insert_dispatch import TypedInsertDispatch
    from src.trading_runtime.numbered_fixed_strategy import numbered_fixed_strategy
    from src.trading_runtime.strategy_registry import initialize_numbered_fixed_strategies
    from src.trading_runtime.strategy_one_management_snapshot import ManagedManagerSnapshotHeadReader
    initialize_numbered_fixed_strategies()
    risk = numbered_fixed_strategy(115).confirmed_original_risk_policy
    channel = declared(installed)[0]
    authority = TypedInsertDispatch(Keeper())
    authority.initialize_new_run('run-1')
    compact_running_prefix(authority)
    client = Client(authority)
    client.confirmed_original_risk_policy = risk
    client.native_channel_management_policy = channel
    operations = []
    for table in ('trading_strategy_one_protection_snapshot_v1',
                  *(t.name for t in selected_snapshot_contracts(channel))):
        token = f'manager-state:run-1:1:{MANAGER_HASH}:{table}'
        sql = (f'INSERT INTO arte.{table} (run_id) SETTINGS '
               'async_insert=1,wait_for_async_insert=1,insert_deduplicate=1,'
               f"insert_deduplication_token='{token}' FORMAT JSONEachRow\n{{}}")
        authority.execute_typed_insert(client, run_id='run-1', table=table, token=token,
            sql=sql, batch_id=BATCH_ID, batch_last_sequence=1, manager_snapshot_hash=MANAGER_HASH)
        authority.seal_verified_operation(run_id='run-1', table=table, token=token,
            batch_id=BATCH_ID, batch_last_sequence=1, manager_snapshot=True)
        operations.append((table, token))
    with pytest.raises(ValueError):
        authority.compact_verified_manager_snapshot(run_id='run-1', batch_id=BATCH_ID,
            last_sequence=1, snapshot_hash=MANAGER_HASH, operations=tuple(operations),
            previous=None, confirmed_original_risk_policy=risk)
    authority.compact_verified_manager_snapshot(run_id='run-1', batch_id=BATCH_ID,
        last_sequence=1, snapshot_hash=MANAGER_HASH, operations=tuple(operations),
        previous=None, confirmed_original_risk_policy=risk, native_channel_management_policy=channel)
    assert authority.keeper.get(ManagedManagerSnapshotHeadReader.path('run-1'))[0] == (
        f'1\nrun-1\n1\n{BATCH_ID}\n{MANAGER_HASH}'.encode())
    assert authority._read_gate('run-1')[0].registered == 0
