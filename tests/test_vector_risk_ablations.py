import importlib
import json
from pathlib import Path
import numpy as np
import pytest
import torch
from test_vector_v36_risk import fixture
from test_vector_v32_two_phase import metrics


def module(version,name):return importlib.import_module('research.vectorized_backtest.'+version+'.'+name)


@pytest.mark.parametrize('version,risk,distance',[('v37',300.,.30),('v38',0.,.20)])
def test_only_requested_execution_factor_changed(version,risk,distance):
    actual=module(version,'evaluator').Execution().validate().__dict__
    baseline=module('v35','evaluator').Execution().__dict__
    expected=dict(baseline,maximum_position_risk=risk,maximum_stop_distance=distance)
    assert actual==expected
    root=Path(__file__).resolve().parents[1]/'research/vectorized_backtest'
    for name in ('composition.py','lineage.py','config.py','genome.py','initial_population.py','species.py'):
        assert (root/version/name).read_text()==(root/'v35'/name).read_text().replace('v35',version).replace('V35',version.upper())
    assert "('generations',10)" in (root/version/'run_train.py').read_text()


@pytest.mark.parametrize('version,risk,distance',[('v37',300.,.30),('v38',0.,.20)])
@pytest.mark.parametrize('stop,addition',[(.2,False),(.25,False),(.31,False),(.2,True)])
def test_cpu_risk_arithmetic_exact_baseline(version,risk,distance,stop,addition):
    baseline=module('v34' if version=='v37' else 'v35','evaluator').step
    current=module(version,'evaluator').step
    args,ratios=fixture(stop,addition)
    for a,b in zip(current(*args,True,distance,risk,ratios,0.),baseline(*args,True,distance,risk,ratios,0.)):assert torch.equal(a,b)


@pytest.mark.parametrize('version,expected',[('v37',[True,False,True,False]),('v38',[False,True,False,False])])
def test_eligibility_has_exactly_one_restored_limit(version,expected):
    m=metrics(4);m['maximum_position_risk']=[299.,301.,100.,100.];m['maximum_stop_distance']=[.25,.1,.21,.1];m['terminal_valid']=[True,True,True,False]
    assert module(version,'objectives').local_panel([m])['eligible'].tolist()==[expected]


@pytest.mark.parametrize('version',['v36','v37','v38'])
def test_prepared_initial_json_roundtrip_keeps_exact_genotypes_and_rng(version):
    members=module('v35','genome').sample(np.random.default_rng(6),2)
    previous=json.loads(json.dumps(dict(population=[m.payload() for m in members],rng_before={'state':1},rng_after={'state':2})))
    payload=[dict(m.payload(),version=version+'-recurrent-v1') for m in members]
    gate=module(version,'deferred_launch')
    gate.verify_initial_parity(payload,{'state':1},{'state':2},previous,version)
    with pytest.raises(ValueError):gate.verify_initial_parity(payload,{'state':1},{'state':3},previous,version)
    changed=json.loads(json.dumps(payload));changed[0]['networks']['entry']['threshold']+=.01
    with pytest.raises(ValueError):gate.verify_initial_parity(changed,{'state':1},{'state':2},previous,version)


@pytest.mark.parametrize('version',['v37','v38'])
def test_deferred_queue_audits_seals_and_exact_owner(version,tmp_path,monkeypatch):
    from test_vector_v36_deferred import fixture as completed_predecessor
    job,root,campaign,local=completed_predecessor(tmp_path)
    job['root']=str(tmp_path/'new');job['predecessor']['generations']=50
    gate=module(version,'deferred_launch');monkeypatch.setattr(gate,'live',lambda _:True)
    class Process:
        def __init__(self,pid):pass
        def children(self,recursive=True):return []
    monkeypatch.setattr(gate.psutil,'Process',Process)
    assert gate.inspect_predecessor(job) is None
    monkeypatch.setattr(gate,'live',lambda _:False)
    assert gate.inspect_predecessor(job)['completed']==50
    pointer=gate.read(root/'active-campaign.json');pointer['creation_time']+=1;gate.write(root/'active-campaign.json',pointer)
    with pytest.raises(ValueError,match='exact owner changed'):gate.inspect_predecessor(job)


@pytest.mark.skipif(not torch.cuda.is_available(),reason='CUDA required')
@pytest.mark.parametrize('version,risk,distance',[('v37',300.,.30),('v38',0.,.20)])
@pytest.mark.parametrize('stop,addition',[(.25,False),(.31,False),(.2,True)])
def test_cuda_matches_cpu(version,risk,distance,stop,addition):
    args,ratios=fixture(stop,addition);expected=module(version,'evaluator').step(*args,True,distance,risk,ratios,0.)
    actual=module(version,'protected_transition').transition(*(v.cuda() if isinstance(v,torch.Tensor) else v for v in args),distance,risk,ratios.cuda(),0.)
    for a,b in zip(expected,actual):torch.testing.assert_close(a,b.cpu(),rtol=1e-12,atol=1e-10)
