import pytest
import torch
import numpy as np
from research.vectorized_backtest.v4.torch_backtest.feature_bank import CATALOG,validity
from research.vectorized_backtest.v4.torch_backtest.program import Node,Program,Op,TorchPrograms
from research.vectorized_backtest.v4.torch_backtest.stability import score,Objective
from research.vectorized_backtest.v4.torch_backtest.evolution import sample,mutate
from research.vectorized_backtest.v4.torch_backtest.genome import StrategySpace

def run(nodes,x,output=None,mask=None):
    p=Program(tuple(nodes),len(nodes)-1 if output is None else output)
    return TorchPrograms([p],CATALOG)(x,torch.ones_like(x,dtype=torch.bool) if mask is None else mask)

def test_full_catalog():
    assert len(CATALOG)==149 and len({f.name for f in CATALOG})==149
    assert [f.name for f in CATALOG[147:]]==['split_this_session','reverse_split_this_session']
    x=torch.ones(4,len(CATALOG));x[:,20]=0
    mask=validity(x)
    assert not mask[:,14:20].any() and mask[:,20].all()

def test_generic_admission_uses_completed_prices_without_fixed_signal():
    from research.vectorized_backtest.v4.torch_backtest.encoding.clickhouse import admission_sql
    from research.vectorized_backtest.v4.torch_backtest.encoding.config import Funnel
    source={'build_id':'producer','units':{'2026-08-03':{'ABC':{'bars':{'attempt_id':'attempt'}}}}}
    sql=admission_sql(source,'2026-08-03',['ABC'],Funnel(admission='price_envelope'),72000000000,57600000000)
    assert 'resolution_ms=1000' in sql and 'price_valid=1' in sql
    assert '(toInt64(bucket_index)+1)*1000000>=57600000000' in sql
    assert '(toInt64(bucket_index)+1)*1000000<=72000000000' in sql
    assert 'min((toInt64(bucket_index)+1)*1000000)' in sql
    assert 'lagInFrame' not in sql and 'arrayFold' not in sql and 'volume>' not in sql

def test_feature_compare_and_completed_mean():
    x=torch.zeros(6,147);x[:,8]=torch.arange(6.)
    nodes=[Node(Op.FEATURE,feature=8),Node(Op.MEAN,a=0,window=3),Node(Op.GREATER,a=0,b=1)]
    v,m=run(nodes,x);assert torch.equal(v[0,2:],torch.ones(4)) and not m[0,:2].any()
    changed=x.clone();changed[4:,8]=1000
    z,_=run(nodes,changed);assert torch.equal(v[:,:4],z[:,:4])

def test_missing_not_or_fail_closed():
    x=torch.ones(3,147);m=torch.ones_like(x,dtype=torch.bool);m[:,23]=False
    nodes=[Node(Op.FEATURE,feature=23),Node(Op.NOT,a=0),Node(Op.CONSTANT,value=1,unit='bool'),Node(Op.OR,a=1,b=2)]
    _,known=run(nodes,x,mask=m);assert not known.any()

def test_types_cycles_and_context():
    with pytest.raises(ValueError):Program((Node(Op.FEATURE,feature=0),Node(Op.FEATURE,feature=8),Node(Op.GREATER,a=0,b=1)),2).validate(CATALOG)
    with pytest.raises(ValueError):Program((Node(Op.NOT,a=0),),0).validate(CATALOG)
    with pytest.raises(ValueError):Program((Node(Op.FEATURE,feature=8),Node(Op.MEAN,a=0,window=120),Node(Op.LAG,a=1,window=1),Node(Op.CONSTANT,value=0,unit='log_shares'),Node(Op.GREATER,a=2,b=3)),4).validate(CATALOG)

def test_stability_rejects_one_winner_and_arithmetic():
    pnl=torch.tensor([[28000.,100.],[-6000.,100.],[-5000.,100.]],dtype=torch.float64)
    zero=torch.zeros_like(pnl);r=score(pnl,zero,zero,zero,torch.ones_like(pnl),torch.ones_like(pnl,dtype=torch.bool),torch.tensor([4.,4.]))
    assert not r['feasible'][0] and r['feasible'][1]
    components=r['components'];expected=components['median_reward']+components['ex_best_reward']-sum(v for k,v in components.items() if k.endswith('penalty'))
    torch.testing.assert_close(r['score'],expected)

def test_seeded_variable_length_mutation():
    a=np.random.default_rng(17);b=np.random.default_rng(17);space=StrategySpace()
    x=sample(a,space,4);y=sample(b,space,4)
    for _ in range(20):x=[mutate(a,v,space) for v in x];y=[mutate(b,v,space) for v in y]
    assert [v.payload() for v in x]==[v.payload() for v in y]
    assert len({len(p.nodes) for v in x for p in v.programs().values()})>1

def test_sharpe_reporting_only():
    from research.vectorized_backtest.v4.torch_backtest.metrics import sharpe
    r=torch.tensor([[.01],[.02],[-.01]],dtype=torch.float64)
    torch.testing.assert_close(sharpe(r,annualization=1),r.mean(0)/r.std(0,correction=1))
    assert torch.isnan(sharpe(torch.ones(3,1))).all()
    assert torch.isnan(sharpe(torch.ones(1,1))).all()

def test_financial_program_path_causal_and_timestamp_duration():
    from research.vectorized_backtest.v4.torch_backtest.fixtures import synthetic_tape
    from research.vectorized_backtest.v4.torch_backtest.program_runner import ProgramRunner
    from research.vectorized_backtest.v4.torch_backtest.evolution import STAGES,Individual
    tape=synthetic_tape(seconds=45,listings=1);space=StrategySpace()
    policy=space.default.copy();policy[4]=1;policy[10]=.05;policy[11]=0;policy[12]=0
    programs={s:[([Node(Op.CONSTANT,value=1 if s in ('entry','trail') else 0,unit='bool')],0)] for s in STAGES}
    candidate=Individual(policy.tolist(),programs,{s:[] for s in STAGES})
    gates={s:torch.ones((45,1,1),dtype=torch.bool) if s in ('entry','trail') else torch.zeros((45,1,1),dtype=torch.bool) for s in STAGES}
    gates['exit'][15:]=True
    runner=ProgramRunner(tape,space,[candidate],gates,backend='eager',maximum_fills=128).compile()
    result=runner.run()
    assert result['filled_batches'][0]>0 and result['terminal_valid'][0]
    ledger=runner.ledger[0,:int(runner.fill_count[0])]
    assert len(ledger)>1
    # Ledger columns: timestamp, ticker, position, side, quantity, price, fees,
    # kind, cash. New signal at t=8 cannot consume the signal interval.
    assert ledger[0,0]>8
    assert result['sold_share_seconds'][0]>0
    shifted=synthetic_tape(seconds=45,listings=1)
    from dataclasses import replace
    shifted=replace(shifted,clocks=shifted.clocks+1000,admission=shifted.admission+1000,level_from=shifted.level_from+1000,level_to=shifted.level_to+1000,provenance={**shifted.provenance,'start_second':1001,'end_second':1045})
    second=ProgramRunner(shifted,space,[candidate],gates,backend='eager',maximum_fills=128).compile().run()
    for name in ('net_pnl','sold_share_seconds','stop_risk_dollar_seconds','capital_dollar_seconds'):
        torch.testing.assert_close(result[name],second[name])

def test_gate_compiler_sparse_clock_has_no_same_or_future_fill():
    from research.vectorized_backtest.v4.torch_backtest.gate_compiler import FeatureResident
    from research.vectorized_backtest.v4.torch_backtest.fixtures import synthetic_tape
    from research.vectorized_backtest.v4.torch_backtest.evolution import Individual,STAGES
    class Bank:
        def listing(self,identity,previous=None):
            x=torch.ones(3,147);x[:,23]=torch.tensor([0.,1.,0.])
            return torch.tensor([8,12,17])*1_000_000,x,torch.ones_like(x,dtype=torch.bool)
    space=StrategySpace();clauses={s:[([Node(Op.FEATURE,feature=23)],0)] for s in STAGES}
    individual=Individual(space.default.tolist(),clauses,{s:[] for s in STAGES})
    tape=synthetic_tape(seconds=30,listings=1)
    resident=FeatureResident(Bank(),['x'],device='cpu');gates,_=resident.compile([individual],tape,chunk_candles=120,packed=False)
    assert torch.nonzero(gates['entry'][:,0,0]).flatten().tolist()==[11]
    # The t=12 signal is unavailable at t=8, and absent seconds do not create
    # repeated observations or turn the future candle into current evidence.
    assert not gates['entry'][:11].any() and not gates['entry'][12:].any()
    packed,_=resident.compile([individual],tape,chunk_candles=120)
    for stage in STAGES:assert torch.equal((packed&(1<<STAGES.index(stage)))!=0,gates[stage])

def test_dashboard_views_fit_compact_and_redirected():
    from io import StringIO
    from rich.console import Console
    from research.vectorized_backtest.v4.torch_backtest.dashboard import render
    status=dict(status='training',stage='Backtest',config=dict(population=128,generations=32,training_sessions=30),completed_generations=8,completed_sessions=12,progress=dict(completed_seconds=200,total_seconds=900))
    for view in ('financial','performance','objective'):
        stream=StringIO();console=Console(file=stream,width=80,height=24,force_terminal=False)
        console.print(render(status,width=80,height=24,view=view))
        lines=stream.getvalue().splitlines()
        assert len(lines)<=24 and all(len(line)<=80 for line in lines)
        assert '\x1b[' not in stream.getvalue()

def test_controller_all30_freeze_once_and_audit(tmp_path,monkeypatch):
    """Controller I/O witness with injected deterministic replay receipts.

    This is not historical or GPU qualification; the real financial tick path
    is exercised separately above. Tests exact coverage/freeze/evaluation order.
    """
    import json
    from datetime import date,timedelta
    from research.vectorized_backtest.v4.torch_backtest import run_search
    from research.vectorized_backtest.v4.torch_backtest.runtime import code_hash,file_hash
    from research.vectorized_backtest.v4.torch_backtest.audit import audit
    inputs=tmp_path/'inputs';inputs.mkdir();mapping=inputs/'map.json';mapping.write_text('{}')
    sessions={role:[dict(day=str(date(2026,1,1)+timedelta(days=offset+i)),execution_root=str(inputs),feature_root=str(inputs),identity_map=str(mapping),split_certificate=str(mapping)) for i in range(count)] for role,offset,count in [('training',0,30),('validation',30,6)]}
    path=tmp_path/'sessions.json';path.write_text(json.dumps(sessions));qualification=tmp_path/'unit-only-qualification.json'
    qualification.write_text(json.dumps(dict(status='passed',code_hash=code_hash(),population=4,sessions_sha256=file_hash(path))))
    calls=[];output=tmp_path/'experiment'
    def replay(spec,population,space,args,folder,emit,cache=None):
        calls.append(spec['day']);lanes=len(population)
        if len(calls)>30:assert (output/'frozen_winner.json').exists()
        ledger=folder/'fills.pt';torch.save(dict(ledger=torch.zeros(lanes,2,9),counts=torch.ones(lanes,dtype=torch.int64)*2),ledger)
        metrics={name:[value]*lanes for name,value in dict(net_pnl=100.,drawdown=20.,stop_risk_dollar_seconds=100.,capital_dollar_seconds=1000.,filled_batches=1,terminal_valid=True,positions_opened=1,fill_count=2,open_positions=0,sold_share_seconds=20.,sold_shares=2).items()}
        return dict(day=spec['day'],metrics=metrics,timing={},ledger_sha256=file_hash(ledger),population_sha256=run_search.fingerprint([run_search.state(v) for v in population]))
    monkeypatch.setattr(run_search,'evaluate_session',replay)
    assert run_search.main(['--sessions',str(path),'--output',str(output),'--execute','--device','cpu','--backend','eager','--population','4','--generations','1','--qualification',str(qualification)])==0
    assert calls==[s['day'] for s in sessions['training']+sessions['validation']]
    assert audit(output)['full_budget_verified']
    assert not (output/'owner.lock').exists()
    with pytest.raises(ValueError,match='immutable'):
        run_search.main(['--sessions',str(path),'--output',str(output),'--execute','--resume','--device','cpu','--backend','eager','--population','4','--generations','1','--qualification',str(qualification)])
