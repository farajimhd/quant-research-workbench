"""Pre-checkpoint normalized pending evidence; no persistence or order authority."""
from uuid import UUID

from .backtest_native_channel_exit_projection import _project_native_channel_exit_facts
from src.trading_runtime.arte_native_channel_pending_schema import (
    PENDING, PENDING_SOURCE_ROW, PENDING_EXECUTION_ROW,
)
from src.trading_runtime.arte_journal_writer import typed_contract_row


def project_native_channel_pending(request, group, executions, *, manager_snapshot_id,
        manager_checkpoint_sequence, manager_boundary_ms, original_entry,
        source_entry_intent_id, current_financial, **lineage_context):
    """Prepare manager children without claiming future checkpoint hashes.

    The snapshot owner must seal these children in its root family counts and
    independently attest the manager and broker roots before confirming exits.
    A projection does not install schemas, publish a snapshot or submit orders.
    """
    if (type(manager_snapshot_id) is not str
            or str(UUID(manager_snapshot_id)) != manager_snapshot_id
            or UUID(manager_snapshot_id).int == 0
            or manager_snapshot_id != lineage_context['parent_record_id']
            or type(manager_checkpoint_sequence) is not int
            or not 0 < manager_checkpoint_sequence < 2**64
            or type(manager_boundary_ms) is not int
            or not 0 < manager_boundary_ms <= 57_600_000
            or manager_boundary_ms % 100):
        raise ValueError('Exact pending manager identity, sequence and clock required')
    values, sources, fills = _project_native_channel_exit_facts(request, group, executions,
        original_entry=original_entry, source_entry_intent_id=source_entry_intent_id,
        current_financial=current_financial, **lineage_context)
    if values['decision_day_ms'] != manager_boundary_ms + 14_400_000:
        raise ValueError('Pending source-day decision differs from native manager clock')
    values.update(manager_snapshot_id=manager_snapshot_id,
        manager_checkpoint_sequence=manager_checkpoint_sequence, manager_boundary_ms=manager_boundary_ms)
    def child(contract, row):
        return typed_contract_row(contract, {k: v for k, v in row.items() if k != 'content_hash'})
    return (typed_contract_row(PENDING, values),
        tuple(child(PENDING_SOURCE_ROW, row) for row in sources),
        tuple(child(PENDING_EXECUTION_ROW, row) for row in fills))
