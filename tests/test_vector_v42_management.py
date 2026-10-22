import copy
import numpy as np
from research.vectorized_backtest.v42.lineage import outcome_telemetry
from research.vectorized_backtest.v42.config import CROSSOVER

def test_sibling_configuration_matches_v41():
    assert CROSSOVER['children_per_pair']=={'1':.75,'2':.20,'3':.05}
    assert CROSSOVER['reciprocal_siblings'] is True

def test_training_only_outcomes_do_not_change_inputs():
    records=[dict(serial=10,generation=2,crossover={'actions':[{'mode':'mask:union'}]},mutation_fields=[{'field':'parameter:weight'}],parent_objectives=[2.,4.],sibling_count=2,pair_group=7),
             dict(serial=11,generation=2,crossover={'actions':[{'mode':'mask:union'}]},parent_objectives=[2.,4.],sibling_count=2,pair_group=7),
             dict(serial=1,generation=1)]
    values=np.array([5.,-1.,20.]);ordered=[{'net_pnl':[6.,-2.,30.]}];descriptors=[{'behavior':[0.,1.]},{'behavior':[1.,1.]},{'behavior':[9.,9.]}]
    before=copy.deepcopy((records,ordered,descriptors));original=values.copy()
    t=outcome_telemetry(records,values,ordered,{10,1},descriptors,3)
    assert t['operator_outcomes']['mask:union']==dict(evaluated=2,profitable=1,elite_survivors=1,beat_best_parent=1,parent_comparisons=2)
    assert t['operator_outcomes']['parameter:weight']['evaluated']==1
    assert t['sibling_survival']==dict(evaluated=2,elite_survivors=1,behavior_comparisons=1,mean_behavior_distance=.5)
    assert before==(records,ordered,descriptors);np.testing.assert_array_equal(original,values)
