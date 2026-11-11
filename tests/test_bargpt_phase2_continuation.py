"""Session-local uncovered-origin plans, with exact causal pool preservation."""

import json
from pathlib import Path
import numpy as np
import pytest

from research.bar_gpt.v5.phase2_ar_shards.contract import BAR, BLOCK, Compiler, Geometry
from research.bar_gpt.v5.phase2_ar_shards.continuation import uncovered, prior_ranges
from research.bar_gpt.v5.shards.contract import digest


def compiled(count=20000):
    from research.bar_gpt.v5.shards.sql import clock
    opened=clock('2025-01-02',4)
    rows=np.zeros(count,dtype=BAR);rows['end_us']=np.arange(1,count+1)*1_000_000
    rows['end_us']+=opened
    rows['values']=10;rows['valid']=1
    old=np.zeros(64,dtype=BAR);old['end_us']=np.arange(-63,1)*1_000_000;old['values']=10;old['valid']=1
    old['end_us']+=opened
    c=Compiler(Geometry());c.bootstrap([old.copy() for _ in range(5)])
    value=c.compile([rows.copy() for _ in range(5)],1)
    value['sampling']=dict(session_origins=count,unselected_prefix_origins=0,reason='complete_blocks')
    return value,rows


def receipt(root,start=6000,blocks=3,count=20000,coverage=None):
    path=root/'shards'/digest('TEST')[:24]/'2025-01-02';path.mkdir(parents=True)
    r=dict(binding='plan',ticker='TEST',session_date='2025-01-02',files=[],blocks=blocks,
        sampling=dict(session_origins=count,unselected_prefix_origins=start),
        stats=dict(source_origins=count-start,warmup_deferred=0,boundary_tail_deferred=0))
    if coverage is not None:r['origin_coverage']=coverage
    r['hash']=digest(r);(path/'receipt.json').write_text(json.dumps(r))


def test_only_context_overlaps_and_no_block_bridges_a_coverage_gap(tmp_path):
    parent=tmp_path/'first';receipt(parent)
    value,rows=compiled();before=[a.copy() for a in value['views']]
    result=uncovered(value,rows,'TEST','2025-01-02',[(parent,'plan')])
    assert result['origin_coverage']['ranges']==[[0,4096]]
    assert result['origin_coverage']['excluded_ranges']==[[6000,18288]]
    assert result['blocks'].shape==(1,BLOCK)
    assert np.all(np.diff(result['blocks']['end_us'],axis=1)==1_000_000)
    assert result['stats']['source_origins']==7712
    assert result['stats']['boundary_tail_deferred']==1904 and len(result['pending'])==1712
    assert result['stats']['unused_pool_bytes_avoided']>0
    for origin in result['blocks'].reshape(-1)[[0,2048,4095]]:
        for frame in range(5):
            end=int(origin['latest'][frame])+1
            oldend=np.searchsorted(before[frame]['end_us'],origin['end_us'],side='right')
            assert result['views'][frame][end-64:end].tobytes()==before[frame][oldend-64:oldend].tobytes()
    # A subsequent plan cannot reuse either plan's origins or join two tails.
    nextroot=tmp_path/'second';receipt(nextroot,start=0,blocks=1,coverage=result['origin_coverage'])
    value,rows=compiled();result=uncovered(value,rows,'TEST','2025-01-02',[(parent,'plan'),(nextroot,'plan')])
    assert len(result['blocks'])==0 and result['stats']['source_origins']==3616
    assert result['sampling']['reason']=='no_remaining_complete_session_block'


def test_coverage_is_per_ticker_session_and_retains_unseen_sessions(tmp_path):
    parent=tmp_path/'first';receipt(parent)
    value,rows=compiled();result=uncovered(value,rows,'TEST','2025-01-03',[(parent,'plan')])
    assert result['origin_coverage']['excluded_ranges']==[]
    assert result['origin_coverage']['ranges']==[[0,16384]]


def test_source_count_drift_and_preexisting_overlap_fail_closed(tmp_path):
    a=tmp_path/'a';b=tmp_path/'b';receipt(a);receipt(b)
    with pytest.raises(ValueError,match='count drifted'):prior_ranges('TEST','2025-01-02',20001,[(a,'plan')])
    with pytest.raises(ValueError,match='overlapping'):prior_ranges('TEST','2025-01-02',20000,[(a,'plan'),(b,'plan')])


def test_prepare_next_plan_preserves_frozen_inputs_and_exposes_exact_exclusions(tmp_path,monkeypatch):
    import polars as pl
    from research.bar_gpt.v5 import phase2_build_campaign_v1 as campaign
    from research.bar_gpt.v5.phase2_ar_shards import source
    from research.bar_gpt.v5.shards.contract import file_hash
    base=tmp_path/'base';inventory=base/'inventory/plan';inventory.mkdir(parents=True)
    table=pl.DataFrame(dict(ticker=['A','B','C'],source_date=['2025-01-02','2025-01-02','2026-01-02'],event_count=[5000,3000,5000]))
    table.write_parquet(inventory/'part.parquet')
    units=pl.DataFrame([dict(ticker=t,month=f'{year}-01',year=year,split=int(year==2026),liquid=1,first_us=1,last_us=2,random_order=i)
        for i,(t,year) in enumerate((('A',2025),('B',2025),('C',2026)))])
    units.write_parquet(base/'sample-units.parquet')
    plan=dict(version='bargpt-phase2-ar-sampled-v2',geometry=json.loads(json.dumps(Geometry().manifest())),
        parent_binding='parent',ordinal_hash='ordinal',authority={},source_query='unchanged',quote_policy={},
        code={'producer.py':'same','phase2_ar_shards/sampling.py':'old'},jobs=[],seed=10,
        partitions=[dict(name='part.parquet',sha256=file_hash(inventory/'part.parquet'))],
        sample_units=dict(name='sample-units.parquet',sha256=file_hash(base/'sample-units.parquet'),rows=3))
    plan['hash']=digest(plan);(base/'plan.json').write_text(json.dumps(plan))
    (base/'provenance.json').write_text(json.dumps(dict(parent=str(tmp_path/'phase1'))))
    (base/'result.json').write_text(json.dumps(dict(binding=plan['hash'],state='inventory_exhausted',failure=None)))
    before={p.name:file_hash(p) for p in (base/'plan.json',base/'sample-units.parquet',inventory/'part.parquet')}
    monkeypatch.setattr(source,'describe',lambda *_:(dict(code={'producer.py':'same','phase2_ar_shards/sampling.py':'new'}),{}))
    root=tmp_path/'next';result=campaign.prepare_plan(base,root,[base],'tested',11)
    assert pl.read_parquet(root/'sample-units.parquet')['ticker'].to_list()==['A']
    assert result['continuation']['validation_units_closed']==1
    assert result['continuation']['event_bound_exclusions']['rows']==1
    assert result['geometry']==plan['geometry'] and result['parent_binding']==plan['parent_binding']
    assert before=={p.name:file_hash(p) for p in (base/'plan.json',base/'sample-units.parquet',inventory/'part.parquet')}
    assert campaign.prepare_plan(base,root,[base],'tested',11)==result
    with pytest.raises(ValueError,match='ancestry'):campaign.prepare_plan(base,root,[base],'tested',12)


def test_heavy_first_changes_only_ticker_execution_order():
    from research.bar_gpt.v5.phase2_ar_shards.assignment import assign
    units=[dict(ticker=t,month='2025-01') for t in ('SMALL','LARGE','MID')]
    jobs=[dict(ticker=t,years=[dict(year=2025,events=n)]) for t,n in (('SMALL',12),('LARGE',1200),('MID',120))]
    before,_=assign(units,jobs,1);after,_=assign(units,jobs,1,heavy_first=True)
    assert [x['ticker'] for x in after[0]]==['LARGE','MID','SMALL']
    assert sorted(before[0],key=lambda u:u['ticker'])==sorted(after[0],key=lambda u:u['ticker'])


def test_campaign_budget_counts_preserved_and_new_plan_bytes_together(tmp_path):
    from types import SimpleNamespace
    from research.bar_gpt.v5.phase2_build_campaign_v1 import snapshot
    a=tmp_path/'first';b=tmp_path/'second';c=tmp_path/'campaign'
    for path in (a,b,c):path.mkdir()
    (a/'result.json').write_text(json.dumps(dict(state='inventory_exhausted',failure=None,bytes=407813163361,
        train_bytes=407564575492,validation_bytes=248587869,units=365160,selected_origins=271876096)))
    (b/'progress.json').write_text(json.dumps(dict(bytes=1000,train_bytes=1000,validation_bytes=0,units=1,
        selected_origins=4096,worker_assignments=[],state='running')))
    args=SimpleNamespace(target_bytes=4_000_000_000_000,validation_bytes=250_000_000,workers=32,io_workers=8,source_revision='tested')
    p=snapshot(c,[a],b,args)
    assert p['bytes']==407813164361 and p['train_bytes']==407564576492
    assert p['capacity']['validation']['blocked'] and not p['capacity']['training']['blocked']
    assert json.loads((c/'execution.json').read_text())['worker_root']=='second'
