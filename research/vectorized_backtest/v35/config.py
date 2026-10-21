"""Persisted search and compute envelope; independent of symbolic crossover."""
VERSION='v35-recurrent-multiaction-v1'
MAX_NODES=512
MAX_STRATEGY_NODES=512
MAX_DEPTH=512
FAN_IN=16
INITIAL_SIZES=(1,2,4,8,12,16,24,32,48,64,80,96)
WEIGHT_BOUND=8.
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
                parameter_mutation_probability=PARAMETER_RATE,activation='bounded-typed-v2',operators=['tanh','max','min','mean','product','gate','attention','identity','conv'],
                temporal_clock='replay clock steps; previous step only; zero reset per session',
                attention='scalar query plus key/value pairs; stable softmax; weighted terms',
                operator_initialization=dict(tanh_only_graph_probability=.5,mixed_node_probability=.35,temporal_module_probability=.5),
                initial_sizes=list(INITIAL_SIZES),changed_actions_per_mating=[1,5],
                scaling='absolute signed-log plus stable shifted float32 current-clock cross-sectional z-score; no future statistics',
                input_channels=['absolute','cross_sectional','validity'],projection_layout='full certified catalog; independent of candidate cohort',
                input_validity='observed current or delayed market-value ancestor required',recurrent=True,
                strategy_node_budget=MAX_STRATEGY_NODES,capacity_buckets=[8,16,32,64,128,256,512],
                workspace_budget_gib=4.,memory_reset='zero at independent session start; exact state export for replay checkpoints',
                sizing_caps={'entry':1000,'add':200,'reduce_fraction':1},minimum_order_dollars=0.,
                speciation={'structure_weight':.4,'parameter_weight':.2,'behavior_weight':.4,'stagnation_generations':10,'ancestry_generations':3,'within_species_probability':.8,'representative_limit':64,'target_species':[16,32]},
                homologous_weight_exchange_probability=.5,initialization=INITIALIZATION)

INITIALIZATION=dict(calibrated_fraction=.8,activation_routes=[.30,.30,.20,.20],
    target_rate_ranges=[[.001,.05],[.05,.30],[.30,.95]],policy_family_fraction=.5,
    maximum_constant_repairs=2,constant_output_span=1e-4,training_only=True,
    fitness_selection=False,structural_strata=['depth','fanin','recurrence'],
    search_bounds_unchanged=True)
