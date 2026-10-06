import copy
from research.rl_trading.v6.run_full_head_underfit import passes,underfit_rows
import numpy as np
import pytest
from research.rl_trading.v6.run_bias_campaign import flatten


def test_all_supervised_labels_required_for_underfit_admission():
    report={c:{'f1':.96} for c in ('ENTRY','WAIT','HOLD','EXIT')}
    report.update(future=[{c:{'count':3,'f1':.96} for c in ('ENTRY','WAIT','HOLD','EXIT')} for _ in range(5)],ratio_targets=32,ratio_mae=.01,quality_mae={'ENTRY':.01,'EXIT':.01},future_quality_mae=[{'ENTRY':.01,'EXIT':.01} for _ in range(5)])
    assert passes(report)
    missing=copy.deepcopy(report);del missing['future_quality_mae'];assert not passes(missing)
    for head in ('future','ratio','quality','current'):
        bad=copy.deepcopy(report)
        if head=='future':bad['future'][4]['ENTRY']['f1']=0
        elif head=='ratio':bad['ratio_mae']=None
        elif head=='quality':bad['quality_mae']['EXIT']=.03
        else:bad['WAIT']['f1']=0
        assert not passes(bad)


def test_forecast_metrics_are_logged_per_horizon_per_label():
    assert flatten({'future':[{'ENTRY':{'f1':.9}},{'EXIT':{'f1':.8}}]},'model')=={'model/future/h0/ENTRY/f1':.9,'model/future/h1/EXIT/f1':.8}


def test_sampling_keeps_current_balance_and_every_forecast_class():
    actions=np.tile(np.arange(4),100);future=np.stack([(actions+h)%4 for h in range(5)],axis=1)
    rows=underfit_rows(actions,future)
    assert len(rows)==len(np.unique(rows))==128
    assert np.bincount(actions[rows]).tolist()==[32]*4
    assert all(set(future[rows,h])==set(range(4)) for h in range(5))
    np.testing.assert_array_equal(rows,underfit_rows(actions,future))
    with pytest.raises(ValueError,match='Source lacks'):underfit_rows(actions,np.zeros_like(future))


def test_larger_underfit_sample_retains_exact_balance_and_coverage():
    actions=np.tile(np.arange(4),600);future=np.stack([(actions+h)%4 for h in range(5)],axis=1)
    rows=underfit_rows(actions,future,per_class=512)
    assert len(rows)==len(np.unique(rows))==2048
    assert np.bincount(actions[rows]).tolist()==[512]*4
    assert all(set(future[rows,h])==set(range(4)) for h in range(5))
