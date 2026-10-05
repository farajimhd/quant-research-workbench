"""Shared prepared ladder journal schemas; installation/admission remain closed."""
from .arte_journal_schema import TableContract

_KEYS = (('record_id','UUID'), ('parent_record_id','UUID'), ('run_id','String'),
         ('event_month','Date'), ('batch_id','UUID'))
SETUP = TableContract('trading_squeeze_ladder_setup_evidence_v1', _KEYS + (
    ('ticker','String'), ('assignment_id','String'), ('session_date','Date'),
    ('admission_boundary_ms','UInt32'), ('qualification_boundary_ms','UInt32'), ('boundary_ms','UInt32'),
    ('market_plan_token','String'), ('scan_content_hash','FixedString(64)'),
    ('v7_plan_token','String'), ('pivot_plan_token','String'),
    ('resistance_id','String'), ('resistance_lower_bits','UInt64'), ('resistance_upper_bits','UInt64'),
    ('resistance_confirmed_epoch_ms','UInt64'), ('qualified_vwap_bits','UInt64'),
    ('resistance_comparison_int','Int64'), ('pivot_id','String'),
    ('pivot_boundary_ms','UInt32'), ('pivot_confirmed_boundary_ms','UInt32'),
    ('pivot_price_int','Int64'), ('stop_int','Int64'), ('stop_buffer_int','Int64'),
    ('previous_close_int','Int64'), ('close_int','Int64'), ('entry_limit_int','Int64'),
    ('target_count','UInt8'), ('content_hash','FixedString(64)')),
    'toYYYYMM(event_month)', 'run_id,parent_record_id,record_id')
TARGET = TableContract('trading_squeeze_ladder_target_evidence_v1', _KEYS + (
    ('ordinal','UInt8'), ('slice_id','String'), ('level_id','String'),
    ('price_bits','UInt64'), ('quantity_fraction_bits','UInt64'),
    ('content_hash','FixedString(64)')),
    'toYYYYMM(event_month)', 'run_id,parent_record_id,record_id')
TABLES = (SETUP, TARGET)
