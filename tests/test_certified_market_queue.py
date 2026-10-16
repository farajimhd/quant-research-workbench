"""Finite incremental storage admission never impersonates full capacity proof."""
import pytest
from threading import Event
from scripts.run_certified_market_queue import IncrementalBudget,SynchronizedClient,GIB,ENVELOPE,RESERVE
from pipelines.certified_research.contracts import digest


def fixture_query(*,used=10*GIB,free=RESERVE+ENVELOPE,missing=False):
    def query(sql):
        if 'system.parts' in sql:return [dict(n=used)]
        return [dict(name=name,free_space=free,unreserved_space=free)
                for name in (('live_market_ssd',) if missing else ('live_market_ssd','sip_raw_ssd'))]
    return query


def test_increment_admission_is_hashed_and_explicitly_not_population_qualification():
    guard=IncrementalBudget(fixture_query(),10*GIB,512*GIB,Event())
    proof=guard.admit('f'*64,('one','two'))
    fingerprint=proof.pop('certificate_hash')
    assert fingerprint==digest(proof)
    assert proof['whole_population_capacity_qualified'] is False
    assert proof['admitted_scope']=='serial guarded increments only'
    assert proof['retained_growth_bytes']==0


@pytest.mark.parametrize('options',[
    dict(used=395*GIB),dict(free=RESERVE+ENVELOPE-1),dict(missing=True)])
def test_budget_or_missing_disk_stops_before_increment(options):
    stop=Event();guard=IncrementalBudget(fixture_query(**options),10*GIB,512*GIB,stop)
    with pytest.raises(RuntimeError,match='Incremental budget pause'):guard.check()
    assert stop.is_set() and guard.reason


def test_existing_parts_cannot_create_negative_growth_credit():
    guard=IncrementalBudget(fixture_query(used=0),100*GIB,512*GIB,Event())
    assert guard.check()['retained_growth_bytes']==0


def test_three_workers_reserve_three_envelopes_and_bind_certificate_resources():
    guard=IncrementalBudget(fixture_query(free=RESERVE+3*ENVELOPE),10*GIB,512*GIB,Event(),
        execution_workers=3,execution_memory_bytes=6*GIB)
    proof=guard.admit('a'*64,())
    assert proof['next_increment_envelope_bytes']==3*ENVELOPE
    assert proof['execution_workers']==3 and proof['execution_memory_bytes']==6*GIB
    assert proof['whole_population_capacity_qualified'] is False


def test_parallel_worker_envelopes_cannot_spend_retained_or_reserve_headroom():
    stop=Event()
    guard=IncrementalBudget(fixture_query(used=140*GIB,free=RESERVE+3*ENVELOPE),10*GIB,512*GIB,stop,
        execution_workers=3,execution_memory_bytes=6*GIB)
    with pytest.raises(RuntimeError,match='parallel increment envelopes'):guard.check()
    assert stop.is_set()


def test_explicit_sixteen_gib_reservation_enables_eight_bounded_workers():
    guard=IncrementalBudget(fixture_query(),10*GIB,512*GIB,Event(),
        execution_workers=8,execution_memory_bytes=16*GIB,per_worker_envelope_bytes=16*GIB)
    proof=guard.admit('a'*64,())
    assert proof['next_increment_envelope_bytes']==128*GIB
    assert proof['per_worker_envelope_bytes']==16*GIB
    assert proof['execution_workers']==8
    assert proof['whole_population_capacity_qualified'] is False


@pytest.mark.parametrize('envelope',[15*GIB,129*GIB,False])
def test_invalid_engineering_envelope_fails_before_queries(envelope):
    def query(sql):raise AssertionError('invalid envelope reached storage')
    with pytest.raises(ValueError,match='engineering envelope'):
        IncrementalBudget(query,0,512*GIB,Event(),per_worker_envelope_bytes=envelope)


@pytest.mark.parametrize('workers,memory',[(0,2*GIB),(9,18*GIB),(True,2*GIB),(3,2*GIB),(3,18*GIB)])
def test_execution_resource_bounds_fail_before_queries(workers,memory):
    def query(sql):raise AssertionError('invalid resources reached storage')
    with pytest.raises(ValueError):
        IncrementalBudget(query,0,512*GIB,Event(),execution_workers=workers,execution_memory_bytes=memory)


def test_shared_control_connection_never_has_overlapping_requests():
    from concurrent.futures import ThreadPoolExecutor
    from threading import Lock
    from time import sleep
    class Client:
        def __init__(self):self.active=0;self.peak=0;self.lock=Lock()
        def query(self,value):
            with self.lock:self.active+=1;self.peak=max(self.peak,self.active)
            sleep(.003)
            with self.lock:self.active-=1
            return value
        def close(self):pass
    raw=Client();client=SynchronizedClient(raw)
    with ThreadPoolExecutor(max_workers=8) as pool:
        assert list(pool.map(client.query,range(16)))==list(range(16))
    assert raw.peak==1


def test_controller_parallel_override_keeps_frozen_plan_and_config(monkeypatch):
    from datetime import date
    from threading import Barrier,Lock
    from types import SimpleNamespace
    from uuid import uuid4
    from unittest.mock import Mock
    from pipelines.certified_research import campaign
    from pipelines.certified_research.contracts import CampaignConfig
    from pipelines.certified_research.execution import Unit
    config=CampaignConfig(date(2026,1,2),date(2026,1,2),workers=1,global_memory_bytes=2*GIB)
    plans=tuple(campaign.PlannedUnit(Unit(str(i),'test',memory_bytes=2*GIB),{}) for i in range(3))
    barrier=Barrier(3);lock=Lock();done=set();threads=set()
    def producer(plan,parents,stop):
        from threading import get_ident
        with lock:threads.add(get_ident())
        barrier.wait(timeout=5)
        with lock:done.add(plan.unit.key)
    def verify(plan,parents):
        with lock:return dict(output_hash='a'*64) if plan.unit.key in done else None
    controller=campaign.Campaign(lambda sql:[dict(n=0)],'arte',SimpleNamespace(check=lambda:None),str(uuid4()),
        config,plans,'b'*64,lambda:None,{'test':producer},{'test':verify})
    original=(config.content_hash,controller.plan_hash,controller.definition.copy())
    monkeypatch.setattr(campaign,'verify_storage',lambda *a,**kw:None)
    monkeypatch.setattr(campaign,'insert_rows',lambda *a,**kw:None)
    monkeypatch.setattr(controller,'install',lambda:None)
    monkeypatch.setattr(controller,'seal',lambda:'c'*64)
    result=controller.run(execution_workers=3,execution_memory_bytes=6*GIB)
    assert result['status']=='complete' and len(threads)==3
    assert result['measurements']['peak_active_workers']==3
    assert result['measurements']['peak_reserved_memory_bytes']==6*GIB
    assert (config.content_hash,controller.plan_hash,controller.definition)==original


@pytest.mark.parametrize('workers,memory',[(True,6*GIB),(33,6*GIB),(3,65*GIB),(3,0),(3,GIB)])
def test_controller_rejects_invalid_override_before_storage_or_install(workers,memory):
    from datetime import date
    from types import SimpleNamespace
    from pipelines.certified_research.campaign import Campaign
    from pipelines.certified_research.contracts import CampaignConfig
    controller=Campaign.__new__(Campaign)
    controller.config=CampaignConfig(date(2026,1,2),date(2026,1,2))
    controller.units=(SimpleNamespace(unit=SimpleNamespace(memory_bytes=2*GIB)),)
    def guard():raise AssertionError('invalid resources reached storage')
    controller.guard=guard
    with pytest.raises(ValueError):controller.run(execution_workers=workers,execution_memory_bytes=memory)


def test_launch_rejects_overrides_without_resource_bound_capacity_certificate():
    from types import SimpleNamespace
    from unittest.mock import Mock
    from pipelines.certified_research.launch import ExecutionLaunch
    launch=ExecutionLaunch.__new__(ExecutionLaunch)
    launch.controller=SimpleNamespace(execution_resources=Mock(return_value=(3,6*GIB)),run=Mock())
    launch.admit=Mock(return_value=dict(status='admitted'))
    with pytest.raises(ValueError,match='does not bind'):launch.run(execution_workers=3,execution_memory_bytes=6*GIB)
    launch.controller.run.assert_not_called()
    launch.admit.return_value.update(execution_workers=3,execution_memory_bytes=6*GIB)
    launch.run(execution_workers=3,execution_memory_bytes=6*GIB)
    launch.controller.run.assert_called_once_with(stop=None,progress=None,execution_workers=3,execution_memory_bytes=6*GIB)
