from dataclasses import replace
from datetime import date
from types import SimpleNamespace
from uuid import uuid4
from unittest.mock import patch
import pytest
from pipelines.certified_research import capacity, collection
from pipelines.certified_research.contracts import CampaignConfig,digest
from pipelines.certified_research.campaign import PlannedUnit
from pipelines.certified_research.execution import Unit


def setup():
    config=CampaignConfig(date(2026,1,2),date(2026,1,2))
    partition=dict(groups=((1,),(2,)),issuer_keys=(1,2),config_hash=config.content_hash)
    partition['partition_hash']=digest(partition)
    id_=str(uuid4())
    members=tuple(collection.Member(collection.member_id(id_,partition['partition_hash'],i),group,str(i+1)*64)
                  for i,group in enumerate(partition['groups']))
    frozen=collection.definition(id_,partition,members)
    plans={m.dataset_id:SimpleNamespace(dataset_id=m.dataset_id,plan_hash=m.plan_hash,
        config=replace(config,issuer_keys=m.issuer_keys),units=tuple(
            PlannedUnit(Unit('u'+str(i),'technical',(),1024),{}) for i in range(3))) for m in members}
    rates=tuple(capacity.PilotRate(p,'a'*64,'b'*64,1,100,20,10,1)
                for p in ('technical','campaign_metadata','collection_metadata'))
    storage=dict(evidence_hash='c'*64,disks=[dict(name=p,free_space=100000,unreserved_space=100000)
        for p in ('live_market_ssd','sip_raw_ssd')])
    guard=capacity.CollectionCapacityGuard(None,'arte',plans.__getitem__,rates,
        {r.product:r.recipe_hash for r in rates},reserve_bytes={p:0 for p in ('live_market_ssd','sip_raw_ssd')},
        volume_groups={p:'ssd' for p in ('live_market_ssd','sip_raw_ssd')},safety_factor='1')
    return frozen,plans,storage,guard


def test_all_persisted_outputs_and_possible_background_merge_overlap():
    frozen,plans,storage,guard=setup()
    with patch.object(capacity,'inventory',return_value=storage): result=guard(frozen)
    assert result['status']=='admitted'
    rows={r['product']:r for r in result['products']}
    assert rows['technical']['units']==6
    assert rows['technical']['active_units']==6
    assert rows['technical']['persisted_bytes']==600
    assert rows['technical']['transient_bytes']==180
    assert rows['collection_metadata']['units']==4
    proof=dict(result);hash_=proof.pop('certificate_hash');assert hash_==digest(proof)


def test_missing_measured_collection_metadata_rate_blocks():
    frozen,plans,storage,guard=setup();guard.rates=guard.rates[:-1]
    with patch.object(capacity,'inventory',return_value=storage): result=guard(frozen)
    assert result['status']=='blocked'
    assert any('collection_metadata' in reason for reason in result['blockers'])


def test_changed_member_configuration_or_plan_blocks():
    frozen,plans,storage,guard=setup()
    saved=next(iter(plans.values()));saved.config=replace(saved.config,workers=2)
    with pytest.raises(ValueError,match='frozen partition'):guard(frozen)


def test_aggregate_space_can_block_when_each_child_would_fit():
    frozen,plans,storage,guard=setup()
    for disk in storage['disks']:disk.update(free_space=1000,unreserved_space=1000)
    with patch.object(capacity,'inventory',return_value=storage): result=guard(frozen)
    assert result['status']=='blocked'
    assert result['headroom']['live_market_ssd']['required_bytes']>1000
