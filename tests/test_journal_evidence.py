import json
from datetime import datetime, timezone
from src.trading_runtime.journal import TradingJournal
from src.trading_runtime.journal_evidence import REFERENCE


def test_batched_full_journal_fences_visibility_and_poisoned_prefix(tmp_path,monkeypatch):
    import pytest
    import sqlite3
    writer=TradingJournal(tmp_path/'batch.sqlite3')
    reader=TradingJournal(writer.path,read_only=True)
    try:
        writer.enable_write_batching()
        assert writer._connection.execute('PRAGMA synchronous').fetchone()[0]==2
        monkeypatch.setattr('src.trading_runtime.journal.perf_counter',lambda:0.)
        writer._batch_started=0.
        def append(n):
            writer.append(run_id='run',category='test',entity_type='test',entity_id=str(n),payload={'n':n})
        append(1);append(2)
        assert reader.records('run')==[]
        assert writer.latest_sequence('run')==2
        assert reader.latest_sequence('run')==2
        append(3)
        writer.save_checkpoint('run','3',{'complete':True},datetime.now(timezone.utc))
        assert reader.load_checkpoint('run')['state']=={'complete':True}
        assert reader.latest_sequence('run')==3
        append(4)
        with pytest.raises(sqlite3.OperationalError):
            with writer._transaction():
                writer._connection.execute('INSERT INTO missing_table VALUES (1)')
        with pytest.raises(RuntimeError,match='durable prefix'):
            writer.latest_sequence('run')
        assert reader.latest_sequence('run')==3
    finally:
        writer.close();reader.close()


def test_frozen_repeated_checkpoint_evidence_reopens_exactly(tmp_path):
    from src.market_engine.immutable_evidence import freeze
    levels=freeze([{'id':i,'fit':{'center':float(i)},'timeframes':['1s']} for i in range(50)])
    state={'complete':True,'states':[{'rows':levels,'prior_rows':levels} for _ in range(20)]}
    path=tmp_path/'checkpoint.sqlite3'
    journal=TradingJournal(path)
    journal.save_checkpoint('run','cursor',state,datetime.now(timezone.utc))
    stored=journal._fetchone('SELECT state_json FROM checkpoints')['state_json']
    assert len(stored)<len(json.dumps(state))/10
    journal.close()
    journal=TradingJournal(path,read_only=True)
    try:
        assert journal.load_checkpoint('run')['state']==state
    finally:journal.close()


def test_compressed_backtest_checkpoint_and_evidence_reopen_exactly(tmp_path):
    from src.trading_runtime.journal_storage import MARKER
    value={'levels':[{'price':i,'evidence':'retained causal evidence '*100} for i in range(100)]}
    state={'complete':True,'controller':{'values':[value]*10,'series':[0]*10000},'broker':{'executions':[]}}
    journal=TradingJournal(tmp_path/'compressed.sqlite3');journal.enable_write_batching()
    journal.append(run_id='run',category='test',entity_type='test',entity_id='1',payload=value)
    journal.save_checkpoint('run','cursor',state,datetime.now(timezone.utc))
    assert journal._fetchone('select sum(length(payload_json)) n from journal_evidence')['n']<len(json.dumps(value))/5
    assert MARKER in journal._fetchone('select state_json from checkpoints')['state_json']
    assert journal._fetchone("select json_type(state_json,'$.broker.executions') kind from checkpoints")['kind']=='array'
    journal.close();journal=TradingJournal(journal.path,read_only=True)
    try:
        assert journal.records('run')[0].payload['levels']==value['levels']
        assert journal.load_checkpoint('run')['state']==state
    finally:journal.close()


def test_compressed_storage_rejects_corruption_and_preserves_unicode():
    import pytest
    from src.trading_runtime.journal_storage import pack,unpack,MARKER
    raw=json.dumps({'value':'\u03bb'*10000},ensure_ascii=False)
    encoded=pack(raw)
    assert unpack(encoded)==raw
    for key,value in [('bytes',1),('bytes',2**40),('sha256','wrong'),('data','!'),('codec','unknown')]:
        changed=json.loads(encoded);changed[MARKER][key]=value
        with pytest.raises(ValueError,match='Corrupt compressed'):
            unpack(json.dumps(changed,separators=(',',':')))


def test_whole_authority_reference_preserves_nested_evidence(tmp_path):
    journal=TradingJournal(tmp_path/'authority.sqlite3');journal.enable_write_batching()
    authority={'source':{'levels':[{'value':'provenance'*10000}], 'revision':'pinned'}}
    reference=journal.reference_json(authority)
    journal.save_checkpoint('run','cursor',{'controller':{'data_authority':reference}},datetime.now(timezone.utc))
    journal.close();journal=TradingJournal(journal.path,read_only=True)
    try:assert journal.load_checkpoint('run')['state']['controller']['data_authority']==authority
    finally:journal.close()


def test_assignment_write_can_skip_unused_hydration_without_changing_storage(tmp_path, monkeypatch):
    from tests.test_long_momentum_strategy import assignment
    journal=TradingJournal(tmp_path/'assignments.sqlite3')
    journal.enable_write_batching()
    payload=assignment(state={'levels':[{'price':2.1,'fit':{'center':2.1}}]}).payload()
    try:
        expected=journal.save_strategy_assignments([payload])
        with monkeypatch.context() as patch:
            patch.setattr(journal,'_fetchall',lambda *args,**kwargs: (_ for _ in ()).throw(AssertionError('Unnecessary hydration')))
            assert journal.save_strategy_assignments([payload],return_rows=False)==[]
        assert journal.strategy_assignment(payload['assignment_id'])==expected[0]
    finally:
        journal.close()


def test_batched_assignment_parameters_remain_exact_after_updates_and_reopen(tmp_path):
    from tests.test_long_momentum_strategy import assignment
    journal=TradingJournal(tmp_path/'parameters.sqlite3');journal.enable_write_batching()
    payload=assignment().payload()
    journal.save_strategy_assignments([payload],return_rows=False)
    assert REFERENCE in journal._fetchone('select parameters_json from strategy_assignments')['parameters_json']
    payload['parameters']['new_parameter']={'value':1}
    journal.save_strategy_assignments([payload],return_rows=False)
    journal.close();journal=TradingJournal(journal.path,read_only=True)
    try:assert journal.strategy_assignment(payload['assignment_id'])['parameters']==payload['parameters']
    finally:journal.close()


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
    journal.enable_write_batching()
    inserted=[]
    journal._connection.set_trace_callback(lambda sql:inserted.append(sql) if sql.startswith('INSERT OR IGNORE INTO journal_evidence') else None)
    payload = {'ticker': 'TEST', 'action': 'enter_long', 'metadata': {
        'profit_target': 12, 'unified_structural_trigger': {'levels': [
            {'price': n, 'evidence': 'original' * 100} for n in range(100)]}}}
    for _ in range(3):
        journal.append(run_id='run', category='strategy', entity_type='strategy_intent',
                       entity_id='entry', payload=payload, event_time=datetime.now(timezone.utc))
    stored = journal._fetchall('SELECT payload_json FROM journal')
    assert all(REFERENCE in row['payload_json'] for row in stored)
    assert journal._fetchone('SELECT count(*) n FROM journal_evidence')['n'] == 2
    assert len(inserted)==2
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


def test_batched_writes_fence_cross_process_admission(tmp_path):
    journal=TradingJournal(tmp_path/'admission.sqlite3')
    journal.enable_write_batching()
    try:
        journal.save_portfolio_state('sim',{'cash':100})
        lease=journal.acquire_portfolio_admission_lease('sim',owner_id='first')
        assert lease is not None
        journal.save_portfolio_state('sim',{'cash':90})
        owner=journal.acquire_campaign_session_ownership('TEST',session_key='today',owner_id='first',state='reserved')
        assert owner is not None
        assert journal.release_portfolio_admission_lease('sim',owner_id='first',epoch=lease['epoch'])
        assert journal.release_campaign_session_reservation('TEST',session_key='today',owner_id='first')
        reader=TradingJournal(journal.path,read_only=True)
        try:
            assert reader.portfolio_states()['sim']=={'cash':90}
            assert reader.campaign_session_ownership('TEST',session_key='today') is None
        finally:reader.close()
    finally:journal.close()


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
