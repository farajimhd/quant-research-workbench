"""Persisted search and compute envelope; independent of symbolic crossover."""
VERSION='v28-independent-tanh-networks-v1'
MAX_NODES=16                 # Per action, including its output neuron.
MAX_DEPTH=6
FAN_IN=4
WEIGHT_BOUND=4.
STATES=('held','profit_fraction','age_hours','capital_entries','stop_distance','adds_fraction','management_age')
ACTION_RATES={'inherit':.4,'exchange':.3,'compose':.3}
PARAMETER_RATE=.7

def contract():
    return dict(version=VERSION,max_nodes_per_action=MAX_NODES,max_depth=MAX_DEPTH,fan_in=FAN_IN,
                max_strategy_connections=5*MAX_NODES*FAN_IN,weight_bound=WEIGHT_BOUND,
                states=list(STATES),action_rates=ACTION_RATES,composition_whole_probability=.5,
                parameter_mutation_probability=PARAMETER_RATE,activation='tanh',
                scaling='tanh((x-catalog_midpoint)/catalog_half_range); no fitted statistics',
                input_validity='all reachable nonzero-weight inputs required',recurrent=False)
