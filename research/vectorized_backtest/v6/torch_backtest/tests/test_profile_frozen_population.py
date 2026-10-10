"""Frozen-prefix profiling preserves strategies despite a different count."""
import json
import numpy as np
import pytest
from research.vectorized_backtest.v6.torch_backtest.evolution import sample
from research.vectorized_backtest.v6.torch_backtest.genome import StrategySpace
from research.vectorized_backtest.v6.torch_backtest.profile_compact_sessions import frozen_profile_population
from research.vectorized_backtest.v6.torch_backtest.training_pass import population_hash

def source(tmp_path):
    members=sample(np.random.default_rng(2236),StrategySpace(),8)
    (tmp_path/'population.json').write_text(json.dumps([m.payload() for m in members]))
    flags=dict(validation_opened=False,optimization_started=False)
    (tmp_path/'identity.json').write_text(json.dumps(dict(population_sha256=population_hash(members),**flags)))
    (tmp_path/'receipt.json').write_text(json.dumps(dict(status='complete',**flags)))
    return members,tmp_path/'population.json'

def test_frozen_prefix_preserves_programs_policies_management_and_identity(tmp_path):
    before,path=source(tmp_path);after,receipt=frozen_profile_population(path,3)
    assert [m.payload() for m in after]==[m.payload() for m in before[:3]]
    assert receipt['source_population_sha256']==population_hash(before)
    assert receipt['selected_indices']==[0,1,2]
    fresh=sample(np.random.default_rng(2236),StrategySpace(),3)
    assert population_hash(after)!=population_hash(fresh)

@pytest.mark.parametrize('name,field,value',[('receipt','status','failed'),('receipt','validation_opened',True),('receipt','optimization_started',True),('identity','validation_opened',True),('identity','optimization_started',True)])
def test_incomplete_search_or_validation_profile_is_rejected(tmp_path,name,field,value):
    _,path=source(tmp_path)
    record=json.loads((tmp_path/(name+'.json')).read_text());record[field]=value
    (tmp_path/(name+'.json')).write_text(json.dumps(record))
    with pytest.raises(ValueError,match='training-only'):frozen_profile_population(path,3)

def test_changed_candidate_and_uncovered_prefix_fail_closed(tmp_path):
    _,path=source(tmp_path)
    with pytest.raises(ValueError,match='coverage'):frozen_profile_population(path,9)
    rows=json.loads(path.read_text());rows[0]['management']['add_fraction']+=.001
    path.write_text(json.dumps(rows))
    with pytest.raises(ValueError,match='identity'):frozen_profile_population(path,3)
