import importlib.util
import json
from pathlib import Path
from dataclasses import replace
from types import SimpleNamespace
import numpy as np
import pytest
from research.rl_trading.v7.experiments import entry_fast_replay as fast

spec=importlib.util.spec_from_file_location('replay_fixture',Path(__file__).with_name('test_rl_trading_v7_entry_replay.py'))
fixture=importlib.util.module_from_spec(spec);spec.loader.exec_module(fixture)


@pytest.mark.parametrize('mode',['wait','active','sticky','terminal'])
def test_exact_replay_and_environment_state(mode,monkeypatch):
    session=fixture.session();cfg=replace(fast.original.Config(),max_hold_seconds=2)
    value=-1. if mode=='wait' else 1.
    if mode=='sticky':
        session.execution['bid_size'][:]=100.
        session.execution['quote_us'][:,0]=(np.arange(len(session.clocks))//7)*7_000000
        session.execution['quote_valid'][::13]=False
        session.execution['mark'][:,0]=10+np.sin(np.arange(len(session.clocks))*.1)*.2
    if mode=='terminal':
        cfg=replace(cfg,max_hold_seconds=10000)
        session.execution['bid_size'][:]=10.
    environments=[]
    base=fast.original.CostEnvironment
    class Captured(base):
        def __init__(self,*a,**kw):
            super().__init__(*a,**kw);self.calls=0;environments.append(self)
        def step(self,action):self.calls+=1;return super().step(action)
    monkeypatch.setattr(fast.original,'CostEnvironment',Captured)
    full_scorer=fixture.Scorer(value);fast_scorer=fixture.Scorer(value)
    full=fast.original.replay_session(session,full_scorer,cfg)
    accelerated=fast.replay_session(session,fast_scorer,cfg)
    assert accelerated==full
    a,b=environments
    for key in ('cash','used','peak','drawdown','last_fills','t','quantity','pending_exit','basis','entry','ticker'):
        assert getattr(a,key)==getattr(b,key)
    assert b.calls<a.calls
    for x,y in zip(full_scorer.observations,fast_scorer.observations):
        for key in x:np.testing.assert_array_equal(x[key],y[key])
    if mode=='sticky':
        assert full['max_drawdown']>0
        assert sum(f['side']==-1 for f in full['fill_records'])>full['exits']
    assert full['valid_terminal']


def test_scoped_cli_actual_main_binding_resume_and_restore(tmp_path,monkeypatch):
    session=fixture.session();data=SimpleNamespace(days=[session.day],binding={'synthetic':True},load=lambda _:session)
    monkeypatch.setattr(fast.original,'TrainingDataset',lambda _:data)
    monkeypatch.setattr(fast,'loader',lambda _:lambda *a,**kw:fixture.Scorer(-1.))
    original=fast.original.replay_session
    root=tmp_path/'fast'
    args=['--recipe','tree','--bundle','unused','--forecast','unused','--output',str(root)]
    assert fast.main(args)==0 and fast.original.replay_session is original
    assert fast.main(args)==0 and fast.original.replay_session is original
    manifest=json.loads((root/'manifest.json').read_text())
    acceleration=manifest['contract']['scorer']['acceleration']
    assert acceleration['source']==fast.original.sha(Path(fast.__file__))
    assert acceleration['rule']==fast.RULE
    with pytest.raises(ValueError,match='TRAINING'):fast.main(args+['--dates','2030-01-01'])
    assert fast.original.replay_session is original
