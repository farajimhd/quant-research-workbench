import numpy as np
import pytest
import torch
from .forecast_metrics import ForecastMetrics
from .tokenizer import raw_logs
from .resume_metrics import changed_files


def example():
    raw=torch.arange(64,dtype=torch.float64)[None,:,None].expand(2,64,18).clone()+100
    raw[1]=200-torch.arange(64,dtype=torch.float64)[:,None]
    raw[:,2]=raw[:,1]  # explicit unchanged transitions
    last=torch.stack((torch.full((18,),99.),torch.full((18,),201.))).double()
    valid=torch.ones_like(raw,dtype=torch.bool);lv=torch.ones_like(last,dtype=torch.bool)
    return dict(raw=raw,valid=valid,last_raw=last,last_valid=lv,logs=raw_logs(raw,valid),last=raw_logs(last,lv))


def test_shifted_control_matches_independent_numpy_and_counts():
    target=example();target['valid'][0,4,3]=False;target['last_valid'][1,9]=False
    p=target['logs']+.001
    m=ForecastMetrics('cpu');m.add(p,target);r=m.result()
    y=target['raw'].numpy();last=target['last_raw'].numpy()
    previous=np.concatenate((last[:,None],y[:,:-1]),axis=1)
    mask=target['valid'].numpy() & np.concatenate((target['last_valid'].numpy()[:,None],target['valid'].numpy()[:,:-1]),1)
    for c in (3,9,15):
        stem=f'forecast/baseline/shifted_actual_future_informed/all/{("trade_close","bid_close","ask_close")[(3,9,15).index(c)]}/'
        assert r[stem+'raw_mae']==pytest.approx(np.abs(previous-y)[:,:,c][mask[:,:,c]].mean())
        assert r[stem+'count']==int(mask[:,:,c].sum())
    assert r['forecast/baseline/shifted_actual_future_informed/bar_1/trade_close/raw_mae']==1
    assert r['forecast/baseline/shifted_actual_future_informed/bar_3/trade_close/raw_mae']==0
    assert r['forecast/baseline/shifted_actual_future_informed/bar_5/trade_close/count']==1
    assert r['forecast/baseline/shifted_actual_future_informed/bar_6/trade_close/count']==1


def test_direction_perfect_persistence_and_partition_merge():
    t=example();m=ForecastMetrics('cpu');m.add(t['logs'],t);r=m.result()
    from ..v5.checkpoint_transition_v1 import state_hash
    assert state_hash({'validation':r})  # Real checkpoint scalar contract.
    for key in ('from_origin','consecutive_path'):
        assert r[f'forecast/direction/{key}/all/trade_close/accuracy_moving_pct']==100
        assert r[f'forecast/direction/{key}/all/trade_close/accuracy_3way_pct']==100
    assert r['forecast/direction/consecutive_path/all/trade_close/unchanged_count']==2
    assert r['forecast/tolerance/all/trade_close/within_0.05pct']==100
    split=ForecastMetrics('cpu')
    for i in range(2):split.add(t['logs'][i:i+1],{k:v[i:i+1] for k,v in t.items()})
    assert split.result()==r
    persist=ForecastMetrics('cpu');persist.add(t['last'][:,None].expand(-1,64,-1),t)
    assert persist.result()['forecast/direction/from_origin/all/trade_close/accuracy_moving_pct']==0


def test_no_valid_denominator_and_resume_source_guard():
    t=example();t['valid'][:]=False;m=ForecastMetrics('cpu');m.add(t['logs'],t)
    assert m.result()['forecast/baseline/shifted_actual_future_informed/all/trade_close/raw_mae'] is None
    with pytest.raises(ValueError):changed_files({'research/bar_gpt/phase2_v4/model.py':'a'},{'research/bar_gpt/phase2_v4/model.py':'b'})
    assert changed_files({}, {'research/bar_gpt/phase2_v4/forecast_metrics.py':'x'})
