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


@pytest.mark.parametrize('op',['tanh','min','max','product','gate','attention'])
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
    if stage=='entry':return
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
    from research.vectorized_backtest.v39.composition import comparison_base
    new=contract();old=baseline();assert comparison_base(new,old)==old
    root=Path(__file__).resolve().parents[1]/'research/vectorized_backtest'
    for name in ('evaluator.py','protected_transition.py','objectives.py','initial_population.py','species.py'):
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


@pytest.mark.parametrize('index,edge,source',[(0,0,1),(0,1,-2),(1,0,-1),(1,1,0)])
def test_blended_rewire_zero_weight_preserves_recurrent_output_and_size(index,edge,source):
    from research.vectorized_backtest.v39.composition import blended_rewire
    class Zero:
        def uniform(self,*args):return 0.
        def integers(self,*args):return 99
    a=net();child,epsilon=blended_rewire(Zero(),a,index,edge,source)
    assert epsilon==0 and child.size_source==(a.size_source+1 if a.size_source>=index else a.size_source)
    rng=np.random.default_rng(11);x=rng.uniform(-1,1,(1,12,8,INPUTS)).astype(np.float32)
    probe=dict(inputs=x,known=np.ones(x.shape[:-1]+(len(CATALOG),),bool),held_states=np.zeros(x.shape[:-1]+(INPUTS-MARKET_INPUTS,),np.float32))
    # Independent chronological reference verifies remapped recurrence, not
    # merely a payload shape or node-count assertion.
    np.testing.assert_array_equal(observe(a,probe,reference=True)[0],observe(child,probe,reference=True)[0])
    child.validate()


def test_entry_threshold_mutation_is_local_but_retains_full_range_resets():
    from research.vectorized_backtest.v39.composition import parameter
    rng=np.random.default_rng(22);values=np.asarray([parameter(rng,.1,True,'entry') for _ in range(10000)])
    assert np.all(np.abs(values)<1)
    assert .025<np.mean(np.abs(values-.1)>.15)<.065
    assert np.any(values<-.8) and np.any(values>.8)
    assert np.quantile(np.abs(values-.1),.9)<.05


def test_comparison_rejects_unrelated_setting_or_authorized_prior_drift():
    import copy
    from research.vectorized_backtest.v39.config import contract
    from research.vectorized_backtest.v36.config import contract as baseline
    from research.vectorized_backtest.v39.composition import comparison_base
    for modify in (lambda v:v['crossover_v39']['children_per_pair'].update({'1':.5}),
                   lambda v:v['mutation_v39'].update(entry_threshold_sigma=.5)):
        new=copy.deepcopy(contract());modify(new)
        with pytest.raises(ValueError):comparison_base(new,baseline())
