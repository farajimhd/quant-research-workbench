import copy
from dataclasses import replace
import numpy as np
from research.vectorized_backtest.v7.genome import sample
from research.vectorized_backtest.v7.diversity import program_key,summarize,next_population

def test_equivalent_metrics_preserve_distinct_programs_but_limit_elites():
    population=sample(np.random.default_rng(2237),20)
    population[1]=replace(population[0],policy=replace(population[0].policy,reduce_minimum_profit=.222))
    ordered=[dict(net_pnl=[10.,10.]+list(range(30,48)),drawdown=[1.]*20) for _ in range(30)]
    reps,sizes,statistics=summarize(population,ordered,list(range(20)))
    assert program_key(population[0])!=program_key(population[1])
    assert sizes[0]==sizes[1]==2 and 0 in reps and 1 not in reps
    assert statistics['evaluated_candidates']==20
    assert statistics['unique_performance_profiles']<20
    rng=np.random.default_rng(22);state=copy.deepcopy(rng.bit_generator.state)
    first,counts=next_population(rng,population,reps)
    other=np.random.default_rng();other.bit_generator.state=state
    second,_=next_population(other,population,reps)
    assert [v.payload() for v in first]==[v.payload() for v in second]
    assert len(first)==20 and len({program_key(v) for v in first})==20
    assert counts['elites']==2

def test_profile_uses_all_sessions_and_all_metrics_without_rounding():
    population=sample(np.random.default_rng(2237),10)
    ordered=[dict(net_pnl=[0.]*10,capital_dollar_seconds=[0.]*10) for _ in range(30)]
    ordered[-1]['capital_dollar_seconds'][1]=1e-12
    reps,sizes,_=summarize(population,ordered,list(range(10)))
    assert reps==[0,1] and sizes[0]==9 and sizes[1]==1

def test_unreachable_percentage_stop_does_not_create_fake_program_diversity():
    parent=sample(np.random.default_rng(2237),1)[0]
    parent=replace(parent,policy=replace(parent.policy,swing_left=2,swing_right=1))
    child=replace(parent,policy=replace(parent.policy,stop_fraction=.999))
    assert program_key(parent)==program_key(child)
