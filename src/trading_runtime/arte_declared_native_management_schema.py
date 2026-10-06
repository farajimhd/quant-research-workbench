"""Standalone normalized own-source contracts; no writer or projector imports."""
from .arte_journal_schema import TableContract

CONTRACT = 'declared-native-management-companion@1'
_COMMON = (('record_id','UUID'),('parent_record_id','UUID'),('run_id','String'),
           ('event_month','Date'),('batch_id','UUID'))

def _table(name, columns):
    return TableContract(name, (*_COMMON,*columns,('content_hash','FixedString(64)')),
                         'toYYYYMM(event_month)','run_id,parent_record_id,record_id')

CONTEXT = _table('trading_declared_native_management_v1', (
    ('companion_contract','String'),('command_type','String'),('result_hash','FixedString(64)'),
    ('entry_record_id','UUID'),('entry_intent_id','UUID'),('strategy_id','String'),('revision','UInt32'),
    ('account_id','String'),('assignment_id','UUID'),('ticker','String'),('session_date','Date'),
    ('configuration_hash','FixedString(64)'),('managed_spec_hash','FixedString(64)'),
    ('core_spec_token','FixedString(64)'),('source_token','FixedString(64)'),('boundary_ms','UInt32'),
    ('first_held_boundary_ms','Nullable(UInt32)'),('source_build_id','String'),
    ('source_market_plan_token','FixedString(64)'),('source_bars_attempt_id','UUID'),
    ('source_indicators_attempt_id','UUID'),('source_liquidity_attempt_id','UUID'),
    ('predecessor_batch_id','UUID'),('predecessor_sequence','UInt64'),('predecessor_cursor','String'),
    ('portfolio_state_hash','FixedString(64)'),('broker_snapshot_hash','FixedString(64)'),
    ('financial_status','String'),('position_quantity','Float64'),('position_quantity_is_int','UInt8'),
    *((name,'UInt8') for name in ('pending_entry','pending_exit','pending_capital_request')),
    *((name,'UInt32') for name in ('completed_entries','reentry_not_before_ms','current_purchase_groups')),
    *((f'permission_{name}','UInt8') for name in ('observe','enter','add','reduce','exit','reenter')),
))
_COMPLETED = (('boundary_ms','UInt32'),('first_held_boundary_ms','UInt32'),('reference_ask','Float64'),
    ('initial_stop','Float64'),('completed_five_second_boundary_ms','Nullable(UInt32)'),
    ('completed_five_second_close_int','Nullable(UInt64)'),('price_valid','UInt8'),
    ('macd_line','Nullable(Float64)'),('macd_signal','Nullable(Float64)'),('bid','Nullable(Float64)'),
    ('ask','Nullable(Float64)'),('quote_age_us','Nullable(UInt64)'),('position_quantity','Float64'),
    ('position_quantity_is_int','UInt8'),('pending_exit','UInt8'))
COMPLETED = _table('trading_declared_native_management_completed_v1', _COMPLETED)
# These are the explicit scalar producer fields supported by this packet.
# Presence is distinct from nullable/unavailable and from a numeric zero.
_RESOLUTION = (('ticker','String'),('boundary_ms','UInt32'),('price_valid','UInt8'),('extremes_valid','UInt8'),
    ('quote_valid','UInt8'),('quote_timestamp_us','UInt64'),('open_int','UInt64'),('high_int','UInt64'),
    ('low_int','UInt64'),('close_int','UInt64'),('bid_int','UInt64'),('ask_int','UInt64'),
    ('bid_size','UInt64'),('ask_size','UInt64'),('trade_count','UInt64'),('volume','UInt64'),
    ('macd_line','Nullable(Float64)'),('macd_signal','Nullable(Float64)'))
RESOLUTION = _table('trading_declared_native_management_resolution_v1',(
    ('ordinal','UInt32'),('resolution_ms','UInt32'),
    *((name,'Nullable('+kind+')') if not kind.startswith('Nullable') else (name,kind) for name,kind in _RESOLUTION),
    *((name+'_present','UInt8') for name,_ in _RESOLUTION)))
CANDLE = _table('trading_declared_native_management_candle_v1', (
    ('ordinal','UInt32'),('boundary_ms','UInt32'),('trade_count','UInt64')))
ARM = _table('trading_declared_native_management_arm_v1', (
    ('source_token','FixedString(64)'),('snapshot_id','UUID'),('checkpoint_sequence','UInt64'),
    ('journal_batch_id','UUID'),('snapshot_hash','FixedString(64)'),('boundary_ms','UInt32'),
    ('first_held_boundary_ms','UInt32'),('reference_ask','Float64'),('initial_stop','Float64'),('high_int','UInt64')))
PROTECTION = _table('trading_declared_native_management_protection_v1', (
    ('now_ms','UInt32'),('bid','Float64'),('ask','Float64'),('tick','Float64'),
    ('low_boundary_ms','Nullable(UInt32)'),('low_int','Nullable(UInt64)'),
    ('low_price_valid','UInt8'),('low_extremes_valid','UInt8'),('price_bearing_bar','UInt8')))
STATE = _table('trading_declared_native_management_state_v1', (
    ('phase','String'),('boundary_ms','UInt32'),('stop','Float64'),('target','Float64'),
    ('earned_groups','UInt32'),('applied_groups','UInt32')))
ACCEPTED = _table('trading_declared_native_management_accepted_v1', (
    ('phase','String'),('group','String'),('ordinal','UInt32'),('unified_level_id','String'),
    ('lower','Nullable(Float64)'),('upper','Nullable(Float64)')))
_LEVEL = (('unified_level_id','String'),('lower','Float64'),('upper','Float64'),
          ('role','String'),('side','String'),('transition_from','String'))
LEVEL = _table('trading_declared_native_management_level_v1', (
    ('group','String'),('ordinal','UInt32'),('completed_boundary_ms','Nullable(UInt32)'),
    *((name,'Nullable('+kind+')') for name,kind in _LEVEL),
    *((name+'_present','UInt8') for name,_ in _LEVEL)))
TABLES = (CONTEXT,COMPLETED,RESOLUTION,CANDLE,ARM,PROTECTION,STATE,ACCEPTED,LEVEL)
