"""All-training V6 generation/resume controller; no validation entry point.

This library is not launched by profiling. Population/generation/concurrency
arguments must come from the user's final measured-budget decision.
"""
from copy import deepcopy
from pathlib import Path
import json
import numpy as np
from dataclasses import asdict
from .runtime import require_runtime,write_json,code_hash,file_hash
from .materialize import owned_run
from .training_pass import full_training_pass,population_hash
from .evolution import sample
from .genome import StrategySpace
from .staged import migrate,selection_rank
from .run_search import state,restore,fingerprint,clean
from .stability import LowerTailDollarObjective


def verify_completed_pass(root,record):
    if record.get('validation_opened',True) or not record.get('selection_allowed') or len(record.get('training_days',[]))!=30:
        raise ValueError('Incomplete full-training pass cannot be resumed')
    for day,checksum in record['session_receipts'].items():
        path=root/day/'receipt.json'
        if file_hash(path)!=checksum:raise ValueError('Durable training receipt changed')
        session=json.loads(path.read_text())
        for batch in session.get('batch_receipts',[]):
            folder=root/day/batch['directory'];batch_path=folder/'receipt.json'
            if folder.resolve().parent!=(root/day).resolve() or file_hash(batch_path)!=batch['sha256']:raise ValueError('Batch receipt identity changed')
            receipt=json.loads(batch_path.read_text())
            if file_hash(folder/'fills.pt')!=receipt['ledger_sha256']:raise ValueError('Durable financial ledger changed')


def run_generations(spec,population_size,generations,evaluator,output,*,seed=2236,workers=2,objective=LowerTailDollarObjective()):
    if type(population_size) is not int or population_size<10 or type(generations) is not int or generations<1:raise ValueError('Explicit valid full-search budget required')
    if type(workers) is not int or not 1<=workers<=8:raise ValueError('Full-training session workers must be 1..8')
    output=require_runtime(output);space=StrategySpace()
    contract=dict(version='v6-all-training-search-v1',source_sha256=code_hash(),spec=fingerprint(spec),population=population_size,
        generations=generations,seed=seed,workers=workers,objective=asdict(objective),
        evaluator=evaluator.contract(spec['training'],workers) if hasattr(evaluator,'contract') else {'custom_evaluator':True})
    checkpoint=output/'checkpoint.json'
    with owned_run(output,version=contract['version']):
        rng=np.random.default_rng(seed);completed=0
        if checkpoint.exists():
            saved=json.loads(checkpoint.read_text())
            if saved['contract']!=contract:raise ValueError('Exact immutable resume contract changed')
            completed=saved['completed_generations'];population=[restore(v) for v in saved['population']]
            if len(population)!=population_size or population_hash(population)!=saved['population_sha256']:raise ValueError('Checkpoint population/RNG identity mismatch')
            rng.bit_generator.state=saved['rng_state']
            for index in range(1,completed+1):
                root=output/f'generation-{index:04d}';record=json.loads((root/'complete.json').read_text())
                verify_completed_pass(root,record)
            if completed and file_hash(output/f'generation-{completed:04d}'/'complete.json')!=saved['last_generation_sha256']:
                raise ValueError('Last generation seal changed')
        else:
            population=sample(rng,space,population_size)
            write_json(checkpoint,dict(contract=contract,completed_generations=0,population=[state(v) for v in population],
                population_sha256=population_hash(population),rng_state=deepcopy(rng.bit_generator.state),last_generation_sha256=None))
        for generation in range(completed+1,generations+1):
            root=require_runtime(output/f'generation-{generation:04d}')
            if (root/'complete.json').exists():
                import torch
                record=json.loads((root/'complete.json').read_text());verify_completed_pass(root,record)
                if record['population_sha256']!=population_hash(population) or record['objective']!=asdict(objective):raise ValueError('Completed generation resume contract changed')
                ranking={k:torch.as_tensor(record['ranking'][k],dtype=torch.bool if k=='feasible' else torch.float64) for k in ('score','feasible')}
                ordered=[json.loads((root/day/'receipt.json').read_text())['metrics'] for day in record['training_days']]
            else:
                if hasattr(evaluator,'prepare_pass'):
                    evaluator.prepare_pass(spec['training'],population,root,workers=workers)
                ranking,ordered=full_training_pass(spec['training'],[s['day'] for s in spec['validation']],population,evaluator,root,workers=workers,objective=objective)
            eligible=[i for i,v in enumerate(ranking['feasible'].tolist()) if v]
            scores=ranking['score'].tolist();rank=sorted(eligible,key=lambda i:(-scores[i],i))
            if not rank:raise ValueError('No financially feasible parent; preserve completed evaluation and stop')
            write_json(root/'winner.json',dict(candidate=rank[0],individual=state(population[rank[0]]),score=scores[rank[0]],validation_opened=False))
            # Parent selection and RNG changes happen only beyond the all30 barrier.
            if generation<generations:
                parents=selection_rank(rng,rank,ranking,ordered,objective)
                population=migrate(rng,population,parents,population_size,space,None)
            write_json(checkpoint,dict(contract=contract,completed_generations=generation,population=[state(v) for v in population],
                population_sha256=population_hash(population),rng_state=deepcopy(rng.bit_generator.state),last_generation_sha256=file_hash(root/'complete.json')))
            write_json(output/'status.json',dict(status='complete' if generation==generations else 'running',completed_generations=generation,total_generations=generations,validation_opened=False))
            if (output/'STOP').exists():
                write_json(output/'status.json',dict(status='interrupted',completed_generations=generation,total_generations=generations,validation_opened=False));return 130
    return 0
