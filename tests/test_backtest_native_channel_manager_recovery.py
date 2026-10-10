from dataclasses import replace

import pytest

from tests.test_backtest_native_channel_qualification import installed, unit  # noqa: F401
from tests.test_native_channel_manager_snapshot import snapshot_case
from tests.test_backtest_native_channel_exit_recovery import recovery_case, rehash
from src.trading_runtime.native_channel_management_policy import NativeChannelManagementPolicy
from src.trading_runtime.native_channel_manager_snapshot import (
    seal_native_channel_manager_rows, verify_native_channel_manager_rows, parent_contract,
)
from src.trading_runtime.arte_native_channel_pending_schema import PENDING_SOURCE_ROW
from src.backend.backtest_native_channel_manager_recovery import reprove_native_channel_manager_snapshot


def manager_case(installed):
    inherited, (header, sources, fills) = snapshot_case(installed)
    _, args = recovery_case(installed)
    args.pop('checkpoint')
    args.update(parent_record_id=inherited.snapshot['snapshot_id'], max_rows_per_family=100,
                original_entry=replace(args['original_entry'], strategy_number=1))
    policy = NativeChannelManagementPolicy(args['policy'], 10, 100)
    rows = seal_native_channel_manager_rows(inherited, (header,), sources, fills,
        max_pending_requests=policy.max_pending_requests, max_rows_per_family=policy.max_rows_per_family)
    return rows, policy, {header['record_id']: args}


def test_full_root_and_each_request_recover_from_independent_evidence(installed):
    rows, policy, contexts = manager_case(installed)
    assert verify_native_channel_manager_rows(rows, policy).snapshot == rows.snapshot
    requests = reprove_native_channel_manager_snapshot(rows, policy, contexts)
    assert len(requests) == 1
    assert requests[0].source_plan is next(iter(contexts.values()))['prepared'].source_plan
    empty = seal_native_channel_manager_rows(rows.inherited, (), (), (),
        max_pending_requests=10, max_rows_per_family=100)
    assert reprove_native_channel_manager_snapshot(empty, policy, {}) == ()


@pytest.mark.parametrize('defect', ['missing', 'extra', 'date', 'run', 'parent', 'bound', 'clock_override'])
def test_recovery_rejects_missing_extra_or_foreign_independent_context(installed, defect):
    rows, policy, contexts = manager_case(installed)
    key = next(iter(contexts))
    if defect == 'missing': contexts = {}
    elif defect == 'extra': contexts['foreign'] = contexts[key]
    else:
        field, value = {
            'date': ('session_date', None), 'run': ('run_id', 'foreign'),
            'parent': ('parent_record_id', '88888888-8888-4888-8888-888888888888'),
            'bound': ('max_rows_per_family', 101), 'clock_override': ('manager_boundary_ms', 0),
        }[defect]
        contexts[key][field] = value
    with pytest.raises(ValueError): reprove_native_channel_manager_snapshot(rows, policy, contexts)


def test_rehashed_root_cannot_change_child_inventory(installed):
    rows, policy, contexts = manager_case(installed)
    forged = replace(rows, snapshot=rehash(parent_contract(),
        {**rows.snapshot, 'native_channel_pending_count': 0}))
    with pytest.raises(ValueError, match='complete inherited'):
        reprove_native_channel_manager_snapshot(forged, policy, contexts)


def test_resealed_source_forgery_still_fails_independent_recomputation(installed):
    rows, policy, contexts = manager_case(installed)
    forged = rehash(PENDING_SOURCE_ROW, {**rows.sources[0], 'close_int': rows.sources[0]['close_int'] + 1})
    rows = seal_native_channel_manager_rows(rows.inherited, rows.pending, (forged,), rows.executions,
        max_pending_requests=10, max_rows_per_family=100)
    with pytest.raises(ValueError, match='independent recomputation'):
        reprove_native_channel_manager_snapshot(rows, policy, contexts)
