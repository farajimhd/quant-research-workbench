import copy
from research.rl_trading.v6.run_full_head_underfit import passes
from research.rl_trading.v6.run_bias_campaign import flatten


def test_all_supervised_labels_required_for_underfit_admission():
    report={c:{'f1':.96} for c in ('ENTRY','WAIT','HOLD','EXIT')}
    report.update(future=[{c:{'count':3,'f1':.96} for c in ('ENTRY','WAIT','HOLD','EXIT')} for _ in range(5)],ratio_targets=32,ratio_mae=.01,quality_mae={'ENTRY':.01,'EXIT':.01})
    assert passes(report)
    for head in ('future','ratio','quality','current'):
        bad=copy.deepcopy(report)
        if head=='future':bad['future'][4]['ENTRY']['f1']=0
        elif head=='ratio':bad['ratio_mae']=None
        elif head=='quality':bad['quality_mae']['EXIT']=.03
        else:bad['WAIT']['f1']=0
        assert not passes(bad)


def test_forecast_metrics_are_logged_per_horizon_per_label():
    assert flatten({'future':[{'ENTRY':{'f1':.9}},{'EXIT':{'f1':.8}}]},'model')=={'model/future/h0/ENTRY/f1':.9,'model/future/h1/EXIT/f1':.8}
