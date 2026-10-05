"""Independent source-reproduced entry cost companion; never journal authority."""
from datetime import date
from decimal import Decimal
from uuid import UUID, NAMESPACE_URL, uuid5
from .arte_journal_schema import TableContract

ENTRY_SPREAD_RISK = TableContract('trading_entry_spread_risk_v4', (
    ('record_id', 'UUID'), ('parent_record_id', 'UUID'), ('run_id', 'String'),
    ('event_month', 'Date'), ('batch_id', 'UUID'), ('strategy_number', 'UInt32'),
    ('ticker', 'String'), ('session_date', 'Date'), ('boundary_ms', 'UInt32'),
    ('source_build_id', 'FixedString(64)'), ('liquidity_attempt_id', 'UUID'),
    ('market_plan_token', 'FixedString(64)'), ('parent_gate_token', 'FixedString(64)'),
    ('source_plan_token', 'FixedString(64)'), ('policy_id', 'String'),
    ('ratio_numerator', 'UInt32'), ('ratio_denominator', 'UInt32'),
    ('bid_int', 'UInt64'), ('ask_int', 'UInt64'), ('original_stop_int', 'UInt64'),
    ('quote_timestamp_us', 'Int64'), ('content_hash', 'FixedString(64)')),
    'toYYYYMM(event_month)', 'run_id,parent_record_id,record_id')


def project_entry_spread_risk(witness, *, run_id, batch_id, parent_record_id,
                             event_month, strategy_number):
    from src.backend.backtest_entry_spread_risk import EntrySpreadRiskWitness
    from src.backend.backtest_market_data import market_day_boundary
    from .entry_spread_risk import entry_spread_risk_allowed, exact_epoch_us
    from .numbered_fixed_strategy import numbered_fixed_strategy
    if (type(witness) is not EntrySpreadRiskWitness or type(strategy_number) is not int
            or numbered_fixed_strategy(strategy_number).entry_spread_risk_policy != witness.policy
            or not entry_spread_risk_allowed(witness.policy, bid_int=witness.bid_int,
                                             ask_int=witness.ask_int, original_stop_int=witness.original_stop_int)
            or type(run_id) is not str or not run_id):
        raise ValueError('Entry cost evidence lacks exact declared source witness')
    for value in (batch_id, parent_record_id):
        if type(value) is not str or str(UUID(value)) != value or not UUID(value).int:
            raise ValueError('Entry cost requires canonical parent identities')
    instant = market_day_boundary(date.fromisoformat(witness.session_date), witness.boundary_ms)
    if (event_month != instant.date().replace(day=1).isoformat()
            or not 0 <= exact_epoch_us(instant) - witness.quote_timestamp_us <= 1_000_000):
        raise ValueError('Entry cost quote has stale or future native clock')
    row = dict(record_id=str(uuid5(NAMESPACE_URL, parent_record_id + ':entry-spread-risk')),
        parent_record_id=parent_record_id, run_id=run_id, batch_id=batch_id,
        event_month=event_month, strategy_number=strategy_number,
        policy_id=witness.policy.policy_id,
        ratio_numerator=witness.policy.maximum_spread_original_risk[0],
        ratio_denominator=witness.policy.maximum_spread_original_risk[1])
    for name in ('ticker', 'session_date', 'boundary_ms', 'source_build_id',
                 'liquidity_attempt_id', 'market_plan_token', 'parent_gate_token',
                 'source_plan_token', 'bid_int', 'ask_int', 'original_stop_int', 'quote_timestamp_us'):
        row[name] = getattr(witness, name)
    return row


def seal_certified_entry_spread_risk_rows(rows, entries, intents, events, *, run_id, source=None):
    from .numbered_fixed_strategy import numbered_fixed_strategy
    from .arte_journal_writer import typed_row, _datetime_wire
    from src.backend.backtest_declared_entry_quote_source import entry_spread_risk_authority_type
    from src.backend.backtest_market_data import market_day_boundary
    from .entry_spread_risk import canonical_price_int
    required = tuple(e for e in entries if numbered_fixed_strategy(e['strategy_number']).entry_spread_risk_policy is not None)
    if not required:
        if rows:
            raise ValueError('Entry cost evidence has no declared parent')
        return ()
    if (len({e['strategy_number'] for e in required}) != 1
            or type(source) is not entry_spread_risk_authority_type(required[0]['strategy_number']) or source.run_id != run_id
            or source.strategy_number != required[0]['strategy_number']):
        raise ValueError('Entry cost requires exact independent run source')
    parents = {e['parent_record_id']: e for e in required}
    intent_map = {e['record_id']: e for e in intents}
    event_map = {e['record_id']: e for e in events}
    if len(parents) != len(required) or len(intent_map) != len(intents) or len(event_map) != len(events):
        raise ValueError('Entry cost graph has duplicate identities')
    sealed = tuple(typed_row(ENTRY_SPREAD_RISK.name, {k:v for k,v in r.items() if k != 'content_hash'}) for r in rows)
    if len(sealed) != len(parents) or len({r['parent_record_id'] for r in sealed}) != len(sealed):
        raise ValueError('Entry cost companions omit or duplicate accepted entries')
    for old, row in zip(rows, sealed):
        entry = parents.get(row['parent_record_id'])
        if entry is None or ('content_hash' in old and old['content_hash'] != row['content_hash']):
            raise ValueError('Entry cost companion is extra or corrupted')
        intent = intent_map.get(entry['parent_record_id'])
        event = event_map.get(entry['parent_record_id'])
        if intent is None or event is None:
            raise ValueError('Entry cost graph lacks intent or event')
        witness = source.witness(intent['ticker'], entry['boundary_ms'])
        expected = typed_row(ENTRY_SPREAD_RISK.name, project_entry_spread_risk(witness,
            run_id=run_id, batch_id=entry['batch_id'], parent_record_id=entry['parent_record_id'],
            event_month=str(entry['event_month']), strategy_number=entry['strategy_number']))
        if row != expected or intent is None or event is None:
            raise ValueError('Entry cost differs from independently reconstructed source')
        identity = (f"strategy-{entry['strategy_number']}:{witness.session_date}:{entry['assignment_id']}:"
                    f"{intent['account_id']}:{witness.ticker}:{witness.boundary_ms}:{entry['episode_start_ms']}")
        if (intent['action'] != 'enter_long' or intent['reason'] != 'strategy_one_entry'
                or intent['intent_id'] != str(uuid5(NAMESPACE_URL, identity))
                or canonical_price_int(Decimal(str(intent['reference_price']))) != witness.ask_int
                or canonical_price_int(Decimal(str(intent['invalidation_price']))) != witness.original_stop_int
                or event['entity_id'] != intent['intent_id'] or event['account_id'] != intent['account_id']
                or event['category'] != 'strategy' or event['entity_type'] != 'strategy_intent'
                or any(str(intent[n]) != str(entry[n]) or str(event[n]) != str(entry[n])
                       for n in ('run_id', 'batch_id', 'event_month'))):
            raise ValueError('Entry cost graph differs from original accepted intent')
        instant = market_day_boundary(date.fromisoformat(witness.session_date), witness.boundary_ms)
        try:
            wire = _datetime_wire(event['event_time'], 9)
        except ValueError:
            wire = _datetime_wire(event['event_time'], 9, stored_utc=True)
        if wire != _datetime_wire(instant, 9):
            raise ValueError('Entry cost event clock differs from exact proposal')
    return sealed
