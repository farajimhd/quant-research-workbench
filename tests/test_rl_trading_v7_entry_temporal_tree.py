import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace
import numpy as np
import pytest
from research.rl_trading.v7.experiments import entry_temporal_tree as tree

spec=importlib.util.spec_from_file_location('entry_score_fixture',Path(__file__).with_name('test_rl_trading_v7_entry_score.py'))
fixtures=importlib.util.module_from_spec(spec);spec.loader.exec_module(fixtures)
authority=fixtures.authority


def test_fit_preserves_zero_weight_rows_and_separate_targets(monkeypatch):
    class Estimator:
        def __init__(self,**kwargs):assert kwargs==tree.PARAMETERS
        def fit(self,x,y,sample_weight):
            assert x.shape==(3,204)
            np.testing.assert_array_equal(y,[False,False,True])
            np.testing.assert_array_equal(sample_weight,[2.,0.,3.])
    monkeypatch.setattr(tree,'HistGradientBoostingClassifier',Estimator)
    tree.fit_model(np.zeros((3,204)),np.array([-20.,0.,30.]))
    obs=fixtures.snapshots();obs['y']=np.full((1,5),1e10)
    np.testing.assert_array_equal(tree.shared.feature_matrix(obs,'raw'),tree.shared.feature_matrix(fixtures.snapshots(),'raw'))


def test_analytic_statistics_padding_gaps_and_flat():
    seq=np.zeros((1,5,7,60,14),np.float32)
    seq[:,:,1,:30,:7]=999 # Padding ignored even if populated.
    seq[:,:,1,:30,7:]=1
    native=seq[:,:,1,-30:]
    u=np.linspace(0,1,30)
    native[...,:4]=u[None,None,:,None]*100
    native[...,1]+=10;native[...,2]-=10;native[...,7:]=1
    native[...,:15,4:6]=1;native[...,15:,4:6]=3
    stats=tree.ordered_statistics(seq).reshape(1,5,7,12)[0,0,1]
    np.testing.assert_allclose(stats[:6],[1,1.1/1.2,1/29,1,2,2],rtol=1e-6)
    assert stats[6:].all()
    native[...,10]=0;native[...,0,10]=1;native[...,-1,10]=1
    stats=tree.ordered_statistics(seq).reshape(1,5,7,12)[0,0,1]
    assert stats[0]==pytest.approx(1) and stats[8]==stats[9]==0
    native[...,:4]=0;native[...,7:]=1
    stats=tree.ordered_statistics(seq).reshape(1,5,7,12)[0,0,1]
    assert stats[0]==stats[1]==stats[2]==stats[3]==0 and stats[7]==0
    assert stats[6]==stats[8]==stats[9]==1
    obs=fixtures.snapshots();obs['y']=np.full((1,5),999.)
    assert tree.features(obs,seq).shape==(1,5,204)
    np.testing.assert_array_equal(tree.features(obs,seq),tree.features(fixtures.snapshots(),seq))


def test_bound_clock_guard_and_wait():
    obs=fixtures.snapshots();obs['account'][:,3]=12/57600
    parent=SimpleNamespace(model=None,selected=False,binding={})
    bound=tree.BoundScorer(parent,np.zeros((1,5,84)),obs,np.array([0]),np.array([12]))
    np.testing.assert_array_equal(bound.score(obs),np.zeros((1,5)))
    bad=dict(obs,account=obs['account'].copy());bad['account'][:,3]=13/57600
    with pytest.raises(ValueError,match='Clock'):bound.score(bad)
    scorer=tree.Scorer(None,False,{},SimpleNamespace(days=['2026-01-05']))
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
    data.source.root=tmp_path/'source'
    temporal_root=tmp_path/'temporal';temporal_root.mkdir()
    arrays=dict(rows=np.array([0],np.int64),seq=np.zeros((1,5,7,60,14),np.float32))
    for day in data.days:
        folder=temporal_root/day;folder.mkdir()
        tree.shared.write_json(folder/'complete.json',dict(parity_exact=True,parity_scalar_count=490,
            admitted_candidates=5,files={'seq.npy':'synthetic','rows.npy':'synthetic'}))
    temporal=SimpleNamespace(root=temporal_root,predictive=data,days=data.days,binding={'temporal':'synthetic'},load=lambda _:arrays)
    monkeypatch.setattr(tree,'TemporalDataset',lambda *_:temporal)
    monkeypatch.setattr(tree.shared,'load_forecast',lambda *_:pytest.fail('Opened forecast'))
    output=tmp_path/'tree'
    args=['--labels',str(labels),'--output',str(output)]
    assert tree.main(args)==0
    scorer=tree.load_bundle(output,data.source,'ignored')
    assert scorer.row_step==10
    obs=fixtures.snapshots(121.);obs['account'][:,3]=12/57600
    day=data.days[120]
    monkeypatch.setattr(tree.temporal_net,'causal_snapshots',lambda *_:obs)
    session=SimpleNamespace(day=day,tickers=['ABC'],selection=np.zeros((13,5),np.int32),features=np.repeat(obs['features'][...,:98],13,axis=0))
    bound=scorer.for_session(session)
    expected=tree.scores(scorer.model,tree.features(obs,arrays['seq'])) if scorer.selected else np.zeros((1,5))
    np.testing.assert_array_equal(bound.score(obs),expected)
    metrics=json.loads((output/'metrics.json').read_text())
    assert metrics['fit_zero_weight_count']==120 and metrics['fit_eligible']==480
    assert metrics['selected_candidate']==(metrics['development']['greedy_net_per_decision']>0)
    assert tree.main(args+['--resume'])==0
    with monkeypatch.context() as changed:
        changed.setattr(tree,'sources',lambda:{'changed':True})
        with pytest.raises(ValueError,match='contract'):tree.load_bundle(output,data.source)
    receipt=temporal_root/day/'complete.json'
    receipt.write_text(receipt.read_text()+' ')
    with pytest.raises(ValueError,match='receipt'):scorer.for_session(session)
    with (output/'model.pkl').open('ab') as stream:stream.write(b'corrupt')
    monkeypatch.setattr(tree.pickle,'load',lambda *_:pytest.fail('Deserialized unverified pickle'))
    with pytest.raises(ValueError,match='hash'):tree.load_bundle(output,data.source)


def test_wrapper_routes_exact_loader(monkeypatch):
    from research.rl_trading.v7.experiments import entry_replay,entry_temporal_tree_replay
    def run(argv,*,bundle_loader):
        assert bundle_loader is tree.load_bundle and argv==['--synthetic']
        return 19
    monkeypatch.setattr(entry_replay,'main',run)
    assert entry_temporal_tree_replay.main(['--synthetic'])==19
