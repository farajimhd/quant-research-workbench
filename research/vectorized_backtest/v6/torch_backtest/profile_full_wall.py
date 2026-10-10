"""One frozen all30 financial pass with wall attribution; never optimize or validate."""
import os
os.environ['PYTHONDONTWRITEBYTECODE'] = '1'
import argparse
from copy import deepcopy
import gc
import json
from pathlib import Path
import shutil
import numpy as np
import torch
from . import resident_evaluator as resident, sparse_evaluator as sparse, training_pass as training
from . import run_search, financial_audit, runtime
from .compact_runner import CompactProgramRunner
from .program_runner import ProgramRunner
from .runner import SqueezeRunner
from .sparse_replay import SparseInputs
from .captured_rules import SharedRuleBatch
from .materialize import owned_run
from .genome import StrategySpace
from .staged import selection_rank, migrate
from .stability import LowerTailDollarObjective
from .profile_compact_sessions import compare_sessions
from .wall_trace import WallTrace


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('sessions','inputs','structure','output','population-checkpoint','reference-generation','capture-seeds'):
        parser.add_argument('--'+name, type=Path, required=True)
    parser.add_argument('--population', type=int, default=1024)
    parser.add_argument('--batch-size', type=int, default=128)
    parser.add_argument('--session-workers', type=int, default=8)
    parser.add_argument('--compiler-budget', type=int, default=8192)
    args = parser.parse_args(argv)
    root = runtime.require_runtime(args.output)
    if any(root.iterdir()):
        raise ValueError('Wall profile requires a new empty owned output')
    trace = WallTrace()
    evaluate = None
    outcome = 'failed'
    with owned_run(root, version='v6-frozen-full-wall-profile-v1'):
        try:
            with trace.span('startup_and_frozen_contract'):
                spec = json.loads(args.sessions.read_text())
                saved = json.loads(args.population_checkpoint.read_text())
                reference = json.loads((args.reference_generation/'complete.json').read_text())
                population = [run_search.restore(v) for v in saved['population']]
                token = training.population_hash(population)
                if (len(population) != args.population or token != saved['population_sha256']
                        or reference['population_sha256'] != token or reference.get('validation_opened') is not False
                        or reference['training_days'] != [s['day'] for s in spec['training']]
                        or len(spec['training']) != 30):
                    raise ValueError('Frozen full-training reference/population mismatch')
                objective = LowerTailDollarObjective()
                from dataclasses import asdict
                if reference['objective'] != asdict(objective):
                    raise ValueError('Frozen objective changed')
                # Copy only compiler initializer authority into this owned profile.
                # Production checkpoints, RNG and initializer records remain untouched.
                shutil.copytree(args.capture_seeds, root/'capture-seeds')
                runtime.configure_caches(root/'cache', recompile_limit=args.compiler_budget)
                evaluate = resident.ResidentSessionEvaluator(args.inputs,args.structure,batch_size=args.batch_size,
                    holding_capacity=40,maximum_fills=16384,maximum_input_gib=1.,maximum_state_gib=.35,
                    capture_variants=2,capture_seed_root=root/'capture-seeds',compiler_specialization_budget=args.compiler_budget)
                runtime.write_json(root/'identity.json',dict(arguments={k:str(v) if isinstance(v,Path) else v for k,v in vars(args).items()},
                    population_sha256=token,source_sha256=runtime.code_hash(),
                    checkpoint_sha256=runtime.file_hash(args.population_checkpoint),
                    reference_sha256=runtime.file_hash(args.reference_generation/'complete.json'),
                    optimization_started=False,validation_opened=False))
            targets = [
                (SparseInputs,'__init__','input_load_verify_transfer'),
                (SparseInputs,'compile','causal_rule_evaluation'),
                (SharedRuleBatch,'__init__','shared_population_rule_preparation'),
                (SharedRuleBatch,'close','shared_rule_cleanup'),
                (CompactProgramRunner,'__init__','broker_construction'),
                (CompactProgramRunner,'set_sparse_population','broker_population_update_reset'),
                (SqueezeRunner,'compile','broker_compile_capture'),
                (SqueezeRunner,'run','financial_replay_host_wall'),
                (ProgramRunner,'live_metrics','position_reporting_and_live_metrics'),
                (resident.ResidentSessionEvaluator,'_reserve_capture','capture_eviction'),
                (resident,'seal_batch','batch_seal_audit_save'),
                (resident,'seal_session','session_merge_save'),
                (resident,'audit_fills','reused_batch_financial_audit'),
                (sparse,'audit_fills','financial_fill_audit'),
                (sparse,'seal_ledger','ledger_transfer_serialize_save'),
                (training,'score','objective_calculation'),
                (gc,'collect','garbage_collection'),
            ]
            # Module-local imported writers are patched as well as the runtime writer.
            for module in (runtime,resident,sparse,training):
                targets.extend([(module,'write_json','json_receipt_save'),(module,'file_hash','file_integrity_hash')])
            with trace.patches(targets):
                with trace.span('cold_compiler_context_restoration'):
                    evaluate.restore_capture_context(spec['training'],root,workers=args.session_workers)
                with trace.span('full_training_pass_wall'):
                    with trace.span('prepare_and_concurrent_replay_wall'):
                        evaluate.prepare_pass(spec['training'],population,root/'pass',workers=args.session_workers)
                    with trace.span('all30_receipt_reaudit_objective_seal_wall'):
                        ranking, ordered = training.full_training_pass(spec['training'],[s['day'] for s in spec['validation']],
                            population,evaluate,root/'pass',workers=args.session_workers,objective=objective)
                    with trace.span('winner_selection_wall'):
                        feasible = ranking['feasible'].tolist(); scores = ranking['score'].tolist()
                        rank = sorted((i for i,v in enumerate(feasible) if v),key=lambda i:(-scores[i],i))
                        if not rank: raise ValueError('No financially feasible frozen candidate')
                        runtime.write_json(root/'profile-winner.json',dict(candidate=rank[0],score=scores[rank[0]],
                            selection_allowed=False,validation_opened=False))
                    with trace.span('parent_selection_and_mutation_dry_run_wall'):
                        rng = np.random.default_rng();rng.bit_generator.state = deepcopy(saved['rng_state'])
                        parents = selection_rank(rng,rank,ranking,ordered,objective)
                        next_population = migrate(rng,population,parents,args.population,StrategySpace(),None)
                    with trace.span('profile_checkpoint_serialize_save_wall'):
                        runtime.write_json(root/'profile-checkpoint.json',dict(population=[run_search.state(v) for v in next_population],
                            population_sha256=training.population_hash(next_population),rng_state=deepcopy(rng.bit_generator.state),
                            optimization_started=False,selection_allowed=False,validation_opened=False))
                with trace.span('outside_pass_exact_reference_comparison'):
                    comparisons = [compare_sessions(args.reference_generation/s['day'],root/'pass'/s['day']) for s in spec['training']]
                    runtime.write_json(root/'parity.json',dict(comparisons=comparisons,actual_fill_parity='exact',financial_metrics_parity='exact'))
                with trace.span('cleanup_wall'):
                    evaluate.close(); evaluate = None
            outcome = 'complete'
        finally:
            if evaluate is not None:
                with trace.span('failure_cleanup_wall'): evaluate.close()
            runtime.write_json(root/'wall-timing.json',dict(status=outcome,parts=trace.summary(),events=trace.events,
                elapsed_seconds=__import__('time').perf_counter()-trace.origin,
                interpretation='Top-level spans are elapsed wall. Nested inclusive work overlaps and must not be summed. Host intervals retain existing CUDA barriers; no per-tick instrumentation or added synchronizations.',
                optimization_started=False,validation_opened=False))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
