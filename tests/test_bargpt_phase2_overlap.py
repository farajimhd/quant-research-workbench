import copy
import json
import numpy as np
import pytest
from research.bar_gpt.v5.phase2_ar_shards.contract import BAR, BLOCK, Compiler, Geometry
from research.bar_gpt.v5.phase2_ar_shards.storage import publish, ShardReader
from research.bar_gpt.v5.phase2_ar_shards.overlap import rebuild, POLICY
from research.bar_gpt.v5.shards.sql import clock
from research.bar_gpt.v5.shards.contract import digest


def parent(tmp_path, name='parent', count=10000):
    day='2025-01-02';opened=clock(day,4)
    rows=np.zeros(count,dtype=BAR);rows['end_us']=opened+np.arange(1,count+1)*1000000
    rows['values']=10;rows['valid']=1
    history=rows[:64].copy();history['end_us']=opened+np.arange(-63,1)*1000000
    c=Compiler(Geometry());c.bootstrap([history.copy() for _ in range(5)])
    value=c.compile([rows.copy() for _ in range(5)],1)
    value.update(independent_unit=True,price_basis=dict(session_date=day,basis_us=opened),
                 sampling=dict(session_origins=count,unselected_prefix_origins=0,reason='complete_blocks'))
    value['splits']=[0]*len(value['blocks'])
    root=tmp_path/name
    publish(root,'TEST',day,1,'binding','source',value,Geometry())
    return ShardReader(root/'shards'/digest('TEST')[:24]/day,'binding')


def test_overlap_reuses_exact_context_and_excludes_identical_blocks(tmp_path):
    reader=parent(tmp_path);result=rebuild([reader],Geometry(),256,42)
    starts=result['origin_coverage']['block_starts']
    assert len(starts)>2 and not ({0,4096}&set(starts)) and len(starts)==len(set(starts))
    assert result['blocks'].shape==(len(starts),BLOCK)
    assert result['stats']['source_origins']==len(starts)*BLOCK
    assert np.all(np.diff(result['blocks']['end_us'],axis=1)==1000000)
    for origin in result['blocks'].reshape(-1)[::819]:
        for frame in range(5):
            old=reader.view(frame);end=np.searchsorted(old['end_us'],origin['end_us'],side='right')
            index=int(origin['latest'][frame])+1
            assert old[end-64:end].tobytes()==result['views'][frame][index-64:index].tobytes()
    publish(tmp_path/'new','TEST','2025-01-02',1,'newbinding','source',result,Geometry())
    new=ShardReader(tmp_path/'new/shards'/digest('TEST')[:24]/'2025-01-02','newbinding')
    again=rebuild([reader,new],Geometry(),256,42)
    assert len(again['blocks'])==0
    other=rebuild([reader,new],Geometry(),256,43)
    assert not (set(starts)&set(other['origin_coverage']['block_starts']))


def test_parent_payload_corruption_fails_closed(tmp_path):
    reader=parent(tmp_path);path=reader.directory/'origins.npy'
    data=bytearray(path.read_bytes());data[-1]^=1;path.write_bytes(data)
    with pytest.raises(ValueError,match='mutation'):rebuild([reader],Geometry(),256,42)


def test_parent_split_basis_and_rank_count_drift_fail(tmp_path):
    a=parent(tmp_path,'a');b=parent(tmp_path,'b')
    b.receipt['price_basis']['basis_us']+=1
    with pytest.raises(ValueError,match='basis'):rebuild([a,b],Geometry(),256,42)
    b.receipt['price_basis']=copy.deepcopy(a.receipt['price_basis'])
    b.receipt['sampling']['session_origins']+=1
    with pytest.raises(ValueError,match='basis'):rebuild([a,b],Geometry(),256,42)


def test_nonconsecutive_parent_rank_runs_never_bridge(tmp_path):
    reader=parent(tmp_path,count=20000)
    reader.receipt['origin_coverage']=dict(version=POLICY,block_starts=[0,6000,11000,15000])
    # Changed rank evidence conflicts with pending ranks/clock order: fail closed.
    with pytest.raises(ValueError):rebuild([reader],Geometry(),256,42)
