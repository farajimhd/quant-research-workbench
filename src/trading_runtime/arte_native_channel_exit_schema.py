"""Uninstalled normalized channel-exit contracts; no writer or release admission.

Decision, completed source rows and execution lineage are separate families.
Native publication must independently bind their counts, hashes and checkpoint
roots. These definitions neither create tables nor alter occupied schemas.
"""
from .arte_journal_schema import TableContract


_ENVELOPE = (
    ('record_id','UUID'),('parent_record_id','UUID'),('run_id','String'),
    ('event_month','Date'),('batch_id','UUID'),
)

DECISION = TableContract('trading_native_channel_exit_v1', _ENVELOPE + (
    ('strategy_number','UInt32'),('session_date','Date'),
    ('account_id','String'),('assignment_id','String'),('ticker','String'),
    ('source_entry_intent_id','UUID'),('source_group_id','String'),
    ('first_execution_id','String'),('first_execution_time',"DateTime64(6, 'UTC')"),
    ('acquisition_day_us','UInt64'),('decision_day_ms','UInt32'),
    ('source_build_id','String'),('source_bars_attempt_id','UUID'),
    ('source_feature_attempt_id','UUID'),('source_market_plan_token','FixedString(64)'),
    ('source_feature_plan_token','FixedString(64)'),('source_feature_hash','FixedString(64)'),
    ('source_coverage_hash','FixedString(64)'),('source_producer_hash','FixedString(64)'),
    ('input_policy_digest','FixedString(64)'),('exit_policy_digest','FixedString(64)'),
    ('sequence_start_day_ms','UInt32'),('sequence_end_day_ms','UInt32'),
    ('reference_completion_day_ms','Nullable(UInt32)'),('reference_close_int','Nullable(UInt64)'),
    ('quote_day_us','UInt64'),('quote_age_us','UInt64'),('bid_int','UInt64'),('ask_int','UInt64'),
    ('original_ask','Decimal(38, 18)'),('initial_stop','Decimal(38, 18)'),
    ('position_quantity','Decimal(38, 10)'),('source_row_count','UInt32'),
    ('execution_row_count','UInt32'),('source_manager_snapshot_id','String'),
    ('source_manager_checkpoint_sequence','UInt64'),('source_manager_snapshot_hash','FixedString(64)'),
    ('source_broker_snapshot_id','String'),('source_broker_snapshot_hash','FixedString(64)'),
    ('content_hash','FixedString(64)'),
), 'toYYYYMM(event_month)', 'run_id, parent_record_id, record_id')

SOURCE_ROW = TableContract('trading_native_channel_exit_source_v1', _ENVELOPE + (
    ('decision_record_id','UUID'),('source_role','LowCardinality(String)'),
    ('source_ordinal','UInt32'),('source_build_id','String'),('session_date','Date'),
    ('ticker','String'),('source_bars_attempt_id','UUID'),('source_feature_attempt_id','UUID'),
    ('input_policy_digest','FixedString(64)'),('resolution_ms','UInt32'),
    ('bucket_index','UInt32'),('available_day_boundary_ms','UInt32'),
    ('close_int','UInt64'),('channel','String'),('channel_value','Nullable(Float64)'),
    ('content_hash','FixedString(64)'),
), 'toYYYYMM(event_month)', 'run_id, decision_record_id, source_role, source_ordinal, record_id')

EXECUTION_ROW = TableContract('trading_native_channel_exit_execution_v1', _ENVELOPE + (
    ('decision_record_id','UUID'),('execution_ordinal','UInt32'),
    ('source_execution_id','String'),('source_order_id','String'),('source_group_id','String'),
    ('account_id','String'),('ticker','String'),('conid','UInt64'),
    ('source_event_time',"DateTime64(6, 'UTC')"),('source_epoch_ms','UInt64'),
    ('quantity','Decimal(38, 10)'),('content_hash','FixedString(64)'),
), 'toYYYYMM(event_month)', 'run_id, decision_record_id, execution_ordinal, record_id')

TABLES = (DECISION, SOURCE_ROW, EXECUTION_ROW)
