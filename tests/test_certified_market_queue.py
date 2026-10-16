"""Finite incremental storage admission never impersonates full capacity proof."""
import pytest
from threading import Event
from scripts.run_certified_market_queue import IncrementalBudget,GIB,ENVELOPE,RESERVE
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
