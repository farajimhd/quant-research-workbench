"""Persisted search and compute envelope; independent of symbolic crossover."""
VERSION='v29-recurrent-multiaction-v1'
MAX_NODES=512
MAX_STRATEGY_NODES=512
MAX_DEPTH=512
FAN_IN=8
INITIAL_SIZES=tuple(range(1,17))+(1,1,20,24,32,48,64)
WEIGHT_BOUND=4.
STATES=('held','profit_fraction','age_hours','capital_entries','stop_distance','adds_fraction','management_age',
        'previous_entry_request','previous_add_request','previous_reduce_request',
        'previous_entry_filled','previous_add_filled','previous_reduce_filled','previous_exit_filled',
        'previous_entry_unfilled','previous_add_unfilled','previous_action_mask','elapsed_seconds')
ACTION_RATES={'inherit':.4,'exchange':.3,'compose':.3}
PARAMETER_RATE=.7

def contract():
    return dict(version=VERSION,max_nodes_per_action=MAX_NODES,max_depth=MAX_DEPTH,fan_in=FAN_IN,
                max_strategy_connections=MAX_STRATEGY_NODES*FAN_IN,weight_bound=WEIGHT_BOUND,
                states=list(STATES),action_rates=ACTION_RATES,composition_whole_probability=.5,
                parameter_mutation_probability=PARAMETER_RATE,activation='tanh',
                initial_sizes=list(INITIAL_SIZES),changed_actions_per_mating=[1,2],
                scaling='absolute signed-log plus current-clock cross-sectional z-score; no future statistics',
                input_channels=['absolute','cross_sectional','validity'],
                input_validity='observed current or delayed market-value ancestor required',recurrent=True,
                strategy_node_budget=MAX_STRATEGY_NODES,capacity_buckets=[8,16,32,64,128,256,512],
                workspace_budget_gib=4.,memory_reset='zero at independent session start; exact state export for replay checkpoints',
                sizing_caps={'entry':1000,'add':200,'reduce_fraction':1},minimum_order_dollars=1.,
                niche_protection={'elite_fraction':.5,'maximum_protected_niches':16,'structural_signature':'total-size-largest-action-recurrence'},
                homologous_weight_exchange_probability=.5)
