from types import SimpleNamespace
from threading import Event
from unittest.mock import patch
import pytest
from pipelines.certified_research import collection_execution as module


def test_child_claim_cleanup_and_original_stop_event():
    event=Event();claims=[];results=[]
    frozen=dict(members=[dict(dataset_id='child')])
    saved=SimpleNamespace(plan_hash='hash',config=SimpleNamespace())
    class Claim:
        def check(self):pass
        def close(self):self.closed=True
    def child(id_):
        claim=Claim();claim.closed=False;claims.append(claim);return claim
    class Store:
        def __init__(self,*args):pass
        def load(self):return frozen
        def controller(self,launch,guard,aggregate):
            adapter=launch('child')
            results.append(adapter.run(stop=lambda:False))
            return SimpleNamespace(run=lambda **kwargs:dict(status='complete'))
    def run(*args,**kwargs):
        assert kwargs['stop'] is event
        return dict(status='complete')
    with patch.object(module.collection_store,'Store',Store),patch.object(module.campaign,'load_plan',return_value=saved),\
         patch.object(module.capacity_profile,'factory',return_value=lambda *args:None),\
         patch.object(module.saved_execution,'run',side_effect=run):
        assert module.execute(None,'arte','root',Claim(),child,None,None,profile={},stop=event)['status']=='complete'
    assert results==[dict(status='complete')] and all(c.closed for c in claims)


def test_failed_child_releases_claim():
    class Claim:
        closed=False
        def check(self):pass
        def close(self):self.closed=True
    child=Claim()
    class Store:
        def __init__(self,*args):pass
        def load(self):return dict(members=[dict(dataset_id='child')])
        def controller(self,launch,*args):
            launch('child').run()
    saved=SimpleNamespace(plan_hash='hash',config=SimpleNamespace())
    with patch.object(module.collection_store,'Store',Store),patch.object(module.campaign,'load_plan',return_value=saved),\
         patch.object(module.capacity_profile,'factory',return_value=lambda *args:None),\
         patch.object(module.saved_execution,'run',side_effect=ValueError('blocked')):
        with pytest.raises(ValueError,match='blocked'):
            module.execute(None,'arte','root',Claim(),lambda id_:child,None,None,profile={})
    assert child.closed


def test_collection_cli_requires_id_and_capacity_and_disallows_acquisition():
    from scripts.prepare_certified_research import parse_args
    from uuid import uuid4
    id_=str(uuid4())
    for argv in (['resume-collection'],['resume-collection','--dataset-id',id_],
                 ['verify-collection','--dataset-id',id_,'--acquire-missing']):
        with pytest.raises(SystemExit):parse_args(argv)
    assert parse_args(['verify-collection','--dataset-id',id_]).command=='verify-collection'
