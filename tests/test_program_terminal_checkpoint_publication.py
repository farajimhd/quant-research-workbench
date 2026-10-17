"""Repair publication composes existing publishers without a journal write."""
import asyncio
from dataclasses import replace
from datetime import date
from threading import Event
from types import SimpleNamespace

import pytest

from src.backend import program_terminal_checkpoint_publication as m
from src.trading_runtime.arte_oms_observation_snapshot import project_shared_oms_observations
from src.trading_runtime.arte_portfolio_snapshot import CapturedPortfolioSnapshot
from tests.test_live_signal_completion_keeper import FakeKazoo
from tests.test_program_terminal_financial_rollforward import inputs, project


def fixture(monkeypatch):
    values = inputs()
    result = project(values)
    batch, previous_batch = ('00000000-0000-0000-0000-000000000200',
                             '00000000-0000-0000-0000-000000000099')
    root = result.broker_snapshot.snapshot
    previous = m.V4CommittedPrefix('run',99,previous_batch,
        f"{root['session_date']}:{root['boundary_ms']-300}",'running',(previous_batch,))
    prefix = m.V4CommittedPrefix('run',200,batch,
        f"{root['session_date']}:{root['boundary_ms']}",'running',(previous_batch,batch))
    observed = project_shared_oms_observations(run_id='run',
        session_date=date.fromisoformat(root['session_date']),checkpoint_sequence=200,
        boundary_ms=root['boundary_ms'],groups={})
    kazoo = FakeKazoo();kazoo.add_listener=lambda listener:None
    session = m.ManagedKeeperSession(kazoo);session._on_state('CONNECTED')
    current=[True]
    owner=SimpleNamespace(_session=session,is_current=lambda *a,**k:current[0])
    lease=m.BacktestV4KeeperLease(owner,'run','repair-owner',2)
    writer=SimpleNamespace(manager_keeper_session=session,backtest_v4_lease=lease,
        typed_insert_strict=True,typed_insert_dispatch=m.TypedInsertDispatch(kazoo))
    oldheads=(m.BrokerMatchHead('run',99,previous_batch,values[8].snapshot['content_hash'],0),
              m.OmsObservationHead('run',99,previous_batch,'a'*64,0))
    readers=(m.ManagedSharedBrokerMatchHeadReader(session),m.ManagedSharedOmsObservationHeadReader(session))
    def sethead(i,sequence,batch,digest,version):
        kazoo.nodes[readers[i].path('run')]=(f'1\nrun\n{sequence}\n{batch}\n{digest}'.encode(),version,0)
    for i,head in enumerate(oldheads):sethead(i,99,previous_batch,head.snapshot_hash,0)
    clients=[];calls=[];prefix_reads=[]
    def read_factory():
        c=SimpleNamespace(closed=False)
        c.close=lambda:setattr(c,'closed',True)
        c.execute=lambda sql:'1'
        clients.append(c);return c
    def load(*args):prefix_reads.append(1);return prefix
    monkeypatch.setattr(m,'load_verified_v4_prefix',load)
    monkeypatch.setattr(m,'load_latest_backtest_cursor',lambda *a:dict(run_id='run',event_sequence=200,
        batch_id=batch,session_date=root['session_date'],boundary_ms=root['boundary_ms']))
    monkeypatch.setattr(m,'storage_preflight',lambda *a,**k:calls.append('storage'))
    def broker_publish(client,sess,rows,**kw):
        assert client is writer and sess is session and rows==result.broker_snapshot
        assert kw['journal_batch_id']==batch
        calls.append('broker');sethead(0,200,batch,root['content_hash'],1)
        return readers[0].read_head(run_id='run')
    def oms_publish(client,sess,rows,**kw):
        assert client is writer and sess is session and rows==observed
        calls.append('oms');sethead(1,200,batch,observed.root['content_hash'],1)
        return readers[1].read_head(run_id='run')
    monkeypatch.setattr(m,'publish_shared_broker_snapshot',broker_publish)
    monkeypatch.setattr(m,'publish_shared_oms_observations',oms_publish)
    monkeypatch.setattr(m,'publish_prepared_portfolio_snapshot',lambda client,p:calls.append('portfolio') or 'b'*64)
    capture=CapturedPortfolioSnapshot('run','DU1',200,result.scope.stop_at,'primary',
        'disabled','synchronized','account-snapshot',result.scope.stop_at,'',100000.,None,None,
        (),(),(),(),(),())
    kwargs=dict(read_client_factory=read_factory,writer_connection_factory=lambda:None,
        session=session,lease=lease,prefix=prefix,previous_prefix=previous,
        previous_broker_head=oldheads[0],previous_observation_head=oldheads[1],
        broker_result=result,broker_contract=values[9],observations=observed,
        portfolios=(capture,),concurrency=1)
    return SimpleNamespace(writer=writer,kwargs=kwargs,calls=calls,clients=clients,
        prefix_reads=prefix_reads,current=current,sethead=sethead,readers=readers)


def run(f):return asyncio.run(m.publish_terminal_program_checkpoints(f.writer,**f.kwargs))


def test_exact_existing_fence_all_components_and_idempotent_retry(monkeypatch):
    f=fixture(monkeypatch)
    first=run(f)
    assert f.calls==['storage','broker','oms','portfolio']
    assert first.prefix==f.kwargs['prefix'] and first.lease_epoch==2
    assert first.portfolio_state_hashes==( ('DU1','b'*64), )
    assert len(f.prefix_reads)==2 and all(c.closed for c in f.clients)
    # A new dedicated worker can repeat exactly the already-selected targets.
    second=run(f)
    assert first==second and all(c.closed for c in f.clients)


def test_partial_component_failure_can_repeat_only_identical_projection(monkeypatch):
    f=fixture(monkeypatch)
    original=m.publish_shared_oms_observations
    def fail(*a,**k):raise RuntimeError('OMS interrupted after broker publication')
    monkeypatch.setattr(m,'publish_shared_oms_observations',fail)
    with pytest.raises(RuntimeError,match='OMS interrupted'):run(f)
    assert f.calls==['storage','broker'] and all(c.closed for c in f.clients)
    monkeypatch.setattr(m,'publish_shared_oms_observations',original)
    result=run(f)
    assert result.broker_head.checkpoint_sequence==200
    assert result.observation_head.checkpoint_sequence==200
    assert f.calls[-3:]==['broker','oms','portfolio']


def test_verifies_normalized_projection_before_any_publication(monkeypatch):
    f=fixture(monkeypatch);result=f.kwargs['broker_result']
    broken=replace(result.broker_snapshot,snapshot={**result.broker_snapshot.snapshot,'content_hash':'0'*64})
    f.kwargs['broker_result']=replace(result,broker_snapshot=broken)
    with pytest.raises(ValueError):run(f)
    assert not f.calls and not f.clients


@pytest.mark.parametrize('bad',['epoch','prefix','oldhead','scope','account','clock','portfolio','readonly','lease','head','storage','broker','oms','finalprefix','version'])
def test_rejects_changed_authorities_and_drains_resources(monkeypatch,bad):
    f=fixture(monkeypatch)
    if bad=='epoch':
        f.kwargs['lease']=replace(f.kwargs['lease'],epoch=1);f.writer.backtest_v4_lease=f.kwargs['lease']
    if bad=='prefix':f.kwargs['prefix']=replace(f.kwargs['prefix'],status='completed')
    if bad=='oldhead':f.kwargs['previous_broker_head']=replace(f.kwargs['previous_broker_head'],checkpoint_sequence=98)
    if bad=='scope':f.kwargs['previous_prefix']=replace(f.kwargs['previous_prefix'],source_cursor='foreign')
    if bad=='account':f.kwargs['portfolios']=(replace(f.kwargs['portfolios'][0],account_id='foreign'),)
    if bad=='clock':f.kwargs['portfolios']=(replace(f.kwargs['portfolios'][0],snapshot_at=f.kwargs['broker_result'].scope.start_at),)
    if bad=='portfolio':f.kwargs['portfolios']=(replace(f.kwargs['portfolios'][0],commands=({'command':'pending'},)),)
    if bad=='readonly':
        original=f.kwargs['read_client_factory']
        def factory():c=original();c.execute=lambda sql:'0';return c
        f.kwargs['read_client_factory']=factory
    if bad=='lease':f.current[0]=False
    if bad=='head':f.sethead(1,101,f.kwargs['prefix'].last_batch_id,'c'*64,1)
    def fail(*a,**k):raise RuntimeError('injected publication failure')
    if bad=='storage':monkeypatch.setattr(m,'storage_preflight',fail)
    if bad=='broker':monkeypatch.setattr(m,'publish_shared_broker_snapshot',fail)
    if bad=='oms':monkeypatch.setattr(m,'publish_shared_oms_observations',fail)
    if bad=='finalprefix':
        original=m.load_verified_v4_prefix
        monkeypatch.setattr(m,'load_verified_v4_prefix',lambda *a: original(*a) if not f.calls else None)
    if bad=='version':
        original=m.publish_prepared_portfolio_snapshot
        def changed(*a):
            h=f.readers[0].read_head(run_id='run')
            f.sethead(0,h.checkpoint_sequence,h.journal_batch_id,h.snapshot_hash,h.keeper_version+1)
            return original(*a)
        monkeypatch.setattr(m,'publish_prepared_portfolio_snapshot',changed)
    with pytest.raises((ValueError,RuntimeError)):run(f)
    assert all(c.closed for c in f.clients)
    if bad in ('epoch','prefix','oldhead','scope','account','clock','portfolio','readonly','lease','head','storage'):
        assert 'broker' not in f.calls


def test_cancel_waits_for_existing_publication_worker_before_return(monkeypatch):
    f=fixture(monkeypatch);entered=Event();release=Event();finished=Event()
    original=m.publish_shared_broker_snapshot
    def slow(*a,**k):entered.set();release.wait(3);result=original(*a,**k);finished.set();return result
    monkeypatch.setattr(m,'publish_shared_broker_snapshot',slow)
    async def work():
        task=asyncio.create_task(m.publish_terminal_program_checkpoints(f.writer,**f.kwargs))
        assert await asyncio.to_thread(entered.wait,2)
        task.cancel();await asyncio.sleep(0);task.cancel();await asyncio.sleep(0)
        assert not task.done() and not finished.is_set()
        release.set()
        with pytest.raises(asyncio.CancelledError):await task
        assert finished.is_set() and f.calls[-1]=='portfolio'
    asyncio.run(work())
    assert all(c.closed for c in f.clients)
