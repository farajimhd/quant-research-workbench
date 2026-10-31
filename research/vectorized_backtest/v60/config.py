"""Persisted search and compute envelope; independent of symbolic crossover."""

VERSION='v60-market-only-pipelined-v4'

MAX_NODES=1024

MAX_STRATEGY_NODES=1024

MAX_DEPTH=512

FAN_IN=16

INITIAL_SIZES=(1,2,4,8,12,16,24,32,48,64,80,96)

WEIGHT_BOUND=8.

STATES=tuple('reserved_'+str(i) for i in range(11))  # ABI padding; no model state inputs

ACTION_RATES={'uniform':.25,'union':.25,'intersection':.25,'xor':.25}

PARAMETER_RATE=.7



def contract():

    return dict(strategy_crossover=dict(entry_routing=.1,reciprocal_veto=.1,entry_management_exchange=.1,existing=.7,arbitration='A-first current market entry; no position ownership',branch_budget='included in 1024 total'),feature_policy='v60-market-contract-v1',mask_crossover=dict(modes=ACTION_RATES,numerical_inherit=.5,numerical_blend=.5,alignment='persistent action-local node slots; input identity',replay='unchanged sparse engine'),crossover_management=CROSSOVER,version=VERSION,max_nodes_per_action=MAX_NODES,max_depth=MAX_DEPTH,fan_in=FAN_IN,

                max_strategy_connections=MAX_STRATEGY_NODES*FAN_IN,weight_bound=WEIGHT_BOUND,

                states=[],action_rates=ACTION_RATES,composition_whole_probability=.5,

                parameter_mutation_probability=PARAMETER_RATE,activation='bounded-typed-v2',operators=['tanh','max','min','mean','product','gate','attention','identity','conv','cross'],

                temporal_clock='same-candle requests; causal market crossing events; market-only signals; no position feedback or recurrent edges',

                attention='scalar query plus key/value pairs; stable softmax; weighted terms',

                operator_initialization=dict(tanh_only_graph_probability=.5,mixed_node_probability=.35,temporal_module_probability=0.),

                initial_sizes=list(INITIAL_SIZES),changed_actions_per_mating=[1,5],

                scaling='absolute signed-log plus stable shifted float32 current-clock cross-sectional z-score; no future statistics',

                input_channels=['absolute','cross_sectional','validity'],projection_layout='full certified catalog; independent of candidate cohort',

                input_validity='optional zero-filled inputs plus explicit validity channels; observed current market-value ancestor required',recurrent=False,
                action_scope='all heads market-only; execution interprets requests against positions',

                strategy_node_budget=MAX_STRATEGY_NODES,capacity_buckets=[8,16,32,64,128,256,512,1024],

                workspace_budget_gib=4.,memory_reset='current-clock scratch; position state reset at independent session start',

                sizing_caps={'entry':1000,'add':200,'reduce_fraction':1},minimum_order_dollars=0.,

                speciation={'structure_weight':.4,'parameter_weight':.2,'behavior_weight':.4,'stagnation_generations':10,'ancestry_generations':3,'within_species_probability':.8,'representative_limit':64,'target_species':[16,32]},

                initialization=INITIALIZATION)



INITIALIZATION=dict(calibrated_fraction=.8,activation_routes=[.30,.30,.20,.20],

    target_rate_ranges=[[.001,.05],[.05,.30],[.30,.95]],policy_family_fraction=.5,

    maximum_constant_repairs=2,constant_output_span=1e-4,training_only=True,

    fitness_selection=False,structural_strata=['depth','fanin','recurrence'],

    search_bounds_unchanged=True)


CROSSOVER=dict(children_per_pair={'1':.75,'2':.20,'3':.05},reciprocal_siblings=True,
    fixed_population_and_species_budgets=True,validation_feedback=False,
    telemetry='training-only parent objective comparisons, mask/mutation outcomes and sibling survival; observational multi-tag')
