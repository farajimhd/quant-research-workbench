import pytest
from dataclasses import replace

from tests.test_backtest_native_channel_qualification import installed, unit  # noqa: F401
from tests.test_backtest_native_channel_exit_recovery import recovery_case, rehash
from src.backend.backtest_native_channel_exit_recovery import reprove_native_channel_exit_rows
from src.backend.backtest_native_channel_pending_projection import project_native_channel_pending
from src.backend.backtest_strategy_one_management import OriginalRiskManagementState
from src.trading_runtime.strategy_one_position import ProtectionState
from src.trading_runtime.strategy_one_management_snapshot import project_manager_snapshot
from src.trading_runtime.native_channel_manager_snapshot import seal_native_channel_manager_rows, parent_contract
from src.trading_runtime.arte_native_channel_pending_schema import PENDING, PENDING_SOURCE_ROW
from src.trading_runtime.arte_journal_writer import _CONTRACTS, verify_typed_contract_row


def snapshot_case(installed):
    rows, args = recovery_case(installed)
    request = reprove_native_channel_exit_rows(*rows, **args)
    # Isolated schema fixture: Strategy117 production snapshots separately
    # require their certified price-source context, never this base proposal.
    entry = replace(args['original_entry'], strategy_number=1)
    key = entry.account_id, entry.assignment_id, entry.ticker
    boundary = request.decision['decision_day_ms'] - 14_400_000
    state = OriginalRiskManagementState(boundary, ((key, entry),),
        ((key, ProtectionState(entry.boundary_ms, entry.initial_stop, entry.initial_target)),), (),
        position_highs=((key, 10000),))
    inherited = project_manager_snapshot(run_id=args['run_id'], session_date=args['session_date'],
        checkpoint_sequence=12, state=state)
    projection_args = {k: v for k, v in args.items()
        if k not in ('prepared', 'policy', 'quotes', 'session_date', 'group', 'executions', 'checkpoint')}
    projection_args.update(parent_record_id=inherited.snapshot['snapshot_id'],
        manager_snapshot_id=inherited.snapshot['snapshot_id'], manager_checkpoint_sequence=12,
        manager_boundary_ms=boundary, original_entry=entry)
    pending = project_native_channel_pending(request, args['group'], args['executions'], **projection_args)
    return inherited, pending


def test_new_root_seals_inherited_state_and_complete_pending_children_without_installing(installed):
    inherited, pending = snapshot_case(installed)
    root = seal_native_channel_manager_rows(inherited, (pending[0],), pending[1], pending[2],
        max_pending_requests=10, max_rows_per_family=100)
    assert root.snapshot['native_channel_pending_count'] == 1
    assert root.snapshot['native_channel_source_count'] == 1
    assert root.snapshot['native_channel_execution_count'] == 1
    assert root.inherited is inherited
    assert verify_typed_contract_row(parent_contract(), root.snapshot)['content_hash'] == root.snapshot['content_hash']
    assert parent_contract().name not in _CONTRACTS
    assert "storage_policy = 'live_market_ssd'" in parent_contract().ddl()
    empty = seal_native_channel_manager_rows(inherited, (), (), (), max_pending_requests=10, max_rows_per_family=100)
    assert empty.snapshot['native_channel_pending_count'] == 0
    assert empty.snapshot['content_hash'] != root.snapshot['content_hash']


@pytest.mark.parametrize('defect', ['risk', 'owner', 'root', 'sequence', 'missing', 'duplicate', 'orphan'])
def test_new_root_rejects_rehashed_foreign_or_incomplete_snapshot_children(installed, defect):
    inherited, (headers, sources, fills) = snapshot_case(installed)
    headers = (headers,)
    if defect == 'risk': headers = (rehash(PENDING, {**headers[0], 'initial_stop': 0.7}),)
    if defect == 'owner': headers = (rehash(PENDING, {**headers[0], 'account_id': 'foreign'}),)
    if defect == 'root': headers = (rehash(PENDING, {**headers[0], 'manager_snapshot_id': '88888888-8888-4888-8888-888888888888'}),)
    if defect == 'sequence': headers = (rehash(PENDING, {**headers[0], 'manager_checkpoint_sequence': 13}),)
    if defect == 'missing': sources = ()
    if defect == 'duplicate': headers = (*headers, headers[0])
    if defect == 'orphan': sources = (rehash(PENDING_SOURCE_ROW, {**sources[0], 'decision_record_id': '99999999-9999-4999-8999-999999999999'}),)
    with pytest.raises(ValueError): seal_native_channel_manager_rows(inherited, headers, sources, fills,
        max_pending_requests=10, max_rows_per_family=100)


def test_native_strategy_still_requires_its_certified_price_context(installed):
    from src.trading_runtime.strategy_registry import initialize_numbered_fixed_strategies
    initialize_numbered_fixed_strategies()
    _, args = recovery_case(installed)
    entry = args['original_entry']
    key = entry.account_id, entry.assignment_id, entry.ticker
    state = OriginalRiskManagementState(360000, ((key, entry),),
        ((key, ProtectionState(entry.boundary_ms, entry.initial_stop, entry.initial_target)),), (),
        position_highs=((key, 10000),), first_held_boundaries=((key, 200),))
    with pytest.raises(ValueError, match='requires its native source context'):
        project_manager_snapshot(run_id=args['run_id'], session_date=args['session_date'],
            checkpoint_sequence=12, state=state)
