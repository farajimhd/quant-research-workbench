import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace
import numpy as np
import pytest
from research.rl_trading.v7.experiments import entry_tree_policy as tree

spec=importlib.util.spec_from_file_location('entry_score_fixture',Path(__file__).with_name('test_rl_trading_v7_entry_score.py'))
fixtures=importlib.util.module_from_spec(spec);spec.loader.exec_module(fixtures)
authority=fixtures.authority


def test_fit_preserves_zero_weight_rows_and_separate_targets(monkeypatch):
    class Estimator:
        def __init__(self,**kwargs):assert kwargs==tree.PARAMETERS
        def fit(self,x,y,sample_weight):
            assert x.shape==(3,120)
            np.testing.assert_array_equal(y,[False,False,True])
            np.testing.assert_array_equal(sample_weight,[2.,0.,3.])
    monkeypatch.setattr(tree,'HistGradientBoostingClassifier',Estimator)
    tree.fit_model(np.zeros((3,120)),np.array([-20.,0.,30.]))
    obs=fixtures.snapshots();obs['y']=np.full((1,5),1e10)
    np.testing.assert_array_equal(tree.shared.feature_matrix(obs,'raw'),tree.shared.feature_matrix(fixtures.snapshots(),'raw'))


def test_wait_and_future_guard():
    scorer=tree.Scorer(None,False,{},['2026-01-05'])
    np.testing.assert_array_equal(scorer.score(fixtures.snapshots()),np.zeros((1,5)))
    with pytest.raises(ValueError,match='TRAINING'):scorer.for_session(SimpleNamespace(day='2030-01-01'))


def test_real_synthetic_main_resume_load_and_corruption(authority,tmp_path,monkeypatch):
    data,labels=authority
    for day in data.days[:60]:
        path=labels/(day+'.json');report=json.loads(path.read_text())
        for record in report['records']:
            if record['status']=='closed':
                record['fills'][-1]['price']=9.;record['net_dollars']=-12.
        tree.shared.write_json(path,report)
        tree.shared.write_json(labels/(day+'.complete.json'),dict(sha256=tree.shared.sha(path),contract_hash=report['contract_hash']))
    monkeypatch.setattr(tree.shared,'PredictiveDataset',lambda *_:data)
    monkeypatch.setattr(tree.shared,'load_forecast',lambda *_:pytest.fail('Opened forecast'))
    output=tmp_path/'tree'
    args=['--labels',str(labels),'--output',str(output)]
    assert tree.main(args)==0
    scorer=tree.load_bundle(output,data.source,'ignored')
    assert scorer.row_step==10
    obs=fixtures.snapshots(121.)
    expected=tree.scores(scorer.model,tree.shared.feature_matrix(obs,'raw')) if scorer.selected else np.zeros((1,5))
    np.testing.assert_array_equal(scorer.score(obs),expected)
    metrics=json.loads((output/'metrics.json').read_text())
    assert metrics['fit_zero_weight_count']==120 and metrics['fit_eligible']==480
    assert metrics['selected_candidate']==(metrics['development']['greedy_net_per_decision']>0)
    assert tree.main(args+['--resume'])==0
    with (output/'model.pkl').open('ab') as stream:stream.write(b'corrupt')
    monkeypatch.setattr(tree.pickle,'load',lambda *_:pytest.fail('Deserialized unverified pickle'))
    with pytest.raises(ValueError,match='hash'):tree.load_bundle(output,data.source)


def test_wrapper_routes_exact_loader(monkeypatch):
    from research.rl_trading.v7.experiments import entry_replay,entry_tree_policy_replay
    def run(argv,*,bundle_loader):
        assert bundle_loader is tree.load_bundle and argv==['--synthetic']
        return 19
    monkeypatch.setattr(entry_replay,'main',run)
    assert entry_tree_policy_replay.main(['--synthetic'])==19
