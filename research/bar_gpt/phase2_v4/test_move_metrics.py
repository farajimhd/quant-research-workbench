import numpy as np
import pytest
import torch
from .move_metrics import MoveMetrics
from .test_forecast_metrics import example
from ..v5.checkpoint_transition_v1 import state_hash


def test_bucket_boundaries():
    m=MoveMetrics('cpu')
    x=torch.tensor([0,-.0001,-.05,-.05001,-.1,-.25,-.5,-.5001,.05,.1,.25,.5,.5001],dtype=torch.float64)
    assert m.bucket(x).tolist()==[0,1,1,2,2,3,4,5,6,7,8,9,10]


def test_independent_numpy_reduction_and_validity(tmp_path):
    t=example();t['valid'][0,4,3]=False;t['last_valid'][1,9]=False
    p=t['logs']+.001
    m=MoveMetrics('cpu');m.add(p,t);a=m.arrays();expected=np.zeros_like(a)
    # Deliberately simple reference; production uses batched scatter, no row loops.
    for row in range(2):
        for h in range(64):
            for c,j in enumerate((3,9,15)):
                if not (t['valid'][row,h,j] and t['last_valid'][row,j] and (t['valid'][row,h-1,j] if h else t['last_valid'][row,j])):continue
                y=float(t['raw'][row,h,j]);last=float(t['last_raw'][row,j]);prev=float(t['raw'][row,h-1,j]) if h else last
                plog=float(p[row,h,j]);lastlog=float(t['last'][row,j]);pred=np.exp(plog)
                for d,ref in enumerate((last,prev)):
                    predref=lastlog if d==0 or h==0 else float(p[row,h-1,j])
                    actual=100*(y/ref-1);prediction=100*np.expm1(plog-predref)
                    correct=float(np.sign(actual)==np.sign(prediction));moving=float(actual!=0)
                    v=[1,correct,moving,correct*moving,abs(pred-y),100*abs(pred-y)/y,abs(last-y),abs(prev-y),actual,prediction]
                    for g,r in enumerate((actual,prediction)):
                        b=0 if r==0 else 1+np.searchsorted([.05,.1,.25,.5],abs(r),side='left')+5*int(r>0)
                        expected[d,g,h,c,b]+=v
    np.testing.assert_allclose(a,expected,rtol=1e-10,atol=1e-10)
    assert state_hash(m.result())
    path=m.save(tmp_path,123)
    with np.load(path,allow_pickle=False) as saved:
        assert int(saved['step'])==123
        np.testing.assert_array_equal(saved['sums'],a)
    # Both grouping methods and return definitions retain the common population.
    np.testing.assert_array_equal(a[0,0,...,0].sum(-1),a[1,1,...,0].sum(-1))


def test_perfect_predictions_and_empty_groups():
    t=example();m=MoveMetrics('cpu');m.add(t['logs'],t);r=m.result()
    for k,v in r.items():
        if k.endswith('direction_moving_pct') and v is not None:assert v==100
        assert v is None or type(v) in (int,float)
    t['valid'][:]=False;m=MoveMetrics('cpu');m.add(t['logs'],t)
    assert all(v is None for k,v in m.result().items() if k.endswith('raw_mae'))
