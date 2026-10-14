"""Independent profitability gates and unchanged replay boundary."""
import numpy as np
from dataclasses import replace
from research.vectorized_backtest.v19.objectives import score,session_scores,Objective
from research.vectorized_backtest.v18.objectives import score as prior_score
from test_vector_v17_regressions import metrics

def test_global_reward_and_separate_global_local_eligibility():
    # Consistent profit, concentrated outlier, nearly idle, and local specialist.
    profits=[[[10.,10.,10.],([1000.]*3 if d==0 else [-1.]*3),([.001] if d==0 else []),([100.]*3 if d==0 else [])] for d in range(24)]
    ordered=[metrics(p) for p in profits]
    result=score(ordered,[0]*4);old=prior_score(ordered,[0]*4)
    alltrades=[np.concatenate([np.asarray(p[i]) for p in profits]) for i in range(4)]
    from research.vectorized_backtest.v18.objectives import episode_statistics
    trimmed=episode_statistics(alltrades,Objective())['trimmed_profit']
    np.testing.assert_allclose(result['score'],old['score']+.1*trimmed)
    assert result['global_eligible'].tolist()==[True,False,False,False]
    values,eligible=session_scores(ordered,prepared=result)
    assert eligible[0].tolist()==[True,True,False,True]
    assert not eligible[1:,3].any()
    np.testing.assert_allclose(values,old['_local']['values'])

def test_near_zero_and_loss_cannot_be_global_or_local_parents():
    ordered=[metrics([[0.,0.,0.],[-1.,-1.,-1.]]) for _ in range(24)]
    result=score(ordered,[0,0]);assert not result['global_eligible'].any()
    assert not session_scores(ordered,prepared=result)[1].any()

def test_empty_parent_pools_supply_fresh_population_without_relaxing_gates():
    from research.vectorized_backtest.v19.genome import sample
    from research.vectorized_backtest.v19.lineage import Pedigree,next_population,Selection
    rng=np.random.default_rng(19);members=sample(rng,64);pedigree=Pedigree();ids=pedigree.birth(members,1,[(-1,-1)]*64,["fresh"]*64)
    children,newids,report=next_population(rng,members,ids,pedigree,list(range(64)),2,session_values=np.zeros((24,64)),session_eligible=np.zeros((24,64),bool),global_values=np.zeros(64),global_eligible=np.zeros(64,bool))
    assert len(children)==64 and report['fresh_candidates']==64
    assert report['global_elites']==report['local_champions']==0
