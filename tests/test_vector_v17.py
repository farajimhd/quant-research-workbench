"""Independent cost oracle, semantic safety, effective variation and identity."""
from dataclasses import replace
import numpy as np
import pytest
import torch
from research.vectorized_backtest.v17.objectives import Objective,score,session_scores,positive_trim
from research.vectorized_backtest.v17.genome import Individual,Policy,STAGES,assemble,sample
from research.vectorized_backtest.v17.program import Program,Node,Op,TorchPrograms
from research.vectorized_backtest.v17.features import CATALOG
from research.vectorized_backtest.v17.semantics import SemanticError,analyze
from research.vectorized_backtest.v17.lineage import program_key
from research.vectorized_backtest.v17.variation import fields,mutate_fields
from research.vectorized_backtest.v17.complexity import simplify
from test_vector_v17_regressions import metrics


@pytest.mark.parametrize('encoding',['utf-8','utf-8-sig','utf-16','utf-32'])
def test_launcher_reads_legacy_pointer_encodings_and_rejects_invalid_json(tmp_path,encoding):
    import json
    from research.vectorized_backtest.v17.run_train import read_json
    path=tmp_path/'pointer.json';value={'pid':123,'creation_time':17.0}
    path.write_bytes(json.dumps(value).encode(encoding));assert read_json(path)==value
    path.write_bytes(b'not a JSON record')
    with pytest.raises(json.JSONDecodeError):read_json(path)


def trimmed(x,f=.1):
    values=sorted(x,reverse=True);mass=f*len(values)
    return sum(values)-sum(max(0,v)*max(0,min(1,mass-i)) for i,v in enumerate(values))


def test_costs_match_scalar_oracle_with_losses_empty_and_fractional_boundaries():
    rng=np.random.default_rng(17);profits=[[rng.normal(0,100,int(rng.integers(0,15))).tolist() for _ in range(8)] for _ in range(7)]
    profits[0][0]=[-20,-10];profits[1][0]=[];profits[2][0]=[1000]
    ordered=[metrics(p) for p in profits]
    for m in ordered:
        for key in ('drawdown','stop_risk_dollar_seconds','capital_dollar_seconds'):m[key]=rng.uniform(0,10000,8).tolist()
    result=score(ordered,[0]*8);local,eligible=session_scores(ordered,prepared=result)
    for i in range(8):
        pnl=[sum(p[i]) for p in profits];tp=[trimmed(p[i]) for p in profits]
        risk=[.05*m['drawdown'][i]+.005*m['stop_risk_dollar_seconds'][i]/3600+.0004*m['capital_dollar_seconds'][i]/3600 for m in ordered]
        sorted_pnl=sorted(pnl);mass=.2*len(pnl);tail=sum(v*max(0,min(1,mass-j)) for j,v in enumerate(sorted_pnl))/mass
        expected=trimmed(pnl)+.1*len(pnl)*tail-sum(risk)
        assert result['score'][i]==pytest.approx(expected)
        for s in range(7):
            other=sum(tp[u] for u in range(7) if u!=s)/6
            assert local[s,i]==pytest.approx(tp[s]-risk[s]-.25*max(0,-other))
            assert eligible[s,i]==bool(profits[s][i])
    assert result['_local']['trimmed'][0,0]==-30
    assert result['_local']['trimmed'][2,0]==900


def test_one_session_and_inactive_sessions_have_defined_costs():
    values,eligible=session_scores([metrics([[100],[],[-30]])])
    np.testing.assert_array_equal(values,[[90,0,-30]])
    np.testing.assert_array_equal(eligible,[[True,False,True]])
    result=score([metrics([[100],[],[-30]])],[1,1,1])
    np.testing.assert_allclose(result['score'],[100,0,-33])
    np.testing.assert_array_equal(result['feasible'],[True,False,True])


def entry():
    i=next(i for i,f in enumerate(CATALOG) if f.unit=='ratio')
    return Program((Node(Op.FEATURE,feature=i),Node(Op.CONSTANT,value=.1),Node(Op.GREATER,a=0,b=1)),2)


def member():
    never=Program((Node(Op.CONSTANT,unit='bool'),),0)
    return assemble(dict(entry=entry(),**{s:never for s in STAGES if s!='entry'}),Policy(),{})


@pytest.mark.parametrize('op',[Op.GREATER,Op.LESS,Op.GREATER_EQUAL,Op.LESS_EQUAL,Op.CROSS_ABOVE,Op.CROSS_BELOW])
def test_self_comparisons_rejected_after_operand_rewiring(op):
    p=entry();bad=Program(p.nodes[:2]+(Node(op,a=0,b=0),),2)
    m=member();rules=dict(m.rules);rules['entry']=bad
    with pytest.raises(SemanticError,match='self_comparison'):replace(m,rules=rules).validate()


def test_inactive_entry_rejected_explicit_disabled_management_retained():
    m=member();m.validate();rules=dict(m.rules);rules['entry']=m.open_rules['exit']
    with pytest.raises(SemanticError,match='inactive_entry'):replace(m,rules=rules).validate()


def test_dead_metadata_simplification_preserves_values_and_missing_masks():
    p=entry();p=Program(p.nodes+(Node(Op.CONSTANT,value=77,unit='price'),),p.output)
    canonical=simplify(p);assert len(canonical.nodes)==3
    x=torch.randn(2,9,len(CATALOG));known=torch.ones_like(x,dtype=torch.bool);known[:,::2]=False
    left,lv=TorchPrograms([p],CATALOG)(x,known);right,rv=TorchPrograms([canonical],CATALOG)(x,known)
    assert torch.equal(left,right) and torch.equal(lv,rv)


def test_identity_and_mutation_ignore_only_execution_irrelevant_fields():
    m=member();m=replace(m,policy=replace(m.policy,swing_left=20,swing_right=10))
    ignored=replace(m,policy=replace(m.policy,stop_fraction=.8,trail_fraction=.8,add_minimum_profit=.8),minimum_age={'exit':100})
    assert program_key(m)==program_key(ignored)
    assert program_key(m)!=program_key(replace(m,policy=replace(m.policy,target_fraction=.8)))
    choices=fields(m)
    assert ('policy',-1,'stop_fraction') not in choices
    assert ('policy',-1,'trail_fraction') not in choices
    assert ('age',-1,'exit') not in choices
    for seed in range(20):
        child,edits=mutate_fields(np.random.default_rng(seed),m,5)
        assert len(edits)==5 and len({(e['stage'],e['node'],e['field']) for e in edits})==5
        assert program_key(child)!=program_key(m)


def test_validity_dependencies_of_always_true_formulas_are_not_erased():
    p=entry();false=Program((Node(Op.CONSTANT,unit='bool'),),0)
    a=Program(p.nodes+(Node(Op.NOT,a=p.output),Node(Op.OR,a=p.output,b=3)),4)
    # The conservative analyzer leaves x OR NOT x data-dependent; its unknown
    # mask is preserved instead of replacing it with unconditional TRUE.
    assert analyze(a)['constant'] is None
    assert simplify(a).nodes[-1].op==Op.OR


def test_production_initial_population_has_no_accidental_constant_signals():
    p=sample(np.random.default_rng(172237),1024)
    for m in p:
        for stage,rule in __import__('research.vectorized_backtest.v17.genome',fromlist=['effective_rules']).effective_rules(m).items():
            info=analyze(rule)
            assert not info['self_comparisons']
            assert info['constant'] is None or stage!='entry' and info['explicit_constant']


@pytest.mark.parametrize('device',['cpu',pytest.param('cuda',marks=pytest.mark.skipif(not torch.cuda.is_available(),reason='Workstation CUDA trace gate'))])
def test_optional_position_trace_preserves_financial_accounting(device):
    from research.vectorized_backtest.v7.qualify import synthetic,member as baseline_member
    from research.vectorized_backtest.v17.evaluator import replay_cohort,Execution
    gates=torch.tensor([1,4,8,1,2,0],dtype=torch.uint8,device=device)[None,:,None]
    members=[baseline_member()];execution=Execution(fee_per_share=.005,minimum_order_fee=1)
    backend='compile' if device=='cuda' else 'eager'
    baseline=replay_cohort([synthetic(device)],members,[gates],execution=execution,backend=backend)[0]
    traced=replay_cohort([synthetic(device)],members,[gates],execution=execution,backend=backend,trace=True)[0]
    for key in baseline:
        if isinstance(baseline[key],torch.Tensor):assert torch.equal(baseline[key],traced[key])
        else:assert baseline[key]==traced[key]
    assert traced['episode_entry_clocks'][0] and traced['episode_exit_clocks'][0]
    assert all(b<e for b,e in zip(traced['episode_entry_clocks'][0],traced['episode_exit_clocks'][0]))
