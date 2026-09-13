import json
from datetime import datetime, timezone
from src.trading_runtime.journal import TradingJournal
from src.trading_runtime.journal_evidence import REFERENCE


def test_compact_projection_retains_frozen_entry_chart_references():
    from src.trading_runtime.journal_evidence import activity_payload
    level = {'unified_level_id': 'r1', 'price': 12, 'entry_boundary': 12.1,
             'evidence': {'large_book': [1] * 1000}}
    snapshot = {'session_high': 13, 'selected_at': '2026-09-08T08:00:00Z',
                'frozen_at_entry': True, 'interval_based': True, 'levels': [level]}
    projected = activity_payload({'unified_structural_trigger': {'current_snapshot': snapshot}})
    current = projected['unified_structural_trigger']['current_snapshot']
    assert current['session_high'] == 13 and current['frozen_at_entry']
    assert current['levels'] == [{'unified_level_id': 'r1', 'price': 12, 'entry_boundary': 12.1}]


def test_evidence_roundtrip_compact_read_reopen_and_integrity(tmp_path):
    path = tmp_path / 'journal.sqlite3'
    journal = TradingJournal(path)
    payload = {'ticker': 'TEST', 'action': 'enter_long', 'metadata': {
        'profit_target': 12, 'unified_structural_trigger': {'levels': [
            {'price': n, 'evidence': 'original' * 100} for n in range(100)]}}}
    for _ in range(3):
        journal.append(run_id='run', category='strategy', entity_type='strategy_intent',
                       entity_id='entry', payload=payload, event_time=datetime.now(timezone.utc))
    stored = journal._fetchall('SELECT payload_json FROM journal')
    assert all(REFERENCE in row['payload_json'] for row in stored)
    assert journal._fetchone('SELECT count(*) n FROM journal_evidence')['n'] == 2
    assert journal.records('run')[0].payload['metadata'] == payload['metadata']
    compact = journal.strategy_activity_records(run_id='run', compact=True)
    assert compact[0].payload['metadata']['profit_target'] == 12
    assert len(json.dumps(compact[0].payload)) < 1000
    journal.close()
    journal = TradingJournal(path, read_only=True)
    assert journal.records('run')[0].payload['metadata'] == payload['metadata']
    journal.close()


def test_missing_evidence_fails_closed(tmp_path):
    import pytest
    journal = TradingJournal(tmp_path / 'journal.sqlite3')
    journal.append(run_id='run', category='strategy', entity_type='strategy_intent', entity_id='entry',
                   payload={'metadata': {'profit_target_selection': {'price': 12}}})
    with journal._connection:
        journal._connection.execute('DELETE FROM journal_evidence')
    with pytest.raises(ValueError, match='Missing or corrupt'):
        journal.records('run')
    journal.close()


def test_execution_references_are_idempotent_and_recover_original_evidence(tmp_path):
    journal = TradingJournal(tmp_path / 'execution.sqlite3')
    metadata = {'action': 'enter_long', 'protective_stop_selection': {
        'level': {'price': 12, 'references': [{'price': 11}]}}}
    compact = journal.reference_evidence(metadata)
    count = journal._fetchone('SELECT count(*) n FROM journal_evidence')['n']
    assert REFERENCE in compact['protective_stop_selection']
    assert journal.reference_evidence(compact) == compact
    journal.append(run_id='run', category='execution', entity_type='fill', entity_id='fill',
                   payload={'canonical_metadata': compact})
    assert journal._fetchone('SELECT count(*) n FROM journal_evidence')['n'] == count
    assert journal.records('run')[0].payload['canonical_metadata'] == metadata
    journal.close()


def test_checkpoint_and_oms_state_recover_complete_evidence(tmp_path):
    journal = TradingJournal(tmp_path / 'recovery.sqlite3')
    state = {'profit_target_selection': {'references': [{'price': 12}], 'target': 12}}
    at = datetime.now(timezone.utc)
    journal.save_checkpoint('run', 'cursor', state, at)
    journal.save_order_management_state('group', run_id='run', account_id='sim', state=state)
    journal.save_portfolio_state('sim', state)
    assert journal.load_checkpoint('run')['state'] == state
    assert journal.order_management_states(run_id='run')[0]['state'] == state
    assert journal.portfolio_states()['sim'] == state
    journal.close()


def test_execution_externalized_chart_plan_survives_new_and_legacy_compact_reads(tmp_path):
    path=tmp_path/'chart.sqlite3'
    journal=TradingJournal(path)
    metadata={'unified_structural_trigger':{'current_snapshot':{
        'session_high':4.,'frozen_at_entry':True,'levels':[
            {'unified_level_id':'r','price':3.61,'entry_boundary':3.61,'fit':{'observations':[1]*1000}}]}},
        'profit_target_selection':{'price':4.28,'selected_target_prices':[4.28]},
        'historical_hod_reference':{'changed':True,'at':1,'hod':4.,'zone_lower':3.6},
        'reason_code':'historical_hod_entry'}
    external=journal.reference_evidence(metadata)
    record=journal.append(run_id='run',category='strategy',entity_type='strategy_intent',entity_id='entry',
        payload={'ticker':'SUGP','action':'enter_long','metadata':external})
    def check():
        compact=journal.strategy_activity_records(run_id='run',compact=True)[0].payload
        current=compact['metadata']['unified_structural_trigger']['current_snapshot']
        assert current['session_high']==4. and current['levels'][0]['entry_boundary']==3.61
        assert 'fit' not in current['levels'][0]
        assert REFERENCE not in json.dumps(compact)
        assert len(json.dumps(compact))<2000
    check()
    # Reproduce the old projection, which persisted hashes rather than prices.
    with journal._connection:
        journal._connection.execute('UPDATE journal_activity SET payload_json=? WHERE record_id=?',
            (json.dumps({'ticker':'SUGP','action':'enter_long','metadata':external}),record.record_id))
    before=journal._fetchone('SELECT payload_json FROM journal_activity')[0]
    journal.close()
    journal=TradingJournal(path,read_only=True)
    check()
    assert journal._fetchone('SELECT payload_json FROM journal_activity')[0]==before
    assert journal.records('run')[0].payload['metadata']==metadata
    journal.close()


def test_compact_reference_integrity_and_unselected_books():
    from hashlib import sha256
    import pytest
    from src.trading_runtime.journal_evidence import activity_payload
    raw=json.dumps({'current_snapshot':{'session_high':4.,'levels':[]},
                    'levels':{REFERENCE:'unselected-book'}})
    digest=sha256(raw.encode()).hexdigest()
    fetched=[]
    def fetch(key):
        fetched.append(key)
        assert key==digest  # Never fetch the unselected entire book.
        return raw
    compact=activity_payload({'unified_structural_trigger':{REFERENCE:digest}},fetch)
    assert compact['unified_structural_trigger']['current_snapshot']['session_high']==4.
    assert fetched==[digest]
    with pytest.raises(ValueError,match='Missing or corrupt'):
        activity_payload({'unified_structural_trigger':{REFERENCE:digest}},lambda _:raw+' ')
