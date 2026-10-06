"""Standalone running financial checkpoint contracts; no installation authority."""
from src.trading_runtime.arte_journal_schema import TableContract

ROOT = TableContract('trading_running_financial_checkpoint_v1', (
    ('run_id','String'), ('checkpoint_month','Date'), ('batch_id','UUID'),
    ('last_sequence','UInt64'), ('source_cursor','String'), ('session_date','Date'),
    ('boundary_ms','UInt32'), ('configuration_hash','FixedString(64)'),
    ('broker_snapshot_id','UUID'), ('broker_snapshot_hash','FixedString(64)'),
    ('account_count','UInt32'), ('account_hash','FixedString(64)'),
    ('content_hash','FixedString(64)')), 'toYYYYMM(checkpoint_month)', 'run_id, last_sequence')
ACCOUNT = TableContract('trading_running_financial_checkpoint_account_v1', (
    ('run_id','String'), ('checkpoint_month','Date'), ('batch_id','UUID'),
    ('last_sequence','UInt64'), ('ordinal','UInt32'), ('account_id','String'),
    ('state_revision','UInt64'), ('state_hash','FixedString(64)'),
    ('snapshot_at',"DateTime64(6, 'UTC')"), ('content_hash','FixedString(64)')),
    'toYYYYMM(checkpoint_month)', 'run_id, last_sequence, ordinal')
TABLES = (ROOT, ACCOUNT)
