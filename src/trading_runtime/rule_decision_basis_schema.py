"""Staged, centrally owned causal decision references for future rule executors.

Shared across strategies; no strategy may define or create these tables.
Excluded from active journal TABLES until projection, writer and cold audit are
integrated. Importing this module performs no DDL or registration.
"""
from .arte_journal_schema import TableContract


BASIS = TableContract(
    "trading_rule_decision_basis_v1",
    (("run_id", "String"), ("event_month", "Date"),
     ("batch_id", "UUID"), ("record_id", "UUID"), ("decision_id", "FixedString(64)"),
     ("account_id", "String"), ("assignment_id", "String"), ("listing_id", "String"),
     ("release_id", "UUID"), ("release_content_hash", "FixedString(64)"),
     ("source_session_date", "Date"), ("source_batch_sequence", "UInt64"),
     ("source_commit_hash", "FixedString(64)"), ("history_input_hash", "FixedString(64)"),
     ("evaluation_block", "String"), ("observed_ordinal", "UInt64"),
     ("boundary_at", "DateTime64(6, 'UTC')"), ("basis_sequence", "UInt64"),
     ("decision_sequence", "UInt64"), ("terminal", "UInt8"),
     ("selection_status", "LowCardinality(String)"), ("intent_count", "UInt16"),
     ("intent_hash", "FixedString(64)"), ("content_hash", "FixedString(64)")),
    "toYYYYMM(event_month)", "run_id, decision_sequence, decision_id",
)
INTENT = TableContract(
    "trading_rule_decision_intent_v1",
    (("run_id", "String"), ("event_month", "Date"),
     ("batch_id", "UUID"), ("record_id", "UUID"), ("decision_id", "FixedString(64)"),
     ("parent_record_id", "UUID"),
     ("ordinal", "UInt16"), ("stage", "LowCardinality(String)"),
     ("strategy_intent_id", "String"), ("content_hash", "FixedString(64)")),
    "toYYYYMM(event_month)", "run_id, decision_id, ordinal",
)
OBSERVATION = TableContract(
    'trading_rule_decision_observation_v1',
    (("run_id", "String"), ("event_month", "Date"), ("batch_id", "UUID"),
     ("record_id", "UUID"), ("decision_id", "FixedString(64)"),
     ("account_id", "String"), ("assignment_id", "String"), ("listing_id", "String"),
     ("ticker", "LowCardinality(String)"), ("observed_at", "DateTime64(6, 'UTC')"),
     ("pending_acquisition", "UInt8"), ("pending_reduction", "UInt8"),
     ("high_water_price_bits", "UInt64"),
     ("working_order_count", "UInt16"), ("working_order_hash", "FixedString(64)"),
     ("unobserved_order_count", "UInt16"), ("unobserved_order_hash", "FixedString(64)"),
     ("content_hash", "FixedString(64)")),
    'toYYYYMM(event_month)', 'run_id, record_id')
OBSERVED_ORDER = TableContract(
    'trading_rule_decision_observed_order_v1',
    (("run_id", "String"), ("event_month", "Date"), ("batch_id", "UUID"),
     ("record_id", "UUID"), ("parent_record_id", "UUID"), ("ordinal", "UInt16"),
     ("broker_order_id", "String"), ("role", "LowCardinality(String)"),
     ("status", "String"), ("remaining_quantity_bits", "UInt64"),
     ("limit_price_bits", "UInt64"), ("stop_price_bits", "UInt64"),
     ("content_hash", "FixedString(64)")),
    'toYYYYMM(event_month)', 'run_id, parent_record_id, ordinal')
UNOBSERVED_ORDER = TableContract(
    'trading_rule_decision_unobserved_order_v1',
    (("run_id", "String"), ("event_month", "Date"), ("batch_id", "UUID"),
     ("record_id", "UUID"), ("parent_record_id", "UUID"), ("ordinal", "UInt16"),
     ("broker_order_id", "String"), ("content_hash", "FixedString(64)")),
    'toYYYYMM(event_month)', 'run_id, parent_record_id, ordinal')
OBSERVATION_TABLES = (OBSERVATION, OBSERVED_ORDER, UNOBSERVED_ORDER)
STAGED_TABLES = (BASIS, INTENT, *OBSERVATION_TABLES)
