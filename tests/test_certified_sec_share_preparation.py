from types import SimpleNamespace
from unittest.mock import Mock,patch

import pytest

from pipelines.certified_research import sec_share_preparation as preparation,capacity
from pipelines.certified_research.contracts import CampaignConfig,digest
from datetime import date


def profile():
    rate=capacity.PilotRate(preparation.PRODUCT,preparation.recipe_hash(),'a'*64,2,100,50,50,2)
    body=dict(rates=[vars(rate)],reserve_bytes={'live_market_ssd':0,'sip_raw_ssd':0},
        volume_groups={'live_market_ssd':'live','sip_raw_ssd':'sip'},safety_factor='1.25')
    return dict(body,profile_hash=digest(body),parsed_rates=(rate,))


def storage():return dict(evidence_hash='b'*64,disks=[dict(name=p,free_space=10**12,unreserved_space=10**12)
    for p in ('live_market_ssd','sip_raw_ssd')])


def test_missing_compatible_rate_blocks_before_any_schema_write():
    p=profile();p['rates']=[];p['parsed_rates']=();p['profile_hash']=digest({k:v for k,v in p.items() if k not in ('profile_hash','parsed_rates')})
    with patch.object(preparation.sec_share_capture,'install') as install,pytest.raises(ValueError,match='pilot rate'):
        preparation.prepare(Mock(),CampaignConfig(date(2026,1,2),date(2026,1,2)),
            (('320193','0000320193-25-000079','filing.htm'),),Mock(),Mock(),Mock(),p)
    install.assert_not_called()


def test_stage_uses_one_bounded_pool_and_closes_all_owned_clients():
    clients=[];dictionary=SimpleNamespace(dictionary_database='q_live',close=Mock());events=[]
    def factory():
        client=SimpleNamespace(query=Mock(),close=Mock());clients.append(client);return client
    def prepared(*args,**kwargs):return dict(prepared_hash='c'*64,document={})
    def captured(*args,**kwargs):return dict(prepared_hash='c'*64,inserted_rows=1)
    config=CampaignConfig(date(2026,1,2),date(2026,1,2),workers=4,global_memory_bytes=8*1024**3)
    with patch.object(preparation.capacity,'inventory',return_value=storage()),\
         patch.object(preparation.sec_share_capture,'install') as install,\
         patch.object(preparation.sec_share_source,'prepare',side_effect=prepared),\
         patch.object(preparation.sec_share_capture,'capture',side_effect=captured):
        result=preparation.prepare(Mock(),config,
            (('320193','0000320193-25-000079','one.htm'),('320193','0000320193-25-000079','two.htm')),
            Mock(),factory,lambda:dictionary,profile(),progress=events.append,retain_descriptors=True)
    assert result['status']=='complete' and result['captured']==2 and result['candidate_only']
    assert all(r['archive_descriptor']['document']=={} for r in result['results'].values())
    assert result['measurements']['peak_reserved_memory_bytes']<=config.global_memory_bytes
    assert len(clients)==2
    for client in clients:client.close.assert_called_once()
    dictionary.close.assert_called_once();install.assert_called_once()
    assert all(event['status']!='certified' for event in events)


def test_profile_mutation_and_recipe_changes_are_not_admitted():
    p=profile();admit=preparation.admission(Mock(),p,1,1)
    with patch.object(preparation.capacity,'inventory',return_value=storage()):
        assert admit(123)['metadata_bytes']==123
        p['safety_factor']='2'
        with pytest.raises(ValueError,match='changed'):admit(123)
