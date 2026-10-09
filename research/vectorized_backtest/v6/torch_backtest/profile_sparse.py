"""Bounded sparse replay profiling; never runs optimization or validation."""
import os
os.environ['PYTHONDONTWRITEBYTECODE']='1'
import argparse
import json
from pathlib import Path
from time import perf_counter
import numpy as np
import torch
from .runtime import require_runtime,write_json,configure_caches,code_hash,file_hash
from .materialize import owned_run
from .sparse_replay import SparseInputs
from .sparse_runner import SparseProgramRunner
from .compact_runner import CompactProgramRunner
from .genome import StrategySpace
from .evolution import sample,Individual,STAGES
from .program import Program
from .financial_audit import audit_fills
from .run_search import clean


def profile_metrics(metrics):
    """Missing statistical diagnostics are null; financial values must exist."""
    for name in ('cash','equity','net_pnl','fees','drawdown','stop_risk_dollar_seconds','capital_dollar_seconds'):
        if not torch.isfinite(torch.as_tensor(metrics[name])).all():
            raise ValueError('Non-finite profiling financial metric: '+name)
    return clean(metrics)


def audit_profile(root, metrics, *, full_session):
    """Audit actual fills without presenting a prefix as terminal evidence."""
    audited = dict(metrics)
    if not full_session:
        audited['terminal_valid'] = [quantity == 0 for quantity in metrics['open_quantity']]
    reports = audit_fills(root/'fills.pt', audited)
    record = dict(status='passed', ledger_sha256=file_hash(root/'fills.pt'),
                  full_session=full_session, terminal_eligibility_qualified=full_session,
                  candidates=len(reports), reports=reports, validation_opened=False)
    write_json(root/'financial-audit.json', record)
    return file_hash(root/'financial-audit.json')


def load_profile_population(path):
    payload=json.loads(path.read_text())
    if isinstance(payload,dict):payload=payload['population']
    members=[]
    for value in payload:
        programs={stage:Program.from_payload(value['programs'][stage]) for stage in STAGES}
        member=Individual(value['policy'],{s:[(list(p.nodes),p.output)] for s,p in programs.items()},
            {s:[] for s in STAGES},value['management'])
        if member.payload()!=value:raise ValueError('Profile population round-trip changed a program')
        members.append(member)
    return members


def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--inputs',type=Path,required=True);p.add_argument('--output',type=Path,required=True)
    p.add_argument('--day',required=True);p.add_argument('--batch-size',type=int,default=8)
    p.add_argument('--seconds',type=int,default=256);p.add_argument('--repeats',type=int,default=2)
    p.add_argument('--backend',choices=['eager','compile','cudagraph','compiled_graph'],default='eager')
    p.add_argument('--device',choices=['cuda','cpu'],default='cuda')
    p.add_argument('--cpu-threads',type=int,default=1)
    p.add_argument('--population-file',type=Path,help='Replay the exact sealed profiling population rather than resampling')
    p.add_argument('--broker',choices=['daily-union','compact'],default='compact')
    p.add_argument('--rule-backend',choices=['auto','eager','cudagraph'],default='auto')
    p.add_argument('--holding-capacity',type=int,default=40)
    p.add_argument('--seed',type=int,default=2236);p.add_argument('--maximum-input-gib',type=float,default=4.)
    p.add_argument('--maximum-state-gib',type=float,default=4.)
    p.add_argument('--rule-workspace-gib',type=float,default=2.)
    p.add_argument('--maximum-fills',type=int,default=4096,help='Per-candidate bounded ledger capacity; exhaustion fails closed')
    p.add_argument('--structure',type=Path,help='Certified sparse raw-level sidecar; enables both target modes')
    a=p.parse_args(argv)
    if not 1<=a.batch_size<=1024 or not 1<=a.seconds<=19800 or not 1<=a.repeats<=5:raise ValueError('Invalid bounded profile dimensions')
    if a.device=='cpu':
        if a.backend not in ('eager','compile') or a.rule_backend=='cudagraph':raise ValueError('CPU profiling requires native or compiled CPU execution')
        if not 1<=a.cpu_threads<=os.cpu_count():raise ValueError('Invalid bounded CPU threads')
        torch.set_num_threads(a.cpu_threads);torch.set_num_interop_threads(1)
    if a.rule_backend=='auto':a.rule_backend='cudagraph' if a.device=='cuda' else 'eager'
    def synchronize():
        if a.device=='cuda':torch.cuda.synchronize()
    root=require_runtime(a.output)
    if (root/'receipt.json').exists():raise ValueError('A profiling receipt already exists; use a new run identity')
    configure_caches(root/'cache')
    if a.device=='cuda' and not torch.cuda.is_available():raise RuntimeError('Requested CUDA unavailable; no timing fallback')
    with owned_run(root,version='v6-sparse-profile-v1'):
        began=perf_counter();inputs=SparseInputs(a.inputs/a.day,device=a.device,maximum_gib=a.maximum_input_gib)
        if inputs.receipt['identity']['session']['day']!=a.day:raise ValueError('Day identity mismatch')
        synchronize();load_seconds=perf_counter()-began
        rng=np.random.default_rng(a.seed);space=StrategySpace();members=[];draws=0
        # Explicit profiling stratum, not a change to the full search space.
        if a.population_file is not None:
            members=load_profile_population(a.population_file)
            if len(members)!=a.batch_size:raise ValueError('Exact profile population size changed')
        while len(members)<a.batch_size:
            value=sample(rng,space,1)[0];draws+=1
            if a.structure is not None or int(value.policy[6])==0:members.append(value)
            if draws>100*a.batch_size:raise ValueError('Percentage-target profiling stratum not found')
        write_json(root/'population.json',dict(seed=a.seed,draws=draws,stratum='all_target_modes' if a.structure is not None else 'percentage_targets_only',population=[v.payload() for v in members]))
        print('Compiling causal sparse lifecycle gates',flush=True)
        write_json(root/'status.json',dict(stage='Compiling causal lifecycle gates',validation_opened=False))
        union=np.unique(inputs.arrays['top_indices']);union=union[union>=0].tolist()
        gates,rule_seconds=inputs.compile(members,listing_ids=union,backend=a.rule_backend,workspace_gib=a.rule_workspace_gib)
        runner_type=CompactProgramRunner if a.broker=='compact' else SparseProgramRunner
        dimensions={'holding_capacity':a.holding_capacity} if a.broker=='compact' else {}
        runner=runner_type(inputs,space,members,gates,structure=a.structure,backend=a.backend,maximum_fills=a.maximum_fills,maximum_state_gib=a.maximum_state_gib,**dimensions)
        print('Preparing financial replay: '+a.device+'/'+a.backend,flush=True)
        write_json(root/'status.json',dict(stage='Preparing financial replay',device=a.device,backend=a.backend,validation_opened=False))
        setup=perf_counter();runner.compile();synchronize();setup=perf_counter()-setup
        measurements=[];previous=None
        for repeat in range(a.repeats):
            if a.device=='cuda':torch.cuda.reset_peak_memory_stats()
            started=perf_counter()
            # Full captured sessions include the separately captured remainder;
            # an explicit prefix is required to contain whole graph blocks.
            steps=None if a.seconds==len(inputs.arrays['clocks']) else a.seconds
            def emit(progress):
                snapshot=dict(stage='Financial replay',repeat=repeat,**progress,validation_opened=False)
                write_json(root/'status.json',snapshot);print(snapshot,flush=True)
            metrics=runner.run(steps=steps,progress=emit);synchronize();elapsed=perf_counter()-started
            measurements.append(dict(repeat=repeat,elapsed_seconds=elapsed,candidate_clock_updates_per_second=a.batch_size*a.seconds/elapsed,
                peak_allocated_bytes=torch.cuda.max_memory_allocated() if a.device=='cuda' else None,fills=int(runner.fill_count.sum())))
            print(measurements[-1],flush=True)
            count=int(runner.fill_count.max())
            saved=dict(ledger=runner.ledger[:,:count].detach().cpu().clone(),counts=runner.fill_count.detach().cpu().clone())
            if previous is not None and any(not torch.equal(saved[k],previous[k]) for k in saved):
                raise ValueError('Profiling repeats changed actual fill receipts')
            previous=saved
        torch.save(previous,root/'fills.pt')
        serial=profile_metrics(metrics)
        full_session=a.seconds==len(inputs.arrays['clocks'])
        financial_audit_sha256=audit_profile(root,serial,full_session=full_session)
        receipt=dict(status='complete',version='v6-sparse-profile-v1',code_sha256=code_hash(),day=a.day,arguments={k:str(v) if isinstance(v,Path) else v for k,v in vars(a).items()},
            input_receipt_sha256=file_hash(a.inputs/a.day/'complete.json'),population_sha256=file_hash(root/'population.json'),ledger_sha256=file_hash(root/'fills.pt'),
            structural_receipt_sha256=runner.tape.provenance.get('structural_receipt_sha256'),
            source_population_sha256=file_hash(a.population_file) if a.population_file is not None else None,
            backend=a.backend,device=a.device,cpu_threads=a.cpu_threads if a.device=='cpu' else None,
            load_seconds=load_seconds,rule_seconds=rule_seconds,setup_seconds=setup,input_bytes=inputs.bytes,
            daily_union_listings=len(union),broker_slots=runner.n,broker=a.broker,measurements=measurements,metrics=serial,validation_opened=False,optimization_started=False,
            full_session=full_session,financial_audit_sha256=financial_audit_sha256,
            repeated_fill_receipts_exact=a.repeats>1,
            limitations=[*(['Daily-union broker state baseline'] if a.broker=='daily-union' else ['Compact holding capacity is bounded; overflow invalidates results']),*(['Percentage targets only; structural sidecar unqualified'] if a.structure is None else []),
                         *(['Partial-session timing is not profitability evidence'] if not full_session else []),
                         'Training-session profiling is not out-of-sample evidence'])
        write_json(root/'receipt.json',receipt)
        write_json(root/'status.json',dict(status='complete',stage='Exact fill and financial audits passed',validation_opened=False))
        print('Profiling complete; no optimization or validation performed',flush=True)


if __name__=='__main__':main()
