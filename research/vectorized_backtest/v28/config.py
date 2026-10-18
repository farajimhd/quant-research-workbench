"""Persisted search and compute envelope; independent of symbolic crossover."""
VERSION='v28-independent-tanh-networks-v2'
MAX_NODES=64                 # Bounded GPU capacity, not an initialization size.
MAX_DEPTH=16
FAN_IN=8
INITIAL_SIZES=tuple(range(1,17))+(1,1,20,24,32,48,64)
WEIGHT_BOUND=4.
STATES=('held','profit_fraction','age_hours','capital_entries','stop_distance','adds_fraction','management_age')
ACTION_RATES={'inherit':.4,'exchange':.3,'compose':.3}
PARAMETER_RATE=.7

def contract():
    return dict(version=VERSION,max_nodes_per_action=MAX_NODES,max_depth=MAX_DEPTH,fan_in=FAN_IN,
                max_strategy_connections=5*MAX_NODES*FAN_IN,weight_bound=WEIGHT_BOUND,
                states=list(STATES),action_rates=ACTION_RATES,composition_whole_probability=.5,
                parameter_mutation_probability=PARAMETER_RATE,activation='tanh',
                initial_sizes=list(INITIAL_SIZES),changed_actions_per_mating=[1,2],
                scaling='absolute signed-log plus current-clock cross-sectional z-score; no future statistics',
                input_channels=['absolute','cross_sectional','validity'],
                input_validity='missing values neutral with explicit validity; at least one observed value ancestor required',recurrent=False)
