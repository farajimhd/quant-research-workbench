import polars as pl
import pytest
from research.rl_trading.v6 import published_market_audit as audit

def test_chart_reads_saved_wait_even_when_original_gain_would_create_entry(monkeypatch,tmp_path):
    folder=tmp_path/'shards'/'00000';folder.mkdir(parents=True)
    pl.DataFrame(dict(listing_id=['a'],time_us=[2_000_000],action=['WAIT'],action_1a=['ENTRY'],
        label_value=[1.],entry_gain=[10.],allocation_ratio=[0.],allocation_loss_mask=[False],group_id=[None])).write_parquet(folder/'labels.parquet')
    meta=dict(day='2026-07-31',dataset_sha256='final',source_dataset_sha256='original',certificate_sha256='certificate')
    monkeypatch.setattr(audit,'session',lambda day:({'sha256':'final'},{},{'sha256':'certificate'},tmp_path,
        {'shards':[{'path':'shards/00000','sha256':'shard'}]}))
    monkeypatch.setattr(audit.source,'read_json',lambda *args:{'identities':['a'],'binding':{'source_sha256':'original'},'files':{'labels':{'sha256':'bytes'}}})
    monkeypatch.setattr(audit.source,'verified_local',lambda path,sha:path)
    monkeypatch.setattr(audit.source,'chart',lambda *args:{'labels':[{'time_us':1_000_000,'action':'CONTEXT'},
        {'time_us':2_000_000,'action':'ENTRY','entry_gain':10.,'model_features':{'causal':True}}]})
    result=audit.chart(meta,'a',2_000_000)
    assert result['label_source']=='published_1b_shard'
    assert result['labels'][1]['action']=='WAIT'
    assert result['labels'][1]['action_1a']=='ENTRY'
    assert result['labels'][1]['model_features']=={'causal':True}
    assert not result['labels'][1]['allocation_loss_mask']
    with pytest.raises(ValueError,match='publication changed'):
        audit.chart({**meta,'dataset_sha256':'stale'},'a',2_000_000)
