import json
from types import SimpleNamespace

import numpy as np
import pytest
import torch

from research.bar_gpt.v5.price_relative_v3 import PriceRelative, PRICES, CONTRACT
from research.bar_gpt.v5.native_validation_v3 import NativeMAE, FRAME_NAMES, inverse
from research.bar_gpt.v5.shards.phase1_contract import CHANNELS
from research.bar_gpt.v5.shards.contract import FRAMES


def add(acc, prices, errors, frames=None, valid=None):
    y = torch.tensor(prices, dtype=torch.float64)[:, None, None].expand(-1,18,64).clone()
    e = torch.tensor(errors, dtype=torch.float64)[:, None, None].expand_as(y).clone()
    mask = torch.ones_like(y,dtype=torch.bool) if valid is None else valid
    acc.add(e,y,mask,torch.tensor(frames if frames is not None else [1]*len(y)))


def test_per_observation_dollar_denominator_and_bands_all_price_channels():
    acc=PriceRelative();add(acc,[1,100],[.05,.05])
    m=acc.metrics('validation_full_raw_mae',FRAME_NAMES,CHANNELS)
    for h in PRICES:
        tail='1s/'+CHANNELS[h]
        assert m['validation_full_price_mape_pct/'+tail]==pytest.approx(2.525)
        assert m['validation_full_price_mape_pct_by_price_band/1_to_5/'+tail]==pytest.approx(5)
        assert m['validation_full_price_mape_pct_by_price_band/100_and_above/'+tail]==pytest.approx(.05)
        assert m['validation_full_price_p90_ape_pct/'+tail]==pytest.approx(5,rel=CONTRACT['quantile_max_bin_relative_width'])
    assert not any('/trade_volume' in k or '/ask_size' in k or '/bid_count' in k for k in m)
    assert not any(k.startswith('validation_full_raw_mae/') for k in m)
    json.dumps(m,allow_nan=False)


def test_exact_band_boundaries_all_frames_and_empty_groups():
    a=PriceRelative();add(a,[.5,1,5,20,100],[.005,.01,.05,.2,1],frames=[0,1,2,3,5])
    m=a.metrics('validation_full_raw_mae',FRAME_NAMES,CHANNELS)
    for frame,band in zip(['100ms','1s','10s','1m','daily'],['below_1','1_to_5','5_to_20','20_to_100','100_and_above']):
        assert m[f'validation_full_price_mape_pct_by_price_band/{band}/{frame}/trade_high']==pytest.approx(1)
    assert m['validation_full_price_mape_pct/1h/trade_high'] is None
    assert m['validation_full_price_relative_counts/1h/trade_high']==0


def test_merge_weights_counts_and_explicit_bad_denominators_and_predictions():
    a=PriceRelative();b=PriceRelative();add(a,[1],[.01]);add(b,[1,1,1],[.03,.03,.03]);a.merge(b)
    assert a.metrics('x_raw_mae',FRAME_NAMES,CHANNELS)['x_price_mape_pct/1s/trade_high']==pytest.approx(2.5)
    c=PriceRelative();add(c,[0,-1,float('nan'),1],[1,1,1,float('inf')])
    m=c.metrics('validation_full_raw_mae',FRAME_NAMES,CHANNELS)
    assert m['validation_full_price_relative_excluded/1s/trade_high']==192
    assert m['validation_full_price_relative_overflows/1s/trade_high']==64
    assert m['validation_full_price_mape_pct/1s/trade_high'] is None
    json.dumps(m,allow_nan=False)


def test_inverse_price_is_used_and_old_raw_metrics_are_unchanged():
    targets=torch.ones((6,18,64),dtype=torch.float64)
    targets[:,PRICES]=100
    reference=torch.full((6,1,1),100.,dtype=torch.float64)
    stats=torch.zeros((6,6),dtype=torch.float64)
    prediction=torch.zeros_like(targets)
    prediction[:,PRICES]=100*np.log(1.05)
    batch=SimpleNamespace(native_reference=reference,native_stats=stats,native_targets=targets,
                          native_valid=torch.ones_like(targets,dtype=torch.bool),native_frames=torch.tensor(FRAMES))
    a=NativeMAE('cpu');a.add(prediction,batch);m=a.metrics()
    expected=(inverse(prediction,reference,stats)-targets).abs()
    for f,frame in enumerate(FRAME_NAMES):
        for h,channel in enumerate(CHANNELS):
            assert m[f'validation_full_raw_mae/{frame}/{channel}']==pytest.approx(expected[f,h].mean().item())
        assert m[f'validation_full_price_mape_pct/{frame}/trade_high']==pytest.approx(5)
        assert m[f'validation_full_price_median_ape_pct/{frame}/trade_high']==pytest.approx(5,rel=.006)
    assert a.price_relative.hist.nbytes<30*1024**2


def test_zero_error_and_extreme_quantile_are_explicit():
    a=PriceRelative();add(a,[1],[0]);m=a.metrics('v_raw_mae',FRAME_NAMES,CHANNELS)
    assert m['v_price_median_ape_pct/1s/trade_high']==0
    b=PriceRelative();add(b,[1],[1e12]);m=b.metrics('v_raw_mae',FRAME_NAMES,CHANNELS)
    assert m['v_price_mape_pct/1s/trade_high']==1e14
    assert m['v_price_p90_ape_pct/1s/trade_high'] is None


def test_restart_requires_complete_validation_guard_and_exact_epoch():
    from research.bar_gpt.v5.run_price_metrics_upgrade_v3 import boundary_ready, resume_command
    state=dict(epoch=19,phase='validation',step=100,scheduler=dict(updates=100),training_metrics=dict(complete_pass=True),history=[dict(
        epoch=19,training=dict(complete_pass=True),validation=dict(complete_pass=True),learning_guard=dict(action='continue'))])
    assert boundary_ready(state,20)
    assert not boundary_ready(state,19)
    state['history'][-1]['learning_guard']['action']='stop'
    assert not boundary_ready(state,20)
    state['history'][-1]['learning_guard']['action']='continue'
    state['phase']='train'
    assert not boundary_ready(state,20)
    original=['python','-B','-m','research.bar_gpt.v5.run_encoder_v3','--output','old','--resume','old.pt','--source-revision','old','--workers','16','--prepare-threads','1']
    changed=resume_command(original,'epoch20.pt','new','revision')
    assert original[original.index('--output')+1]=='old'
    for flag in ('--workers','--prepare-threads'):
        assert changed[changed.index(flag)+1]==original[original.index(flag)+1]


def test_reporter_keeps_current_step_and_old_history_has_no_new_keys():
    import io
    from research.bar_gpt.v5.reporting_v1 import Reporter
    rows=[]
    reporter=Reporter.__new__(Reporter)
    reporter.global_step_offset=123
    reporter.journal=io.StringIO()
    reporter.run=SimpleNamespace(log=lambda value:rows.append(value))
    reporter.log({'validation_full_raw_mae/1s/trade_high':.05},100)
    a=PriceRelative();add(a,[1],[.05]);m=a.metrics('validation_full_raw_mae',FRAME_NAMES,CHANNELS)
    reporter.log(m,200)
    journal=[json.loads(line) for line in reporter.journal.getvalue().splitlines()]
    assert not any('price_mape' in key for key in journal[0])
    assert rows[1]['global_step']==323 and rows[1]['optimizer_global_step']==200
    assert journal[1]['step']==200 and journal[1]['reported_global_step']==323
    assert rows[1]['validation_full_price_mape_pct/1s/trade_high']==pytest.approx(5)


def test_boundary_observer_retries_transient_windows_publication_races():
    from research.bar_gpt.v5.run_price_metrics_upgrade_v3 import read_json
    class ChangingFile:
        calls=0
        def read_text(self,**kwargs):
            self.calls+=1
            if self.calls<3:raise PermissionError('Writer replacing progress')
            return '{"state":"running"}'
    file=ChangingFile()
    assert read_json(file)=={'state':'running'} and file.calls==3


@pytest.mark.parametrize('dtype',[torch.float32,torch.float64])
def test_fused_reducer_matches_numpy_histogram_all_frames_bands_and_masks(dtype):
    from research.bar_gpt.v5.price_relative_v3 import NBINS
    rng=np.random.default_rng(81)
    y=torch.tensor(10.**rng.uniform(-5,4,(48,18,64)),dtype=dtype)
    errors=torch.tensor(rng.uniform(0,.5,(48,18,64)),dtype=torch.float64)
    mask=torch.tensor(rng.uniform(size=(48,18,64))>.2)
    frames=torch.arange(48)%6
    y[0,0,:4]=torch.tensor([0,-1,float('nan'),float('inf')],dtype=dtype)
    errors[1,PRICES[0],0]=float('inf')
    result=PriceRelative();result.add(errors,y,mask,frames)
    for f in range(6):
        rows=(frames==f).numpy()
        for h,c in enumerate(PRICES):
            truth=y.numpy()[rows,c].reshape(-1).astype(np.float64)
            err=errors.numpy()[rows,c].reshape(-1)
            valid=mask.numpy()[rows,c].reshape(-1)
            eligible=valid&np.isfinite(truth)&(truth>0)
            assert result.excluded[f,h]==np.count_nonzero(valid&~eligible)
            truth,err=truth[eligible],err[eligible]
            ape=100*err/truth
            bands=np.searchsorted([1.,5.,20.,100.],truth,side='right')+1
            for band in range(6):
                values=ape if band==0 else ape[bands==band]
                assert result.counts[f,h,band]==len(values)
                assert result.bad[f,h,band]==np.count_nonzero(~np.isfinite(values))
                values=values[np.isfinite(values)]
                assert result.sums[f,h,band]==pytest.approx(values.sum(),rel=2e-14,abs=1e-10)
                with np.errstate(divide='ignore'):
                    bins=np.floor(np.clip((np.log10(values)+9)*(8192/18),-1,8192)).astype(np.int64)+2
                bins[values==0]=0
                assert np.array_equal(result.hist[f,h,band],np.bincount(bins,minlength=NBINS))


def test_fused_frame_index_validation_prevents_out_of_bounds():
    a=PriceRelative();x=torch.ones(1,18,64)
    with pytest.raises(ValueError,match='aligned'):
        a.add(x,x,x.bool(),torch.tensor([6]))
