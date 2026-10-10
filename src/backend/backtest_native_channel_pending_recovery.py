"""Reprove saved pre-checkpoint children; snapshot attestation remains separate."""
from .backtest_native_channel_exit_recovery import _reprove_native_channel_rows
from .backtest_native_channel_pending_projection import project_native_channel_pending
from src.trading_runtime.arte_native_channel_pending_schema import (
    PENDING, PENDING_SOURCE_ROW, PENDING_EXECUTION_ROW,
)


def reprove_native_channel_pending_rows(header, sources, fills, *, manager_snapshot_id,
        manager_checkpoint_sequence, manager_boundary_ms, prepared, policy, quotes,
        group, executions, original_entry, source_entry_intent_id, current_financial,
        session_date, stored_utc=False, **lineage_context):
    """Recompute full pending evidence under independently supplied manager identity.

    The caller must verify root membership, counts and the manager/broker
    checkpoint attestations. Passing this comparison grants no exit authority.
    """
    return _reprove_native_channel_rows(header, sources, fills,
        contracts=(PENDING, PENDING_SOURCE_ROW, PENDING_EXECUTION_ROW),
        projector=project_native_channel_pending,
        projector_context=dict(manager_snapshot_id=manager_snapshot_id,
            manager_checkpoint_sequence=manager_checkpoint_sequence, manager_boundary_ms=manager_boundary_ms),
        prepared=prepared, policy=policy, quotes=quotes, group=group, executions=executions,
        original_entry=original_entry, source_entry_intent_id=source_entry_intent_id,
        current_financial=current_financial, session_date=session_date, stored_utc=stored_utc, **lineage_context)
