import json
from dataclasses import replace
from pathlib import Path
import numpy as np
import pytest
from research.vectorized_backtest.v39.genome import Network,Neuron,INPUTS,MARKET_INPUTS,STAGES
from research.vectorized_backtest.v39.composition import compose
from research.vectorized_backtest.v39.calibration import observe
from research.vectorized_backtest.v39.features import CATALOG


def net(uid=1):
    return Network((Neuron((0,-1),(.8,.1),0.,uid),Neuron((INPUTS,1),(.9,.2),.1,uid+1)),.17,0,.7,.1).validate()


@pytest.mark.parametrize('op',['product','gate'])
@pytest.mark.parametrize('whole',[False,True])
def test_entry_residual_preserves_primary_graph_sizing_and_margin_bound(op,whole):
    a,b=net(),net(10);child=compose(np.random.default_rng(6),a,b,whole,op,'entry')
    assert child.nodes[:len(a.nodes)]==a.nodes
    assert (child.size_source,child.size_weight,child.size_bias)==(a.size_source,a.size_weight,a.size_bias)
    assert child.threshold==0 and abs(child.nodes[-1].weights[1])<=.1
    rng=np.random.default_rng(8);x=rng.uniform(-.9,.9,(1,8,32,INPUTS)).astype(np.float32)
    p=dict(inputs=x,known=np.ones(x.shape[:-1]+(len(CATALOG),),bool),held_states=np.zeros(x.shape[:-1]+(INPUTS-MARKET_INPUTS,),np.float32),eligible=np.ones(x.shape[:-1],bool))
    parent,valid,_,_=observe(a,p,reference=True);out,_,_,_=observe(child,p,reference=True)
    margin=parent-a.threshold;safe=np.abs(margin)>.10001
    np.testing.assert_array_equal((out>0)[safe],(margin>0)[safe])
    assert np.max(np.abs(np.arctanh(out)-margin))<=.10001
    # This is a birth prior, not a new evolvable-weight restriction.
    changed=replace(child,nodes=child.nodes[:-1]+(replace(child.nodes[-1],weights=(1.,2.)),))
    changed.validate()


@pytest.mark.parametrize('op',['product','gate'])
def test_whole_negative_product_or_gate_cannot_reverse_both_no_margins(op):
    a=Network((Neuron((0,),(1.,),0.,1,'identity'),))
    b=Network((Neuron((1,),(1.,),0.,2,'identity'),))
    x=np.zeros((1,1,4,INPUTS),np.float32);x[0,0,:,0]=[-.9,-.4,-.01,.5];x[0,0,:,1]=[-.9,-.4,-.01,.5]
    p=dict(inputs=x,known=np.ones(x.shape[:-1]+(len(CATALOG),),bool),held_states=np.zeros(x.shape[:-1]+(INPUTS-MARKET_INPUTS,),np.float32))
    for seed in range(20):
        child=compose(np.random.default_rng(seed),a,b,True,op,'entry');values,_,_,_=observe(child,p,reference=True)
        assert (values>0).ravel().tolist()==[False,False,False,True]


@pytest.mark.parametrize('stage',STAGES)
@pytest.mark.parametrize('op',['tanh','min','max','product','gate','attention'])
def test_other_operator_paths_and_rng_are_exact_v36(stage,op):
    if stage=='entry' and op in ('product','gate'):return
    from research.vectorized_backtest.v36.genome import Network as OldNetwork
    from research.vectorized_backtest.v36.composition import compose as old_compose
    a,b=net(),net(10);old_a,old_b=OldNetwork.restore(a.payload()),OldNetwork.restore(b.payload())
    for whole in (False,True):
        x,y=np.random.default_rng(21),np.random.default_rng(21)
        new=compose(x,a,b,whole,op,stage);old=old_compose(y,old_a,old_b,whole,op,stage)
        assert new.payload()==old.payload() and x.bit_generator.state==y.bit_generator.state


def test_only_intended_initializer_settings_differ_from_v36():
    from research.vectorized_backtest.v39.config import contract
    from research.vectorized_backtest.v36.config import contract as baseline
    new=contract();old=baseline();new['version']=old['version'];settings=dict(new.pop('crossover_v39'));settings.pop('entry_residual')
    new['crossover_v36']=settings
    assert new==old
    root=Path(__file__).resolve().parents[1]/'research/vectorized_backtest'
    for name in ('evaluator.py','protected_transition.py','objectives.py','lineage.py','initial_population.py','species.py'):
        assert (root/'v39'/name).read_text()==(root/'v36'/name).read_text().replace('v36','v39').replace('V36','V39')


def test_launch_contract_check_does_not_mutate_live_crossover_settings(tmp_path,monkeypatch):
    import copy
    from dataclasses import asdict
    from types import SimpleNamespace
    from research.vectorized_backtest.v39 import deferred_launch as gate,run_search
    from research.vectorized_backtest.v39.config import CROSSOVER
    from research.vectorized_backtest.v39.evaluator import Execution
    from research.vectorized_backtest.v39.lineage import Selection
    from research.vectorized_backtest.v39.objectives import Objective
    from research.vectorized_backtest.v36.composition import settings
    baseline=dict(execution=asdict(Execution(fee_per_share=.005,minimum_order_fee=1)),selection=asdict(Selection()),objective=asdict(Objective()),
        recombination=settings(Selection()),population=1024,seed=2020,generations=50,initial_multiplier=6,day='2026-08-04')
    job=dict(source_sha256='source',training='training',training_sha256='hash',tests_result='tests',tests_result_sha256='hash',baseline=str(tmp_path),execution=baseline['execution'],campaign=str(tmp_path/'missing'))
    monkeypatch.setattr(run_search,'source_hash',lambda:'source');monkeypatch.setattr(gate,'file_hash',lambda p:'hash')
    monkeypatch.setattr(gate,'read',lambda p:dict(contract=baseline) if str(p).endswith('checkpoint.json') else dict(exit_code=0,source_sha256='source',output='log',output_sha256='hash'))
    monkeypatch.setattr(gate.psutil,'virtual_memory',lambda:SimpleNamespace(available=1024*2**30))
    before=copy.deepcopy(CROSSOVER);gate.check_job(job);assert CROSSOVER==before and 'entry_residual' in CROSSOVER
