"""Uninstalled snapshot children; pending facts cannot include their own root hash."""
from .arte_journal_schema import TableContract
from .arte_native_channel_exit_schema import DECISION, SOURCE_ROW, EXECUTION_ROW


_POST_CHECKPOINT_FIELDS = frozenset((
    'source_manager_snapshot_id', 'source_manager_checkpoint_sequence',
    'source_manager_snapshot_hash', 'source_broker_snapshot_id', 'source_broker_snapshot_hash',
))
PENDING = TableContract('strategy_native_channel_pending_v1',
    tuple(pair for pair in DECISION.columns if pair[0] not in _POST_CHECKPOINT_FIELDS)
    + (('manager_snapshot_id', 'UUID'), ('manager_checkpoint_sequence', 'UInt64'),
       ('manager_boundary_ms', 'UInt32')),
    'toYYYYMM(event_month)', 'run_id, manager_snapshot_id, record_id')
PENDING_SOURCE_ROW = TableContract('strategy_native_channel_pending_source_v1', SOURCE_ROW.columns,
    SOURCE_ROW.partition, SOURCE_ROW.order)
PENDING_EXECUTION_ROW = TableContract('strategy_native_channel_pending_execution_v1', EXECUTION_ROW.columns,
    EXECUTION_ROW.partition, EXECUTION_ROW.order)
TABLES = (PENDING, PENDING_SOURCE_ROW, PENDING_EXECUTION_ROW)
