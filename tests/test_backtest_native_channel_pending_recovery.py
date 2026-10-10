import pytest

from tests.test_backtest_native_channel_qualification import installed, unit  # noqa: F401
from tests.test_backtest_native_channel_exit_recovery import recovery_case, rehash
from src.backend.backtest_native_channel_exit_recovery import reprove_native_channel_exit_rows
from src.backend.backtest_native_channel_pending_recovery import reprove_native_channel_pending_rows
from src.backend.backtest_native_channel_pending_projection import project_native_channel_pending
from src.trading_runtime.arte_native_channel_pending_schema import PENDING, PENDING_SOURCE_ROW


def pending_case(installed):
    rows, args = recovery_case(installed)
    request = reprove_native_channel_exit_rows(*rows, **args)
    args.pop('checkpoint')
    args.update(manager_snapshot_id=args['parent_record_id'], manager_checkpoint_sequence=12,
        manager_boundary_ms=request.decision['decision_day_ms'] - 14_400_000)
    projection_args = {k: v for k, v in args.items()
        if k not in ('prepared', 'policy', 'quotes', 'session_date', 'group', 'executions')}
    pending = project_native_channel_pending(request, args['group'], args['executions'], **projection_args)
    return pending, args


def test_pending_recovery_recomputes_without_fabricated_post_checkpoint_hashes(installed):
    rows, args = pending_case(installed)
    request = reprove_native_channel_pending_rows(*rows, **args)
    assert request.source_plan is args['prepared'].source_plan
    assert request.acquisition.acquisition_day_us == 14_400_200_000
    assert not any('snapshot_hash' in key for key in rows[0])


@pytest.mark.parametrize('defect', ['manager', 'sequence', 'clock', 'source', 'quote', 'missing'])
def test_rehashed_pending_children_cannot_replace_independent_snapshot_context(installed, defect):
    (header, sources, fills), args = pending_case(installed)
    if defect == 'manager': header = rehash(PENDING, {**header, 'manager_snapshot_id': '88888888-8888-4888-8888-888888888888'})
    if defect == 'sequence': header = rehash(PENDING, {**header, 'manager_checkpoint_sequence': 13})
    if defect == 'clock': header = rehash(PENDING, {**header, 'manager_boundary_ms': header['manager_boundary_ms'] + 100})
    if defect == 'source': sources = (rehash(PENDING_SOURCE_ROW, {**sources[0], 'close_int': sources[0]['close_int'] + 1}),)
    if defect == 'quote': header = rehash(PENDING, {**header, 'ask_int': header['ask_int'] + 1})
    if defect == 'missing': fills = ()
    with pytest.raises(ValueError): reprove_native_channel_pending_rows(header, sources, fills, **args)
