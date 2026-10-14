"""Backfill admission never substitutes unqualified legacy market data."""
from datetime import date
from threading import Event
from unittest.mock import Mock,patch
from uuid import uuid4
import pytest

from pipelines.certified_research import checkpoint_backfill as backfill,recipe,runtime_plan
from pipelines.certified_research.campaign import PlannedUnit,StoredPlan
from pipelines.certified_research.contracts import CampaignConfig
from pipelines.certified_research.execution import Unit


def source():
    config=CampaignConfig(date(2026,1,2),date(2026,1,5));dataset=str(uuid4())
    rules=(dict(token_id=1,modifier_int=0,update_last=1,update_high_low=1,update_volume=1),)
    frozen=recipe.market_recipe(config,rules);args=dict(day='2026-01-02',listing_key=1,ticker='TEST')
    plans=(*runtime_plan.units(config,frozen['definition'],rules),
        PlannedUnit(Unit('source:2026-01-02','canonical_source'),dict(day='2026-01-02',source_hash='a'*64)),
        PlannedUnit(Unit('bars','market_aggregate',('source:2026-01-02',)),args),
        PlannedUnit(Unit('levels','v7',('bars',)),args))
    return StoredPlan(dataset,config,plans,'b'*64,'c'*64)


def test_admission_blocks_missing_physical_bars_before_returning_a_plan():
    saved=source();query=Mock();claim=Mock();guard=Mock();events=[]
    with patch.object(backfill,'Publisher') as publisher,patch.object(backfill,'ReferencePublisher'):
        publisher.return_value.receipt.return_value=None
        with pytest.raises(ValueError,match='certified source bars are missing'):
            backfill.assemble(query,saved,str(uuid4()),claim,guard,progress=events.append)
    query.assert_not_called()
    assert events[-1]['status']=='failed' and events[-1]['counts']['failed']==1
    assert all(sum(e['counts'].values())==len(saved.units) for e in events if e['phase']=='backfill-plan-preparation')


@pytest.mark.parametrize('failure',('same_dataset','outside_range','stop'))
def test_invalid_backfill_never_publishes_metadata(failure):
    saved=source();query=Mock();claim=Mock();guard=Mock();stop=Event()
    target=saved.dataset_id if failure=='same_dataset' else str(uuid4())
    if failure=='stop':stop.set()
    with pytest.raises(InterruptedError if failure=='stop' else ValueError):
        backfill.assemble(query,saved,target,claim,guard,stop=stop,
            start=date(2025,12,31) if failure=='outside_range' else None)
    query.assert_not_called()


def test_closure_is_bounded_by_metadata_and_keeps_transitive_state_dependencies():
    plans=tuple(PlannedUnit(Unit(str(n),'v7',() if n==0 else (str(n-1),)),{}) for n in range(10000))
    selected=backfill.closure(plans,('9999',))
    assert selected==plans
    with pytest.raises(ValueError,match='missing'):backfill.closure(plans,('absent',))


def test_existing_target_requires_stored_resume_without_replanning():
    query=Mock(return_value=[dict(n=1)])
    with patch.object(backfill,'load_plan') as loader:
        with pytest.raises(ValueError,match='use resume'):
            backfill.run(query,'arte',str(uuid4()),str(uuid4()),Mock(),Mock(),Mock(),Mock())
    loader.assert_not_called()
