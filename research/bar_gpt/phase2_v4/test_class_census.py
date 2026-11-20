import numpy as np
import torch
from .class_census import count_block
from .move_metrics import MoveMetrics
from .tokenizer import raw_logs


def test_census_matches_training_metric_population_all_horizons():
    rng=np.random.default_rng(17)
    values=(100+rng.normal(0,.3,(130,3))).astype(np.float32)
    values[66]=values[65]
    valid=np.ones_like(values,dtype=np.uint8);valid[70,1]=0;valid[64,2]=0
    ends=np.array([64,65]);horizons=np.arange(1,65)
    actual,missing=count_block(values,valid,ends,horizons)
    raw=torch.ones(2,64,18,dtype=torch.float32);mask=torch.ones_like(raw,dtype=torch.bool)
    last=torch.ones(2,18);lv=torch.ones_like(last,dtype=torch.bool)
    for c,j in enumerate((3,9,15)):
        raw[:,:,j]=torch.from_numpy(values[ends[:,None]+horizons[None,:],c])
        mask[:,:,j]=torch.from_numpy(valid[ends[:,None]+horizons[None,:],c].astype(bool))
        last[:,j]=torch.from_numpy(values[ends,c]);lv[:,j]=torch.from_numpy(valid[ends,c].astype(bool))
    target=dict(raw=raw,valid=mask,last_raw=last,last_valid=lv,last=raw_logs(last,lv))
    m=MoveMetrics('cpu');m.add(raw_logs(raw,mask),target)
    expected=m.arrays()[:,0,:,:,:,0]
    np.testing.assert_array_equal(actual,expected)
    np.testing.assert_array_equal(actual[0].sum(-1)+missing,2)


def test_first_candle_definitions_and_chunk_additivity():
    v=np.array([[100,100,100],[100.05,99.9,100],[100.1,100.1,99.],[101,99,100]],dtype=np.float64)
    valid=np.ones_like(v,dtype=bool);ends=np.arange(3)
    counts,_=count_block(v,valid,ends,np.array([1]))
    np.testing.assert_array_equal(counts[0],counts[1])
    separate=sum((count_block(v,valid,np.array([i]),np.array([1]))[0] for i in ends))
    np.testing.assert_array_equal(counts,separate)
