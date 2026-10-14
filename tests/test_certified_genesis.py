from datetime import date
import pytest

from pipelines.certified_research import genesis,recipe,v7
from pipelines.certified_research.contracts import CampaignConfig
from pipelines.certified_research.checkpoints import CheckpointBinding,boundaries,restore


def inputs():
    day=date(2026,1,2);rules=(dict(token_id=1,modifier_int=0,update_last=1,update_high_low=1,update_volume=1),)
    frozen=recipe.market_recipe(CampaignConfig(day,day),rules)
    row=genesis.binding(1,'TEST',day,frozen['hash'],'a'*64)
    return day,rules,frozen,row


def test_explicit_origin_is_empty_causal_and_survives_all_session_boundaries():
    day,rules,frozen,row=inputs()
    authority=genesis.engine(row,frozen['definition'],rules)
    engine=authority.check(row['origin_hash'],adjustment_hash=row['origin_hash'])
    assert not engine.rows and not engine.pending and engine.bars_processed==0
    binding=CheckpointBinding(frozen['hash'],'b'*64,row['origin_hash'],'TEST',day)
    outputs,_=v7.generate(engine,(),binding,regular_close=boundaries(day)['REGULAR'])
    assert [o['boundary'] for o in outputs]==['PM','REGULAR','AH']
    for output in outputs:
        resumed=restore(output['payload'],output['state_hash'],output['binding'])
        assert not resumed.rows and resumed.bars_processed==0
    with pytest.raises(ValueError,match='binding'):
        genesis.engine(dict(row,origin='2026-01-05'),frozen['definition'],rules)


def test_origin_does_not_accept_missing_or_pre_source_history():
    _,_,frozen,_=inputs()
    with pytest.raises(ValueError):genesis.binding(1,'TEST',date(2018,1,1),frozen['hash'],'a'*64)
    with pytest.raises(ValueError):genesis.binding(1,'TEST',date(2026,1,2),frozen['hash'],'')


def test_source_day_changes_rebind_certificate_without_leaking_into_causal_empty_state():
    day,rules,frozen,row=inputs()
    changed=genesis.binding(1,'TEST',day,frozen['hash'],'f'*64)
    assert changed['certificate_hash']!=row['certificate_hash']
    assert changed['origin_hash']==row['origin_hash']
    a=genesis.engine(row,frozen['definition'],rules).engine
    b=genesis.engine(changed,frozen['definition'],rules).engine
    assert a.checkpoint()==b.checkpoint()


@pytest.mark.parametrize('failure',('source','membership','parent','stop'))
def test_origin_admission_rejects_unqualified_dependencies_and_closes_readers(failure):
    from threading import Event
    from types import SimpleNamespace
    from unittest.mock import Mock
    from pipelines.certified_research.campaign import PlannedUnit
    from pipelines.certified_research.execution import Unit
    from pipelines.certified_research.handlers import RebuildOriginHandlers
    day,rules,frozen,row=inputs();claim=SimpleNamespace(check=Mock());stop=Event()
    client=SimpleNamespace(query=Mock(return_value=[dict(n=0 if failure=='membership' else 1)]),close=Mock())
    runtime=SimpleNamespace(dataset='00000000-0000-0000-0000-000000000001',definition=frozen['definition'],
        rules=rules,sources={day:'a'*64})
    plans=(PlannedUnit(Unit('source','canonical_source'),{}),PlannedUnit(Unit('universe','universe'),{}))
    plan=PlannedUnit(Unit('origin','v7_origin',('source','universe')),dict(binding=row))
    parents=dict(source=dict(product='canonical_source',day=str(day),source_hash='a'*64,output_hash='a'*64),
        universe=dict(output_hash='b'*64))
    if failure=='source':parents['source']['source_hash']='f'*64
    elif failure=='parent':parents.pop('universe')
    elif failure=='stop':stop.set()
    factory=Mock(return_value=client)
    registry=RebuildOriginHandlers('arte',claim,factory,Mock(),runtime,plans)
    with pytest.raises(InterruptedError if failure=='stop' else ValueError):registry.producer(plan,parents,stop)
    if failure=='membership':client.close.assert_called_once()
    else:factory.assert_not_called()
