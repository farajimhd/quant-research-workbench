"""Exact clock/window semantics and packed banks at publication boundaries."""
from dataclasses import replace
import numpy as np
import pytest
import torch
from research.bar_gpt.phase2_v1.tokenizer import SessionTokens,normalize
from research.bar_gpt.phase2_v1.data import checked_unit,plan_block
from research.bar_gpt.v5.transforms_v1 import BarInputTransformV1
from research.bar_gpt.v5.phase2_ar_shards.contract import FRAMES
from tests.test_bargpt_phase2_latent_v1 import session,Teacher


@pytest.fixture(autouse=True)
def one_thread():
    old=torch.get_num_threads();torch.set_num_threads(1)
    yield
    torch.set_num_threads(old)


@pytest.mark.parametrize('device',['cpu','cuda'])
def test_cached_clock_fields_exact_at_tile_gap_year_and_window_boundaries(device):
    if device=='cuda' and not torch.cuda.is_available():pytest.skip('Native CUDA qualification')
    s=SessionTokens.__new__(SessionTokens);s.device=torch.device(device)
    s.transform=BarInputTransformV1().to(device);s.time_cache={};s.pools=[]
    for frame in FRAMES:
        # Includes nonuniform gaps, partial last tiles, ISO year/week boundaries.
        clocks=1_609_286_400_000_000+torch.arange(201,device=device,dtype=torch.int64)*frame
        clocks[80:]+=3*frame;clocks[150:]+=7*frame
        s.pools.append((None,None,clocks))
    for f,frame in enumerate(FRAMES):
        ends=torch.tensor([63,64,79,80,127,128,193,200],device=device)
        gather=ends[:,None]-torch.arange(63,-1,-1,device=device)
        raw=torch.zeros((len(ends),64,18),device=device);valid=torch.zeros_like(raw,dtype=torch.bool)
        expected=s.transform(raw,valid,s.pools[f][2][gather],frame)
        before=torch.get_rng_state().clone();values,mask=s.window_time(f,gather)
        assert torch.equal(before,torch.get_rng_state())
        assert torch.equal(values,expected.values[...,18:])
        assert torch.equal(mask,expected.valid[...,18:])
        assert torch.equal(values,s.window_time(f,gather)[0])
        assert not mask[:,0,4].any() and not values[:,0,4].any()


def old_batch(s,p,start,stop):
    tokens,refs,starts,ends=[],[],[],[]
    for f,selected in enumerate(p.endpoints):
        ids=torch.as_tensor(selected[start:stop].copy(),device=s.device)
        tokens.append(s.bank[f][ids]);refs.append(s.refs[f][ids])
        starts.append(s.pools[f][2][ids-63]);ends.append(s.pools[f][2][ids])
    token=torch.cat(tokens,1);reference=torch.cat(refs,1)
    origin=torch.as_tensor(p.origin_us[start:stop].copy(),device=s.device)
    a=origin[:,None]-torch.cat(starts,1);b=origin[:,None]-torch.cat(ends,1)
    meta=torch.cat((torch.log1p(a.double()/1e6)[...,None],
                    torch.log1p(b.double()/1e6)[...,None],reference-reference[:,7:8]),-1).float()
    return token,meta,s.bank[0][torch.as_tensor(p.targets[start:stop].copy(),device=s.device)]


def test_exact_tokenizer_outputs_targets_metadata_and_resume_cold_cache(tmp_path):
    reader=checked_unit(session(tmp_path));p=plan_block(reader,0);teacher=Teacher()
    s=SessionTokens(reader,teacher,'pinned',token_batch=128);s.prepare(p)
    for start,stop in [(0,1),(1,65),(65,len(p.origins))]:
        assert all(torch.equal(a,b) for a,b in zip(s.batch(p,start,stop),old_batch(s,p,start,stop)))
    # Fresh cache at a mid-publication resume uses exactly the original tiles.
    suffix=replace(p,origins=p.origins[65:],endpoints=tuple(v[65:] for v in p.endpoints),
                   targets=p.targets[65:],origin_us=p.origin_us[65:])
    cold=SessionTokens(reader,teacher,'pinned',token_batch=128);cold.prepare(suffix)
    assert all(torch.equal(a,b) for a,b in zip(s.batch(p,65,len(p.origins)),cold.batch(suffix,0,len(suffix.origins))))
    # The full shared transform and unchanged fixed teacher tile agree exactly.
    for f in range(5):
        selected=np.flatnonzero(s.ready[f])
        for tile in np.unique((selected-63)//s.token_batch):
            start=63+int(tile)*s.token_batch
            ids=torch.arange(start,min(start+s.token_batch,len(s.ready[f])))
            gather=ids[:,None]-torch.arange(63,-1,-1)
            raw,evidence=s.pools[f][0][gather],s.pools[f][1][gather].bool()
            scaled,refs=normalize(raw,evidence)
            transformed=s.transform(scaled,evidence,s.pools[f][2][gather],FRAMES[f])
            expected=teacher.encode_full(scaled,evidence,transformed.values[...,18:],transformed.valid[...,18:])
            assert torch.equal(s.bank[f][ids],expected)
            assert torch.equal(s.refs[f][ids],refs)


def test_clock_cache_rejects_invalid_clock_and_uncached_targets(tmp_path):
    reader=checked_unit(session(tmp_path));s=SessionTokens(reader,Teacher(),'pinned')
    p=plan_block(reader,0)
    with pytest.raises(ValueError,match='uncached'):s.batch(p,0,1)
    s.pools[0][2][0]=-1
    with pytest.raises(ValueError):s.window_time(0,torch.arange(64)[None,:])


def test_compact_index_cache_reused_but_changed_plan_cannot_bypass_causality(tmp_path):
    reader=checked_unit(session(tmp_path));s=SessionTokens(reader,Teacher(),'pinned')
    p=plan_block(reader,0);s.prepare(p);s.batch(p,0,8)
    cached=s.index_cache[2]
    assert cached.shape==(len(p.origins),18) and cached.numel()*cached.element_size()<1024**2
    s.batch(p,8,16)
    assert s.index_cache[2] is cached
    p.endpoints[0][0,-1]+=1
    with pytest.raises(ValueError,match='Lookahead'):s.batch(p,0,8)
