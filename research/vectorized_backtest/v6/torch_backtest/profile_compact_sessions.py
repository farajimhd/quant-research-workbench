"""One-owner full-session compact serial/concurrent qualification, training only."""
import os
os.environ['PYTHONDONTWRITEBYTECODE']='1'
import argparse
import json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from time import perf_counter
import numpy as np
import torch
from .runtime import require_runtime,configure_caches,write_json,file_hash,code_hash
from .materialize import owned_run
from .run_structure import training_days
from .sparse_evaluator import SparseSessionEvaluator
from .resident_evaluator import ResidentSessionEvaluator
from .evolution import sample,Individual,STAGES
from .program import Program
from .feature_bank import CATALOG
from .genome import StrategySpace
from .training_pass import population_hash


def frozen_profile_population(path,count):
    """Select an exact prefix from a completed training-only profile."""
    path=Path(path);folder=path.parent
    receipt=json.loads((folder/'receipt.json').read_text())
    identity=json.loads((folder/'identity.json').read_text())
    if receipt.get('status')!='complete' or any(record.get('validation_opened') is not False or record.get('optimization_started') is not False for record in (receipt,identity)):
        raise ValueError('Frozen population requires a completed training-only profile')
    rows=json.loads(path.read_text());members=[]
    if type(count) is not int or not 1<=count<=len(rows):raise ValueError('Frozen population prefix exceeds source coverage')
    for row in rows:
        if set(row['programs'])!=set(STAGES):raise ValueError('Frozen lifecycle coverage changed')
        programs={stage:Program.from_payload(row['programs'][stage]) for stage in STAGES}
        for program in programs.values():program.validate(CATALOG)
        member=Individual(list(row['policy']),{stage:[(list(program.nodes),program.output)] for stage,program in programs.items()},
            {stage:[] for stage in STAGES},dict(row['management']))
        if member.payload()!=row:raise ValueError('Frozen candidate payload changed on reconstruction')
        members.append(member)
    if population_hash(members)!=identity['population_sha256']:raise ValueError('Frozen population identity changed')
    StrategySpace().validate([m.policy for m in members])
    return members[:count],dict(population_file_sha256=file_hash(path),source_population_sha256=identity['population_sha256'],
        source_identity_sha256=file_hash(folder/'identity.json'),source_receipt_sha256=file_hash(folder/'receipt.json'),
        source_candidates=len(members),selected_indices=list(range(count)))


def compare_sessions(reference,actual):
    """Scheduling cannot change a candidate's complete economic history."""
    before=json.loads((reference/'receipt.json').read_text())
    after=json.loads((actual/'receipt.json').read_text())
    for name in ('day','population_sha256','candidate_indices','metrics','input_receipt_sha256','structural_receipt_sha256'):
        if before[name]!=after[name]:raise ValueError('Concurrent session changed '+name)
    def lanes(root,receipt):
        for batch in receipt['batch_receipts']:
            folder=root/batch['directory']
            record=json.loads((folder/'receipt.json').read_text())
            if file_hash(folder/'receipt.json')!=batch['sha256'] or file_hash(folder/'fills.pt')!=record['ledger_sha256']:
                raise ValueError('Comparison receipt/ledger changed')
            data=torch.load(folder/'fills.pt',map_location='cpu',weights_only=True)
            for lane,(index,count) in enumerate(zip(record['candidate_indices'],data['counts'].tolist())):
                yield index,count,data['ledger'][lane,:count]
    from itertools import zip_longest
    for left,right in zip_longest(lanes(reference,before),lanes(actual,after)):
        if left is None or right is None or left[:2]!=right[:2]:raise ValueError('Candidate identity/fill coverage changed')
        if not torch.equal(left[2],right[2]):raise ValueError('Concurrent actual fills changed')
    return dict(day=before['day'],actual_fill_parity='exact',financial_metrics_parity='exact',
                reference_receipt_sha256=file_hash(reference/'receipt.json'),actual_receipt_sha256=file_hash(actual/'receipt.json'))


def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--inputs',type=Path,required=True);p.add_argument('--structure',type=Path,required=True)
    p.add_argument('--output',type=Path,required=True)
    p.add_argument('--population',type=int,default=128);p.add_argument('--batch-size',type=int,default=128)
    p.add_argument('--session-count',type=int,default=2);p.add_argument('--workers',type=int,default=2)
    p.add_argument('--worker-counts',help='Comma-separated resident concurrency sweep, for example 2,4,8,16')
    p.add_argument('--reference',type=Path,help='Completed serial-warm session directory for this exact population; skips serial passes')
    p.add_argument('--concurrent-only',action='store_true',help='Measure only requested concurrency; first pass is an independently audited batch-partition reference')
    p.add_argument('--resident-repeats',type=int,default=1,help='Repeat the full pass with retained broker buffers; exact comparison against first pass')
    p.add_argument('--holding-capacity',type=int,default=40);p.add_argument('--maximum-fills',type=int,default=16384)
    p.add_argument('--backend',choices=['compile','cudagraph','compiled_graph'],default='compiled_graph')
    p.add_argument('--graph-steps',type=int,default=16,help='Ticks per captured broker block, 1..64; default preserves existing allocation')
    p.add_argument('--seed',type=int,default=2236)
    p.add_argument('--population-reference',type=Path,help='Frozen population.json from a completed training-only profile; uses the first --population candidates without resampling')
    p.add_argument('--maximum-input-gib',type=float,default=4.);p.add_argument('--maximum-state-gib',type=float,default=4.)
    p.add_argument('--capture-variants',type=int,choices=(1,2),default=1,help='Exact broker captures retained per session; included in the memory envelope')
    a=p.parse_args(argv)
    worker_counts=[a.workers] if a.worker_counts is None else [int(v) for v in a.worker_counts.split(',')]
    if not worker_counts or len(set(worker_counts))!=len(worker_counts) or not 1<=a.resident_repeats<=3 or not 2<=a.session_count<=30 or any(not 1<=w<=a.session_count for w in worker_counts) or a.population<1 or not 1<=a.graph_steps<=64:
        raise ValueError('Invalid bounded profiling dimensions')
    root=require_runtime(a.output)
    if (root/'identity.json').exists():raise ValueError('Use a new immutable profile identity')
    configure_caches(root/'cache')
    if not torch.cuda.is_available():raise RuntimeError('CUDA required; no CPU timing fallback')
    with owned_run(root,version='v6-compact-concurrent-profile-v1'):
        days=training_days(a.inputs)[:a.session_count]
        sessions=[json.loads((a.inputs/day/'complete.json').read_text())['identity']['session'] for day in days]
        members,population_source=frozen_profile_population(a.population_reference,a.population) if a.population_reference else (sample(np.random.default_rng(a.seed),StrategySpace(),a.population),None)
        evaluator_type=SparseSessionEvaluator if a.backend=='compile' else ResidentSessionEvaluator
        capture_options={} if a.backend=='compile' else dict(graph_steps=a.graph_steps,capture_variants=a.capture_variants)
        evaluate=evaluator_type(a.inputs,a.structure,batch_size=a.batch_size,backend=a.backend,
            maximum_input_gib=a.maximum_input_gib,maximum_state_gib=a.maximum_state_gib,
            maximum_fills=a.maximum_fills,holding_capacity=a.holding_capacity,**capture_options)
        contract=evaluate.contract(sessions,a.workers)
        write_json(root/'identity.json',dict(code_sha256=code_hash(),arguments={k:str(v) if isinstance(v,Path) else v for k,v in vars(a).items()},contract=contract,
            population_sha256=population_hash(members),population_source=population_source,validation_opened=False,optimization_started=False))
        write_json(root/'population.json',[v.payload() for v in members])
        rows=[]
        audits=[]
        modes=([] if a.reference or a.concurrent_only else [('serial-cold',1),('serial-warm',1)])+[(f'concurrent-warm-{w}',w) for w in worker_counts]
        if a.resident_repeats>1:
            if len(worker_counts)!=1:raise ValueError('Retained-repeat comparison requires one concurrency')
            modes.extend((f'concurrent-warm-{worker_counts[0]}-repeat-{n}',worker_counts[0]) for n in range(2,a.resident_repeats+1))
        for mode,workers in modes:
            # Profiling trials measure fresh ownership consistently; production
            # evaluators retain compatible buffers across generations.
            if hasattr(evaluate,'close') and '-repeat-' not in mode:evaluate.close()
            # Whole-pass graph owners have gone out of scope. Release unused
            # allocator cache before measuring the next independent envelope.
            import gc
            gc.collect();torch.cuda.synchronize();torch.cuda.empty_cache()
            try:evaluate.contract(sessions,workers)
            except MemoryError as error:
                rows.append(dict(mode=mode,status='rejected_memory_envelope',reason=str(error)))
                write_json(root/'measurements.json',rows);print(rows[-1],flush=True)
                continue
            destination=require_runtime(root/mode)
            write_json(root/'status.json',dict(stage=mode,sessions=days,workers=workers,validation_opened=False))
            torch.cuda.synchronize();torch.cuda.reset_peak_memory_stats();started=perf_counter()
            if hasattr(evaluate,'prepare_pass'):evaluate.prepare_pass(sessions,members,destination,workers=workers)
            with ThreadPoolExecutor(max_workers=workers) as pool:
                futures=[pool.submit(evaluate,session,members,destination/session['day']) for session in sessions]
                for future in futures:future.result()
            torch.cuda.synchronize()
            row=dict(mode=mode,elapsed_seconds=perf_counter()-started,peak_allocated_bytes=torch.cuda.max_memory_allocated(),
                peak_reserved_bytes=torch.cuda.max_memory_reserved(),candidate_sessions=a.population*len(days))
            rows.append(row);write_json(root/'measurements.json',rows);print(row,flush=True)
            if mode.startswith('concurrent-warm') and (a.reference or not a.concurrent_only or '-repeat-' in mode):
                reference=(root/f'concurrent-warm-{workers}') if '-repeat-' in mode else a.reference if a.reference else root/'serial-warm'
                audits.extend(dict(workers=workers,**compare_sessions(reference/day,destination/day)) for day in days)
        write_json(root/'receipt.json',dict(status='complete',measurements=rows,audits=audits,validation_opened=False,
            optimization_started=False,backend=a.backend,partition_reference=bool(a.concurrent_only and not a.reference),
            limitations=['Training-only throughput evidence']+(['Independent financial audit; no scheduling parity reference in this pass'] if a.concurrent_only and not a.reference else [])))
        write_json(root/'status.json',dict(status='complete',stage='Full-session financial audit passed' if a.concurrent_only and not a.reference else 'Exact full-session comparison audit passed',validation_opened=False))
        if hasattr(evaluate,'close'):evaluate.close()
    return 0


if __name__=='__main__':raise SystemExit(main())
