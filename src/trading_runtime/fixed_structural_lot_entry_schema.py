"""Pure normalized selected entry contracts. No installation or authority."""
from .arte_journal_schema import TableContract

CONTRACT = 'fixed-structural-lot-entry@1'
COMMON = (('record_id','UUID'),('parent_record_id','UUID'),('root_record_id','UUID'),
          ('run_id','String'),('event_month','Date'),('batch_id','UUID'),('sequence','UInt64'))


def _table(name,columns):
    return TableContract(name,(*COMMON,*columns,('content_hash','FixedString(64)')),
        'toYYYYMM(event_month)','run_id,parent_record_id,record_id')


ENTRY = _table('trading_fixed_structural_lot_entry_v1',(
    ('companion_contract','String'),('intent_id','UUID'),('original_intent_id','UUID'),
    ('strategy_id','String'),('revision','UInt32'),('account_id','String'),('assignment_id','String'),
    ('session_date','Date'),('ticker','String'),('boundary_ms','UInt32'),('episode_start_ms','UInt32'),
    ('reference_ask','Float64'),('initial_stop','Float64'),('original_target','Float64'),
    ('target_level_id','String'),('tick','Float64'),
    ('source_build_id','String'),('interval_token','FixedString(64)'),
    ('source_attempt_id','UUID'),('bars_attempt_id','UUID'),('source_checkpoint_hash','FixedString(64)'),
    ('decoded_seed_hash','FixedString(64)'),('seed_source_plan_hash','FixedString(64)'),
    ('seed_input_policy','String'),('split_evidence_hash','FixedString(64)'),
    ('clock_count','UInt32'),('interval_count','UInt32'),('clock_hash','FixedString(64)'),('interval_hash','FixedString(64)'),
    ('market_plan_token','FixedString(64)'),('gate_token','FixedString(64)'),('broker_attempt_id','UUID'),
    ('bid_int','UInt64'),('ask_int','UInt64'),('quote_timestamp_us','UInt64'),('quote_valid','UInt8'),
    ('parent_attempt_id','UUID'),('parent_token','FixedString(64)'),('parent_configuration_hash','FixedString(64)'),
    ('parent_node_hash','FixedString(64)'),('parent_source_candidate_id','String'),('parent_source_candidate_hash','FixedString(64)'),
    ('selected_configuration_hash','FixedString(64)'),('policy_version','UInt32'),('lot_count','UInt8'),
    ('allocation','String'),('target_selection','String'),('target_price_rule','String'),('target_management','String'),
    ('stop_management','String'),('aggregate_exit','String'),('entry_reentry','String'),('profile_id','String'),
    ('lot_hash','FixedString(64)'),('parent_node_count','UInt32'),('selected_node_count','UInt32'),
    ('proposal_node_count','UInt32'),('configuration_nodes_hash','FixedString(64)'),('proposal_hash','FixedString(64)')))
LOT = _table('trading_fixed_structural_lot_entry_lot_v1',(
    ('ordinal','UInt8'),('slice_id','String'),('weight_numerator','UInt32'),('weight_denominator','UInt32'),
    ('initial_stop','Float64'),('fixed_target','Float64'),('level_id','String'),('lower','Float64'),('upper','Float64'),
    ('confirmed_at_ms','UInt64'),('historical','UInt8'),('role','String'),('transition_from','String')))
NODE = _table('trading_fixed_structural_lot_configuration_node_v1',(
    ('tree_kind','String'),('node_id','UInt32'),('parent_node_id','Nullable(UInt32)'),
    ('child_key','Nullable(String)'),('child_ordinal','Nullable(UInt32)'),('value_kind','String'),
    ('text_value','Nullable(String)'),('int_value','Nullable(Int64)'),('float_value','Nullable(Float64)'),
    ('bool_value','Nullable(UInt8)')))
TABLES = (ENTRY,LOT,NODE)
TREE_KINDS = ('parent_configuration','selected_configuration','proposal')
