"""Continuous-state causal, missing-evidence and feature-bank regression gates."""
import numpy as np
import pytest
import torch
from research.vectorized_backtest.v16.indicator_math import calculate,initial_state
from research.vectorized_backtest.v16.features import CATALOG,ORIGINAL,SPECS
from research.vectorized_backtest.v16.program import Program,Node,Op,TorchPrograms


def run(price,state=None,factor=1.,emit=None,valid=None):
    p=np.asarray(price,dtype=np.float64);s,c=initial_state() if state is None else state
    valid=np.ones(len(p),bool) if valid is None else valid
    return calculate(p,p+.25,p-.25,valid,valid,s,c,factor,np.ones(len(p),bool) if emit is None else emit)


def test_indicator_continuity_exact_and_future_independent():
    p=10+np.sin(np.arange(400)/11)
    whole,s,c=run(p)
    first,s1,c1=run(p[:200]);second,_,_=run(p[200:],(s1,c1))
    np.testing.assert_array_equal(whole,np.concatenate((first,second)))
    altered=p.copy();altered[200:]*=9
    future,_,_=run(altered)
    np.testing.assert_array_equal(whole[:200],future[:200])
    assert np.isfinite(whole[-1]).all()


def test_indicator_split_basis_and_emit_projection():
    p=10+np.arange(200)/100
    full,_,_=run(p)
    _,s,c=run(p[:100]);valid=np.ones(100,bool)
    after,_,_=calculate(p[100:]/2,(p[100:]+.25)/2,(p[100:]-.25)/2,valid,valid,s,c,.5,valid)
    for i,spec in enumerate(SPECS):
        expected=full[100:,i] if spec[-1] or spec[0]=='rsi' else full[100:,i]/2
        np.testing.assert_array_equal(after[:,i],expected)
    emit=np.arange(200)%7==0;projected,_,_=run(p,emit=emit)
    np.testing.assert_array_equal(projected,full[emit])


def test_indicator_warmup_and_missing_bars():
    p=np.arange(20,dtype=float)+1;valid=np.ones(20,bool);valid[5:8]=False
    actual,_,_=run(p,valid=valid);compacted,_,_=run(p[valid])
    np.testing.assert_array_equal(actual[valid],compacted)
    assert np.isnan(actual[~valid]).all()
    j=SPECS.index(('rsi',14,0,0,False))
    assert np.isnan(actual[:17,j]).all() and actual[17,j]==1


def test_boolean_rate_mask_propagates_through_conversion():
    i=next(i for i,f in enumerate(CATALOG) if f.unit=='bool')
    p=Program((Node(Op.FEATURE,feature=i),Node(Op.BOOL_RATIO,a=0),Node(Op.MEAN,a=1,window=3),Node(Op.CONSTANT,value=.5),Node(Op.GREATER,a=2,b=3)),4)
    values=torch.zeros((1,6,len(CATALOG)));values[0,:,i]=torch.tensor([1,0,1,1,0,1.])
    valid=torch.ones_like(values,dtype=torch.bool);valid[0,3,i]=False
    output,mask=TorchPrograms([p],CATALOG)(values,valid)
    assert output.shape==(1,1,6)
    assert mask[0,0,2] and output[0,0,2]==1
    assert not mask[0,0,3:].any()


def test_period_families_not_weighted_by_channel_count():
    from research.vectorized_backtest.v16.search_space import GROUPS,family
    grouped=[g for g in GROUPS if CATALOG[g[0]].name.startswith('indicator.')]
    assert len(grouped)==8
    assert all(len({family(CATALOG[i]) for i in g})==1 for g in grouped)


def test_distinct_successful_mutations_keep_reachable_edits():
    from research.vectorized_backtest.v16.genome import sample,effective_rules
    from research.vectorized_backtest.v16.variation import mutate_fields,reachable
    rng=np.random.default_rng(162237)
    for member in sample(rng,120):
        count=int(rng.integers(1,6));child,edits=mutate_fields(rng,member,count)
        assert len(edits)==len({(e['stage'],e['node'],e['field']) for e in edits})==count
        rules=effective_rules(child)
        for e in edits:
            if e['stage'] in rules and e['node']>=0:assert e['node'] in reachable(rules[e['stage']])


def test_validation_sort_by_training_rank_preserves_validation_rank():
    from research.vectorized_backtest.v16.ranking_display import SortedRows
    rows=[dict(candidate=1,serial=2,rank=1,training_rank=17,training_net_profit=100,metrics=dict(total_pnl=500)),
          dict(candidate=2,serial=3,rank=2,training_rank=1,training_net_profit=900,metrics=dict(total_pnl=-10))]
    panel=np.zeros((6,2));view=SortedRows(rows,np.arange(6),panel,panel,np.ones_like(panel,bool),'training_rank','validation')
    assert [r['training_rank'] for r in view[:]]==[1,17]
    assert [r['rank'] for r in view[:]]==[2,1]
    assert view[0]['training_net_profit']==900


def test_original_cache_and_separate_indicator_identity_both_bound(tmp_path):
    import json
    from test_vector_v7_lightweight import fixture_files
    from research.vectorized_backtest.v7.data import SessionData as OriginalData
    from research.vectorized_backtest.v7.feature_cache import prepare
    from research.vectorized_backtest.v16.data import SessionData
    from research.vectorized_backtest.v16.features import VERSION,VARIANTS
    from research.vectorized_backtest.v6.torch_backtest.runtime import file_hash
    from pathlib import Path
    root,history=fixture_files(tmp_path);old=OriginalData(root,history)
    cache=tmp_path/'cache';prepare(old,cache)
    bank=tmp_path/'periods';bank.mkdir();values=np.ones((len(old.arrays['features']),len(VARIANTS)),np.float32)
    np.save(bank/'values.npy',values)
    math_file=Path(__file__).resolve().parents[1]/'research/vectorized_backtest/v16/indicator_math.py'
    (bank/'complete.json').write_text(json.dumps(dict(status='complete',version=VERSION,input_sha256=old.identity['input_sha256'],
        values_sha256=file_hash(bank/'values.npy'),validation_opened=False,definition=dict(math_sha256=file_hash(math_file)))))
    new=SessionData(root,history,feature_cache=cache,indicator_root=bank)
    try:
        assert 'indicator_sha256' in new.identity
        actual,valid=new.feature_block(0,new.clocks,[0]);original,mask=old.feature_block(0,old.clocks,[0])
        torch.testing.assert_close(actual[...,:len(ORIGINAL)],original,rtol=0,atol=0)
        assert torch.equal(valid[...,:len(ORIGINAL)],mask)
        from research.vectorized_backtest.v16.io_pipeline import feature_tiles
        tiles=list(feature_tiles(new,[(0,new.clocks,[0])]))
        assert len(tiles)==1
        torch.testing.assert_close(tiles[0][1][0],actual,rtol=0,atol=0)
        assert torch.equal(tiles[0][1][1],valid)
    finally:new.close();old.close()
