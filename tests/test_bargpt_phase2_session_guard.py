import copy
from types import SimpleNamespace
import pytest
import torch
from research.bar_gpt.phase2_bars_v1.early_stop import SessionGuard
from research.bar_gpt.phase2_bars_v1.run_train import arguments
from research.bar_gpt.phase2_bars_v1.model import Forecaster


def test_cli_defaults_and_explicit_overrides(tmp_path):
    base=['--dataset-root','unused','--output',str(tmp_path)]
    a=arguments(base)
    assert (a.patience,a.stop_min_session_fraction,a.stop_stale_session_fraction)==(24,.1,.02)
    from research.bar_gpt.phase2_v2.run_train import arguments as legacy_arguments
    assert legacy_arguments(base).patience==8
    a=arguments(base+['--patience=30','--stop-min-session-fraction','.25'])
    assert a.patience==30 and a.stop_min_session_fraction==.25
    for value in ('nan','0','1.1'):
        with pytest.raises(SystemExit):arguments(base+['--stop-stale-session-fraction',value])


def test_all_coverage_gates_and_resume_history():
    units=[SimpleNamespace(blocks=[(0,int(i==2),1,'x')]) for i in range(101)]
    args=SimpleNamespace(patience=24,stop_min_session_fraction=.1,stop_stale_session_fraction=.02)
    guard=SessionGuard(units,args);history=[]
    def evaluate(ui,value,stale,epoch=0,exhausted=False):
        history.append(dict(validation={'mse':value}))
        review=guard.review(epoch=epoch,cursor=(ui,0,0),exhausted=exhausted,history=history,stale=stale)
        history[-1]['learning_guard']=review
        return review
    assert not evaluate(1,1.,0)['stop']
    assert not evaluate(5,2.,24)['stop']  # not enough total coverage
    assert not evaluate(11,.9,0)['stop'] # new best resets both gates
    assert not evaluate(12,2.,24)['stop'] # only one new publication
    assert not evaluate(14,2.,23)['stop'] # enough sessions, too few validations
    r=evaluate(14,2.,24);assert r['stop'] and r['session_publications_seen']==13
    assert r['planned_session_publications']==100 # validation publication excluded
    restored=SessionGuard(units,args)
    assert restored.review(epoch=0,cursor=(14,0,0),exhausted=False,
        history=copy.deepcopy(history),stale=24)==r
    assert evaluate(100,2.,25,exhausted=True)['session_publications_seen']==100
    assert evaluate(1,2.,26,epoch=1)['session_publications_seen']==101


def test_no_implicit_old_history_migration():
    g=SessionGuard([SimpleNamespace(blocks=[(0,0,1,'x')])],SimpleNamespace(patience=24))
    with pytest.raises(ValueError,match='explicit policy-bound'):
        g.review(epoch=0,cursor=(0,0,0),exhausted=False,
                 history=[{'validation':{'mse':1.}},{'validation':{'mse':2.}}],stale=24)


def test_compact_index_counts_publications_not_blocks():
    import polars as pl
    table=pl.DataFrame({'unit_split':[0,1,0],'ordinal':[[0,1,2],[0],[0]]})
    g=SessionGuard(SimpleNamespace(groups=[(None,None,table)]),SimpleNamespace(patience=24))
    assert g.total==2 and g.prefix.tolist()==[0,1,1,2]


def test_unpositioned_encoder_permutation_is_architectural_not_head_concatenation():
    old=torch.get_num_threads();torch.set_num_threads(1)
    try:
        torch.manual_seed(9);model=Forecaster().eval();x=torch.randn(3,16,512)
        perm=[15,14,13,12,11,10,9,7,8,6,5,4,3,2,1,0]
        with torch.no_grad():
            a=model(x)[1];b=model(x[:,perm])[1]
        torch.testing.assert_close(a,b,atol=2e-6,rtol=2e-5)
        assert not torch.allclose(a,model(x.flip(0))[1])
    finally:torch.set_num_threads(old)
