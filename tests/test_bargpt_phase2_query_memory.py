"""Identical-SQL retries, weighted reservations and protected query admission."""

from contextlib import contextmanager,ExitStack
from types import SimpleNamespace
import threading

import pytest

from research.bar_gpt.v5.phase2_ar_shards import query_memory,run

GIB=1024**3


def test_memory_retries_are_identical_and_restore_client(monkeypatch):
    client=SimpleNamespace(default_query_params={'max_memory_usage':123,'max_threads':1})
    calls=[];gates=[]
    @contextmanager
    def gate(budget):
        assert client.default_query_params['max_memory_usage']==123
        gates.append(budget);yield
        assert client.default_query_params['max_memory_usage']==123
    def arrow(c,sql,*args,**kwargs):
        calls.append((sql,c.default_query_params['max_memory_usage']))
        if len(calls)<3:raise RuntimeError('Code:241 MEMORY_LIMIT_EXCEEDED')
        return 'exact bytes'
    monkeypatch.setattr(query_memory.sip,'arrow',arrow)
    timing={};assert query_memory.execute(client,'IDENTICAL SQL','test',gate,1,4,timing)=='exact bytes'
    assert gates==[1,2,4] and calls==[('IDENTICAL SQL',n*GIB) for n in (1,2,4)]
    assert timing['memory_retries']==2 and timing['queries']==3
    assert client.default_query_params=={'max_memory_usage':123,'max_threads':1}


@pytest.mark.parametrize('message,calls',[('MEMORY_LIMIT_EXCEEDED',3),('TIMEOUT_EXCEEDED',1),('bad source schema',1)])
def test_limits_fail_closed_and_never_leave_an_increased_client_limit(monkeypatch,message,calls):
    client=SimpleNamespace(default_query_params={});seen=[]
    monkeypatch.setattr(query_memory.sip,'arrow',lambda *a,**kw:(seen.append(a[1]),(_ for _ in ()).throw(RuntimeError(message)))[1])
    from contextlib import nullcontext
    with pytest.raises(RuntimeError,match=message):query_memory.execute(client,'same SQL','test',nullcontext,1,4,{})
    assert len(seen)==calls and client.default_query_params=={}


def test_reconnect_exhaustion_does_not_escalate_memory(monkeypatch):
    client=SimpleNamespace(default_query_params={'max_memory_usage':GIB});calls=[]
    def broken(*args,**kwargs):calls.append(args[1]);raise ConnectionError('closed')
    monkeypatch.setattr(query_memory.sip,'arrow',broken);monkeypatch.setattr(query_memory.time,'sleep',lambda _:None)
    from contextlib import nullcontext
    with pytest.raises(ConnectionError):query_memory.execute(client,'exact SQL','test',nullcontext,1,4,{})
    assert calls==['exact SQL']*3 and client.default_query_params['max_memory_usage']==GIB


@pytest.fixture
def state(tmp_path,monkeypatch):
    class Guard:
        limit=32;reason='training'
        def refresh(self):return self.limit
    value=dict(root=tmp_path,settings=dict(query_memory_gib=1,query_max_memory_gib=4,reserve_memory_gib=64),
        active=SimpleNamespace(value=0),reserved=SimpleNamespace(value=0),dense=threading.BoundedSemaphore(2),
        dense_active=SimpleNamespace(value=0),dense_attempts=SimpleNamespace(value=0),
        lock=threading.Lock(),stop=threading.Event(),guard=Guard(),telemetry=None)
    monkeypatch.setattr(run,'STATE',value)
    monkeypatch.setattr(run.psutil,'virtual_memory',lambda:SimpleNamespace(available=128*GIB))
    return value


def test_all32_base_queries_fit_and_mixed_tiers_reserve_their_actual_budget(state):
    with ExitStack() as stack:
        for _ in range(32):stack.enter_context(run.query_admission(1))
        assert state['active'].value==32 and state['reserved'].value==32*GIB
    assert state['active'].value==0 and state['reserved'].value==0
    with ExitStack() as stack:
        for n in (1,2,4):stack.enter_context(run.query_admission(n))
        assert state['reserved'].value==7*GIB and state['dense_active'].value==2
    assert state['reserved'].value==0 and state['dense_active'].value==0


def test_dense_retries_stay_bounded_and_stop_releases_permits(state):
    errors=[]
    def third():
        try:
            with run.query_admission(2):raise AssertionError('third dense query admitted')
        except InterruptedError:errors.append('stopped')
    with ExitStack() as stack:
        stack.enter_context(run.query_admission(2));stack.enter_context(run.query_admission(4))
        worker=threading.Thread(target=third);worker.start();state['stop'].set();worker.join(2)
        assert not worker.is_alive() and errors==['stopped']
        assert state['reserved'].value==6*GIB
    assert state['active'].value==0 and state['reserved'].value==0
    assert state['dense'].acquire(blocking=False) and state['dense'].acquire(blocking=False)


def test_ram_and_validation_limits_are_preserved_and_query_errors_release_reservation(state,monkeypatch):
    state['guard'].limit=4
    with ExitStack() as stack:
        for _ in range(4):stack.enter_context(run.query_admission(1))
        state['stop'].set()
        with pytest.raises(InterruptedError):
            with run.query_admission(1):pass
    state['stop'].clear()
    monkeypatch.setattr(run.psutil,'virtual_memory',lambda:SimpleNamespace(available=64*GIB))
    monkeypatch.setattr(state['stop'],'wait',lambda _:state['stop'].set())
    with pytest.raises(InterruptedError):
        with run.query_admission(1):pass
    assert state['reserved'].value==0
    state['stop'].clear();monkeypatch.setattr(run.psutil,'virtual_memory',lambda:SimpleNamespace(available=128*GIB))
    with pytest.raises(ValueError,match='query failure'):
        with run.query_admission(4):raise ValueError('query failure')
    assert state['active'].value==0 and state['reserved'].value==0 and state['dense_active'].value==0


def test_engineering_gate_requires_memory_evidence_and_rejects_selector_changes(tmp_path):
    import json
    from research.bar_gpt.v5.phase2_ar_shards.source import verify_engineering_revision
    from research.bar_gpt.v5.shards.contract import digest,file_hash
    prior=dict(hash='plan',code={'phase2_ar_shards/source.py':'old'})
    current=dict(code={'phase2_ar_shards/source.py':'new','phase2_ar_shards/query_memory.py':'new'})
    flags=('weighted_admission_tests_passed','client_limits_restored','identical_sql_retry_verified',
        'unchanged_payload_selection_and_cache_binding','training_guards_preserved')
    def proof(missing=None):
        a=dict(passed=True,plan_hash='plan',to_code=current['code'],counts_parity_cases=12,complete_output_parity_cases=2,
            **{k:True for k in flags if k!=missing});a['hash']=digest(a)
        ap=tmp_path/'audit.json';ap.write_text(json.dumps(a))
        p=dict(kind='adaptive-query-memory-v1',plan_hash='plan',from_code=prior['code'],to_code=current['code'],audit_path=str(ap),audit_sha256=file_hash(ap));p['hash']=digest(p)
        pp=tmp_path/'proof.json';pp.write_text(json.dumps(p));return pp
    with pytest.raises(ValueError,match='same-contract proof'):verify_engineering_revision(prior,current,proof(flags[0]))
    verify_engineering_revision(prior,current,proof())
    current['code']['phase2_ar_shards/sampling.py']='changed'
    with pytest.raises(ValueError,match='Uncertified'):verify_engineering_revision(prior,current,proof())
