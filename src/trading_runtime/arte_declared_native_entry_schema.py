"""Standalone normalized own-source contracts; no writer or projector imports."""
from .arte_journal_schema import TableContract

CONTRACT = 'declared-native-entry-companion@1'
_COMMON = (('record_id','UUID'),('parent_record_id','UUID'),('run_id','String'),
           ('event_month','Date'),('batch_id','UUID'))


def _table(name, columns):
    return TableContract(name, (*_COMMON, *columns, ('content_hash','FixedString(64)')),
                         'toYYYYMM(event_month)', 'run_id,parent_record_id,record_id')


ENTRY = _table('trading_declared_native_entry_v1', (
    ('companion_contract','String'),('strategy_number','UInt32'),('strategy_id','String'),('revision','UInt32'),
    ('intent_id','UUID'),('ticker','String'),('account_id','String'),('assignment_id','UUID'),('account_key','String'),('conid','UInt64'),
    ('assignment_plan_token','FixedString(64)'),('session_date','Date'),('boundary_ms','UInt32'),
    ('episode_start_ms','UInt32'),('source_token','FixedString(64)'),('configuration_hash','FixedString(64)'),
    ('managed_spec_hash','FixedString(64)'),('entry_request_policy_id','String'),('entry_request_hash','FixedString(64)'),
    ('quote_source_contract','String'),('source_build_id','FixedString(64)'),('market_plan_token','FixedString(64)'),
    ('candidate_plan_token','FixedString(64)'),('entry_plan_token','FixedString(64)'),('selection_token','FixedString(64)'),
    ('identity_token','FixedString(64)'),('bars_attempt_id','UUID'),('technical_attempt_id','UUID'),('broker_attempt_id','UUID'),
    ('reference_ask','Decimal(38,18)'),('initial_stop','Decimal(38,18)'),('initial_target','Decimal(38,18)'),
    ('target_level_id','String'),('frozen_gap','Decimal(38,18)'),('bos_break_boundary_ms','UInt32'),('bos_support_level_id','String'),
    ('first_numerator','UInt32'),('first_denominator','UInt32'),('current_numerator','UInt32'),('current_denominator','UInt32'),
    ('spread_policy_id','Nullable(String)'),('spread_numerator','Nullable(UInt32)'),('spread_denominator','Nullable(UInt32)'),
    ('bid_int','UInt64'),('ask_int','UInt64'),('quote_timestamp_us','Int64'),('quote_valid','UInt8'),
    ('predecessor_batch_id','UUID'),('predecessor_sequence','UInt64'),('predecessor_cursor','String'),
    ('portfolio_state_hash','FixedString(64)'),('broker_snapshot_hash','FixedString(64)')))
MOMENTUM = _table('trading_declared_native_entry_momentum_v1', (
    ('ordinal','UInt8'),('anchor','String'),('ticker','String'),('boundary_ms','UInt32'),
    ('source_build_id','FixedString(64)'),('source_attempt_id','UUID'),('market_plan_token','FixedString(64)'),
    ('resolution_ms','UInt32'),('current_boundary_ms','UInt32'),('prior_boundary_ms','UInt32'),
    ('current_line','Nullable(Float64)'),('current_signal','Nullable(Float64)'),
    ('prior_line','Nullable(Float64)'),('prior_signal','Nullable(Float64)')))
PRICE = _table('trading_declared_native_entry_first_price_v1', (
    ('ticker','String'),('first_setup_boundary_ms','UInt32'),('source_build_id','FixedString(64)'),
    ('bars_attempt_id','UUID'),('market_plan_token','FixedString(64)'),('current_boundary_ms','UInt32'),
    ('prior_boundary_ms','UInt32'),('current_close_int','UInt64'),('prior_high_int','UInt64'),
    ('current_price_valid','UInt8'),('prior_extremes_valid','UInt8')))
ACTIVITY = _table('trading_declared_native_entry_activity_v1', (
    ('ticker','String'),('session_date','Date'),('boundary_ms','UInt32'),('source_build_id','FixedString(64)'),
    ('bars_attempt_id','UUID'),('market_plan_token','FixedString(64)'),('activity_source_token','FixedString(64)'),
    ('candidate_plan_token','FixedString(64)'),('entry_plan_token','FixedString(64)'),('parent_selection_token','FixedString(64)')))
CANDLE = _table('trading_declared_native_entry_activity_candle_v1', (
    ('ordinal','UInt8'),('observed','UInt8'),('boundary_ms','Nullable(UInt32)'),('trade_count','Nullable(UInt64)')))
TABLES = (ENTRY, MOMENTUM, PRICE, ACTIVITY, CANDLE)
