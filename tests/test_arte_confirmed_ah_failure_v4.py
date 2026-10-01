"""Prepared factory/scalar persistence checks; no connected writer or replay."""
from dataclasses import replace
from datetime import date
from uuid import uuid4

import pytest

from src.trading_runtime.arte_confirmed_ah_failure_v4 import (
    CONFIRMED_AH_FAILURE, project_confirmed_ah_failure, restore_confirmed_ah_failure,
)
from src.trading_runtime.strategy_confirmed_ah_failure_exit import confirmed_ah_exit_intent
from src.trading_runtime.strategy_confirmed_ah_risk_failure import (
    ConfirmedAhRiskFailureInput, confirmed_ah_risk_failure,
)
from src.trading_runtime.strategy_followthrough_failure import FollowThroughFailureInput
from src.trading_runtime.strategy_one_stateful import StrategyOneFinancialView
from src.trading_runtime.strategy_engine import AssignmentStatus, StrategyPermissions


def prepared_case():
    five = FollowThroughFailureInput(
        43_700_000, 43_647_500, 2.08, 1.81, 43_700_000, 19_700, True,
        0.01971676900139796, 0.028269653367226016, 1.96, 1.97, 48, 42.0, False,
    )
    witness = confirmed_ah_risk_failure(ConfirmedAhRiskFailureInput(
        five, 43_700_000, True, 0.05043722423951946, 0.05238791450068928,
    ))
    financial = StrategyOneFinancialView(
        'assignment', 'account', 'WAFU', AssignmentStatus.MANAGING,
        StrategyPermissions(), 42.0, False, False, False, 1,
    )
    args = dict(session_date=date(2026, 8, 10), source_entry_intent_id=str(uuid4()))
    intent = confirmed_ah_exit_intent(witness, financial, **args)
    row = project_confirmed_ah_failure(
        witness, intent, financial, **args, run_id='development-run',
        batch_id=str(uuid4()), parent_record_id=str(uuid4()),
    )
    return witness, financial, args, intent, row


def test_factory_and_exact_complete_scalar_roundtrip():
    witness, financial, args, intent, row = prepared_case()
    assert confirmed_ah_exit_intent(witness, financial, **args) == intent
    assert restore_confirmed_ah_failure(row) == witness
    assert set(row) == {name for name, _ in CONFIRMED_AH_FAILURE.columns} - {'content_hash'}
    assert intent.event_time.isoformat() == '2026-08-10T20:08:20+00:00'
    assert intent.quantity == 42 and intent.reference_price == 1.96
    assert intent.metadata == {} and intent.execution_policy.quote_source == 'qmd'
    assert confirmed_ah_exit_intent(witness, replace(financial, ticker='OTHER'), **args).intent_id != intent.intent_id


@pytest.mark.parametrize('field,value', [
    ('strategy_number', 33), ('strategy_number', True),
    ('first_held_boundary_ms', '43647500'), ('first_held_boundary_ms', 43_699_900),
    ('completed_ten_second_boundary_ms', 43_710_000),
    ('ten_second_macd_line', 0.06), ('ten_second_macd_signal', float('nan')),
    ('reference_ask', True), ('bid', 2.02),
])
def test_scalar_restore_rejects_missing_or_altered_authority(field, value):
    *_, row = prepared_case()
    with pytest.raises(ValueError):
        restore_confirmed_ah_failure(dict(row, **{field: value}))


def test_factory_rejects_pending_exit_and_projection_rejects_altered_order():
    witness, financial, args, intent, row = prepared_case()
    with pytest.raises(ValueError):
        confirmed_ah_exit_intent(witness, replace(financial, pending_exit=True), **args)
    with pytest.raises(ValueError):
        project_confirmed_ah_failure(
            witness, replace(intent, quantity=43), financial, **args,
            run_id=row['run_id'], batch_id=row['batch_id'], parent_record_id=row['parent_record_id'],
        )


def prepared_source_graph(monkeypatch):
    """Explicit mock of the separately tested committed-entry loader, not CH."""
    witness, financial, args, intent, row = prepared_case()
    from src.trading_runtime.arte_intent_projection import project_strategy_intent
    from src.trading_runtime.arte_journal_commit_v4 import V4CommittedPrefix
    parent = dict(project_strategy_intent(intent).core)
    at = parent.pop('event_time')
    parent.update(record_id=row['parent_record_id'], run_id=row['run_id'],
                  batch_id=row['batch_id'], event_month=row['event_month'],
                  account_id=financial.account_id)
    event = dict(record_id=row['parent_record_id'], run_id=row['run_id'],
                 batch_id=row['batch_id'], event_time=at, sequence=65,
                 entity_id=intent.intent_id, account_id=financial.account_id)
    preceding = str(uuid4())
    prefix = V4CommittedPrefix(row['run_id'], 64, preceding, 'cursor', 'running', (preceding,))
    source = dict(intent_id=args['source_entry_intent_id'], ticker='WAFU',
                  action='enter_long', reason='strategy_one_entry',
                  reference_price=2.08, invalidation_price=1.81)
    source_event = dict(account_id=financial.account_id, sequence=4)
    child = dict(strategy_number=34, assignment_id=financial.assignment_id,
                 boundary_ms=43_647_400)
    def entry_loader(client, run_id, intent_id, **context):
        assert context['verified_prefix'] == prefix
        assert context['prior_batch_id'] == preceding
        assert intent_id == args['source_entry_intent_id']
        return source, source_event, child
    monkeypatch.setattr('src.trading_runtime.arte_followthrough_failure_v4._source_entry', entry_loader)
    return witness, row, parent, event, prefix, source, source_event, child


def test_prepared_source_graph_retains_original_authority(monkeypatch):
    from src.trading_runtime.strategy_confirmed_ah_failure_source import validate_confirmed_ah_source
    witness, row, parent, event, prefix, *_ = prepared_source_graph(monkeypatch)
    assert validate_confirmed_ah_source(None, row, parent, event, verified_prefix=prefix) == witness


@pytest.mark.parametrize('target,field,value', [
    ('source', 'reference_price', 2.09), ('source', 'invalidation_price', 1.80),
    ('source', 'ticker', 'OTHER'), ('source_event', 'account_id', 'OTHER'),
    ('child', 'assignment_id', 'OTHER'), ('child', 'strategy_number', 33),
    ('child', 'boundary_ms', 43_647_500), ('event', 'sequence', 64),
    ('parent', 'record_id', 'foreign'), ('parent', 'execution_quote_source', 'OTHER'),
])
def test_prepared_source_graph_rejects_ancestry_identity_and_policy_changes(monkeypatch, target, field, value):
    from src.trading_runtime.strategy_confirmed_ah_failure_source import validate_confirmed_ah_source
    _, row, parent, event, prefix, source, source_event, child = prepared_source_graph(monkeypatch)
    objects = dict(source=source, source_event=source_event, child=child, event=event, parent=parent)
    objects[target][field] = value
    with pytest.raises(ValueError):
        validate_confirmed_ah_source(None, row, parent, event, verified_prefix=prefix)


def prepared_transport(monkeypatch):
    from src.trading_runtime.arte_journal_writer import TypedJournalBatch
    _, row, parent, event, prefix, *_ = prepared_source_graph(monkeypatch)
    event.update(category='strategy', entity_type='strategy_intent')
    base = TypedJournalBatch(
        row['run_id'], date(2026, 8, 1), str(uuid4()), row['batch_id'],
        prefix.last_batch_id, 65, 65, 'cursor', 'running', (event,), intents=(parent,),
    )
    return row, base


def test_prepared_transport_freezes_witness_and_retains_exact_factory(monkeypatch):
    from src.trading_runtime.arte_confirmed_ah_failure_v4 import V4ConfirmedAhFailureBatch
    row, base = prepared_transport(monkeypatch)
    unit = V4ConfirmedAhFailureBatch(base, row)
    row['ten_second_macd_line'] = 0.06
    assert unit.confirmation['ten_second_macd_line'] != row['ten_second_macd_line']
    with pytest.raises(TypeError):
        unit.confirmation['bid'] = 2.02


@pytest.mark.parametrize('target,field,value', [
    ('row', 'record_id', str(uuid4())), ('row', 'parent_record_id', str(uuid4())),
    ('row', 'batch_id', str(uuid4())), ('row', 'run_id', 'foreign'),
    ('event', 'sequence', 66), ('event', 'account_id', 'foreign'),
    ('event', 'event_time', '2026-08-10T20:08:25+00:00'),
    ('event', 'entity_type', 'signal'), ('parent', 'execution_quote_source', 'foreign'),
])
def test_prepared_transport_rejects_altered_envelope(monkeypatch, target, field, value):
    from src.trading_runtime.arte_confirmed_ah_failure_v4 import V4ConfirmedAhFailureBatch
    row, base = prepared_transport(monkeypatch)
    if target == 'row':
        row[field] = value
    elif target == 'event':
        base = replace(base, events=(dict(base.events[0], **{field: value}),))
    else:
        base = replace(base, intents=(dict(base.intents[0], **{field: value}),))
    with pytest.raises(ValueError):
        V4ConfirmedAhFailureBatch(base, row)


def test_prepared_family_graph_matches_all_parents_and_freezes_verified_shape(monkeypatch):
    from src.trading_runtime.strategy_confirmed_ah_failure_source import validate_confirmed_ah_rows
    _, row, parent, event, prefix, *_ = prepared_source_graph(monkeypatch)
    result = validate_confirmed_ah_rows(None, [row], [parent], [event], verified_prefix=prefix)
    assert dict(result[0]) == row
    with pytest.raises(TypeError):
        result[0]['bid'] = 2.02
    assert validate_confirmed_ah_rows(None, [], [], [], verified_prefix=None) == ()


@pytest.mark.parametrize('change', ['missing', 'extra', 'duplicate_parent', 'duplicate_event', 'child_id', 'extra_field'])
def test_prepared_family_graph_rejects_incomplete_or_ambiguous_admission(monkeypatch, change):
    from src.trading_runtime.strategy_confirmed_ah_failure_source import validate_confirmed_ah_rows
    _, row, parent, event, prefix, *_ = prepared_source_graph(monkeypatch)
    rows, parents, events = [row], [parent], [event]
    if change == 'missing': rows = []
    elif change == 'extra': rows.append(dict(row))
    elif change == 'duplicate_parent': parents.append(dict(parent))
    elif change == 'duplicate_event': events.append(dict(event))
    elif change == 'child_id': row['record_id'] = str(uuid4())
    else: row['undeclared'] = 1
    with pytest.raises(ValueError):
        validate_confirmed_ah_rows(None, rows, parents, events, verified_prefix=prefix)


def test_registered_scalar_hash_roundtrip_and_tamper_rejection():
    from src.trading_runtime.arte_journal_writer import typed_row
    from src.trading_runtime.arte_intent_projection import _verify_stored_row
    witness, *_, row = prepared_case()
    sealed = typed_row(CONFIRMED_AH_FAILURE.name, row)
    canonical = _verify_stored_row(CONFIRMED_AH_FAILURE.name, sealed)
    # Hash validation precedes any adaptation from native UInt spellings.
    integer_names = {name for name, kind in CONFIRMED_AH_FAILURE.columns if kind.startswith('UInt')}
    adapted = {k: int(v) if k in integer_names else v for k, v in canonical.items()}
    assert restore_confirmed_ah_failure(adapted) == witness
    with pytest.raises(RuntimeError, match='committed hash'):
        _verify_stored_row(CONFIRMED_AH_FAILURE.name, dict(sealed, ten_second_macd_signal=0.06))
    with pytest.raises(ValueError):
        typed_row(CONFIRMED_AH_FAILURE.name, dict(row, undeclared=1))


def test_prepared_graph_sealing_uses_registered_native_hashing(monkeypatch):
    from src.trading_runtime.arte_confirmed_ah_failure_v4 import seal_confirmed_ah_rows
    from src.trading_runtime.arte_intent_projection import _verify_stored_row
    _, row, parent, event, prefix, *_ = prepared_source_graph(monkeypatch)
    sealed = seal_confirmed_ah_rows(None, [row], [parent], [event], verified_prefix=prefix)
    assert len(sealed) == 1 and sealed[0]['content_hash']
    _verify_stored_row(CONFIRMED_AH_FAILURE.name, sealed[0])
    with pytest.raises(ValueError):
        seal_confirmed_ah_rows(None, [dict(row, initial_stop=1.82)], [parent], [event], verified_prefix=prefix)


def test_compound_retains_confirmation_and_blocks_incomplete_publication(monkeypatch):
    from types import SimpleNamespace
    from src.trading_runtime.arte_confirmed_ah_failure_v4 import V4ConfirmedAhFailureBatch
    from src.trading_runtime.arte_journal_compound_v4 import coalesce_v4_units, prepare_compound_v4_families
    row, base = prepared_transport(monkeypatch)
    unit = V4ConfirmedAhFailureBatch(base, row)
    next_id = str(uuid4())
    event = dict(base.events[0], record_id=str(uuid4()), sequence=66, batch_id=next_id)
    continuation = replace(base, batch_id=next_id, prior_batch_id=base.batch_id,
                           first_sequence=66, last_sequence=66, events=(event,), intents=())
    compound = coalesce_v4_units((unit, continuation))
    assert len(compound.children['confirmed_ah_failures']) == 1
    assert compound.children['confirmed_ah_failures'][0]['ten_second_macd_line'] == row['ten_second_macd_line']
    assert unit.confirmation['batch_id'] == base.batch_id
    with pytest.raises(ValueError, match='complete native commit registration'):
        prepare_compound_v4_families(SimpleNamespace(), compound)


def test_cold_scalar_loader_verifies_hash_before_native_uint_adaptation(monkeypatch):
    from src.trading_runtime.arte_confirmed_ah_failure_v4 import load_confirmed_ah_failure
    from src.trading_runtime.arte_journal_writer import typed_row
    from src.trading_runtime.arte_journal_commit_v4 import V4CommittedPrefix
    witness, *_, row = prepared_case()
    stored = typed_row(CONFIRMED_AH_FAILURE.name, row)
    stored['strategy_number'] = '34'
    prefix = V4CommittedPrefix(row['run_id'], 65, row['batch_id'], 'cursor', 'completed', (row['batch_id'],))
    def read(client, query):
        assert 'LIMIT 2 FORMAT JSONEachRow' in query and row['parent_record_id'] in query
        return [stored]
    monkeypatch.setattr('src.trading_runtime.arte_journal_writer._rows', read)
    raw, restored = load_confirmed_ah_failure(None, prefix, row['parent_record_id'])
    assert raw['strategy_number'] == '34' and restored == witness
    stored['ten_second_macd_signal'] = 0.06
    with pytest.raises(RuntimeError, match='committed hash'):
        load_confirmed_ah_failure(None, prefix, row['parent_record_id'])
    stored['batch_id'] = str(uuid4())
    with pytest.raises(RuntimeError, match='committed prefix'):
        load_confirmed_ah_failure(None, prefix, row['parent_record_id'])
