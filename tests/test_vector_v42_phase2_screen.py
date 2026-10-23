import copy
import numpy as np
import pytest
from research.vectorized_backtest.v42.phase2_screen import historical_experts,survivor_indices,choose


def records(day):
    return [dict(day=day,generation=g,serial=g*100+i,score=float(100-i),net_profit=float(i+1),canonical_sha256=f'{day}-{g}-{i}')
            for g in range(1,101) for i in range(10)]


def test_temporal_mix_reproducible_unique_and_rng_independent():
    pools={str(d):records(str(d)) for d in range(24)}
    archives={d:sorted(r,key=lambda x:-x['net_profit'])[:64] for d,r in pools.items()}
    global_before=copy.deepcopy(np.random.get_state())
    a,report=historical_experts(pools,archives,2020);b,again=historical_experts(pools,archives,2020)
    assert a==b and report==again and len(a)==2304
    assert len({r['canonical_sha256'] for r in a})==2304
    assert all(r['objective']==8 and r['sampled']==8 for r in report['bands'] if 'range' in r)
    after=np.random.get_state();assert np.array_equal(global_before[1],after[1]) and global_before[2:]==after[2:]
    assert report['unfilled']==0


def test_shortfall_is_explicit_and_duplicates_never_fill_slots():
    row=records('a')[0];pools={'a':[row]*100,'b':[dict(row,day='b')]}
    selected,report=historical_experts(pools,pools,2020)
    assert len(selected)==1 and report['unfilled']==191
    assert choose([row],0,set(),np.random.default_rng(1))==[]


def test_screen_selects_objective_and_eligibility_with_stable_ties():
    assert survivor_indices([3,100,5,5],[True,False,True,True],2)==[2,3]
    with pytest.raises(ValueError,match='Insufficient'):survivor_indices([3],[False],1)
    with pytest.raises(ValueError,match='Invalid'):survivor_indices([float('nan')],[True],1)


def test_screen_resume_reuses_survivor_receipts_and_rejects_tamper(tmp_path,monkeypatch):
    import json
    from pathlib import Path
    from research.vectorized_backtest.v42 import phase2_screen as m,run_search as rs,genome,complexity,terminal,distributed_lanes
    from research.vectorized_backtest.v42 import objectives
    from research.vectorized_backtest.v42.publication import write_json
    days=['2026-01-01','2026-01-02'];data=dict(days=days,inputs='i',history='h',indicator_root='n',enriched_root='e',receipts={d:{} for d in days})
    monkeypatch.setattr(rs,'bind_dataset',lambda *a:data);monkeypatch.setattr(rs,'source_hash',lambda:'source')
    class Member:
        def __init__(self,p):self.p=p
        def payload(self):return self.p
    monkeypatch.setattr(genome.Individual,'restore',lambda p:Member(p));monkeypatch.setattr(complexity,'count',lambda m:1)
    monkeypatch.setattr(objectives,'score',lambda ordered,*a:dict(score=np.sum([x['net_pnl'] for x in ordered],axis=0),feasible=np.array([True]*len(ordered[0]['net_pnl']))))
    class Panel:
        def __init__(self,*a):pass
        def __enter__(self):return self
        def __exit__(self,*a):pass
        def begin_generation(self,*a):pass
        def begin_evaluation(self,*a):pass
        def update(self,**k):pass
    monkeypatch.setattr(terminal,'CampaignProgress',Panel);calls=[]
    class Lanes:
        def __init__(self,cfg,*a):self.cfg=cfg
        def __enter__(self):return self
        def __exit__(self,*a):pass
        def release_plans(self):pass
        def generation(self,days,members,g,token):
            calls.append(token)
            for d in days:
                values=[m.p['value'] for m in members]
                write_json(Path(self.cfg['output'])/'generation-0001'/d/'receipt.json',dict(population_sha256=token,full_session=True,split='training',validation_opened=False,execution=self.cfg['execution'],identity={'day':d},metrics={'net_pnl':values,'episodes':[[v,v+1] for v in values]}))
    monkeypatch.setattr(distributed_lanes,'DistributedLanes',Lanes)
    payload=[{'value':i} for i in range(6)];pool=dict(population=payload,sources=[None]*6,pool_sha256='wide')
    kwargs=dict(population_size=2,batch_size=2,session_workers=1,capture_gib=1,host_reserve_gib=12,host_admission_gib=8,distributed_job='job')
    selected=m.screen(pool,'training',tmp_path,**kwargs)
    assert [p['value'] for p in selected['population']]==[5,4]
    r=json.loads((tmp_path/'training/generation-0001'/days[0]/'receipt.json').read_text())
    assert r['metrics']=={'net_pnl':[5,4],'episodes':[[5,6],[4,5]]} and r['evaluated_candidates']==0
    assert m.screen(pool,'training',tmp_path,**kwargs)==selected and len(calls)==3
    p=tmp_path/'initial-screen/cohort-00/training/generation-0001'/days[0]/'receipt.json';p.write_text('{}')
    with pytest.raises(ValueError,match='receipt changed'):m.screen(pool,'training',tmp_path,**kwargs)
