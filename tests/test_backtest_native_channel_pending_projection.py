import pytest

from tests.test_backtest_native_channel_qualification import installed, unit  # noqa: F401
from tests.test_backtest_native_channel_exit_projection import context
from src.backend.backtest_native_channel_pending_projection import project_native_channel_pending
from src.trading_runtime.arte_native_channel_pending_schema import PENDING, TABLES
from src.trading_runtime.arte_journal_writer import _CONTRACTS, verify_typed_contract_row


def pending_context(installed):
    request, group, executions, args = context(installed)
    args.pop('checkpoint')
    args.update(manager_snapshot_id=args['parent_record_id'], manager_checkpoint_sequence=12,
        manager_boundary_ms=request.decision['decision_day_ms'] - 14_400_000)
    return request, group, executions, args


def test_pending_projection_cannot_include_its_future_checkpoint_hash(installed):
    request, group, executions, args = pending_context(installed)
    before = dict(_CONTRACTS)
    header, sources, fills = project_native_channel_pending(request, group, executions, **args)
    assert header['manager_snapshot_id'] == header['parent_record_id']
    assert header['manager_checkpoint_sequence'] == 12
    assert header['decision_day_ms'] == header['manager_boundary_ms'] + 14_400_000
    assert not any('snapshot_hash' in key for key in header)
    assert header['source_row_count'] == len(sources) == 1
    assert header['execution_row_count'] == len(fills) == 1
    assert verify_typed_contract_row(PENDING, header)['content_hash'] == header['content_hash']
    for contract in TABLES:
        assert "storage_policy = 'live_market_ssd'" in contract.ddl()
        assert contract.name not in _CONTRACTS
    assert _CONTRACTS == before


@pytest.mark.parametrize('defect', ['root', 'zero_sequence', 'boolean_sequence', 'clock', 'off_grid'])
def test_pending_children_cannot_borrow_another_manager_root_or_boundary(installed, defect):
    request, group, executions, args = pending_context(installed)
    if defect == 'root': args['manager_snapshot_id'] = '88888888-8888-4888-8888-888888888888'
    if defect == 'zero_sequence': args['manager_checkpoint_sequence'] = 0
    if defect == 'boolean_sequence': args['manager_checkpoint_sequence'] = True
    if defect == 'clock': args['manager_boundary_ms'] += 100
    if defect == 'off_grid': args['manager_boundary_ms'] += 1
    with pytest.raises(ValueError): project_native_channel_pending(request, group, executions, **args)
