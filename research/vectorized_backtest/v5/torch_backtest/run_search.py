"""Offline, immutable V4 search. No SQL, source rebuilding or label access."""
import os
os.environ['PYTHONDONTWRITEBYTECODE']='1'
import argparse,json,math,time,gc
from dataclasses import asdict
from pathlib import Path
import numpy as np
import torch
from .runtime import require_runtime,write_json,code_hash,file_hash,configure_caches
from .feature_bank import CATALOG
from .program import Node,Program
from .genome import StrategySpace
from .evolution import Individual,STAGES,sample,mutate
from .offline_data import load_session,preflight
from .gate_compiler import FeatureResident
from .program_runner import ProgramRunner
from .stability import Objective,score
from .metrics import financial_metrics

def state(individual):
    return dict(policy=individual.policy,clauses={s:[dict(nodes=[asdict(n) for n in chunk],root=root) for chunk,root in individual.clauses[s]] for s in STAGES},connectors=individual.connectors)

def restore(value):
    return Individual(value['policy'],{s:[([Node(**n) for n in c['nodes']],c['root']) for c in value['clauses'][s]] for s in STAGES},value['connectors'])

def fingerprint(value):
    from hashlib import sha256
    return sha256(json.dumps(value,sort_keys=True,allow_nan=False,separators=(',',':')).encode()).hexdigest()

def seal_ledger(path,ledger,counts):
    """Crash recovery must reconcile valid fills before reusing their hash."""
    counts=counts.detach().cpu();ledger=ledger.detach().cpu()
    if path.exists():
        saved=torch.load(path,map_location='cpu',weights_only=True)
        if (set(saved)!= {'ledger','counts'} or saved['ledger'].shape!=ledger.shape
                or saved['ledger'].dtype!=ledger.dtype or not torch.equal(saved['counts'],counts)):
            raise ValueError('Existing financial ledger identity/count mismatch')
        for lane,count in enumerate(counts.tolist()):
            if not torch.equal(saved['ledger'][lane,:count],ledger[lane,:count]):
                raise ValueError('Existing financial ledger fill mismatch')
    else:
        from uuid import uuid4
        temporary=path.with_name(path.name+'.'+uuid4().hex+'.tmp')
        torch.save(dict(ledger=ledger,counts=counts),temporary)
        temporary.replace(path)
    return file_hash(path)

def clean(value):
    if isinstance(value,torch.Tensor):return clean(value.detach().cpu().tolist())
    if isinstance(value,float) and not math.isfinite(value):return None
    if isinstance(value,dict):return {k:clean(v) for k,v in value.items()}
    if isinstance(value,(list,tuple)):return [clean(v) for v in value]
    return value

def evaluate_session(spec,population,space,args,output,emit,cache=None,prepared=None):
    began=time.perf_counter()
    if prepared is None:
        tape,bank,prior,identities,receipt=load_session(spec)
        load=time.perf_counter()-began;prefetch_wait=load
    else:
        loaded,load,prefetch_wait=prepared
        tape,bank,prior,identities,receipt=loaded
    emit(stage='Transfer certified inputs',focus=spec['day'],timing=dict(load=load,prefetch_wait=prefetch_wait),active_session=None)
    transfer=time.perf_counter()
    from .session_pool import padded_tape,bind_tape
    capacity=getattr(args,'ticker_capacity',None) or ((len(tape.tickers)+63)//64)*64
    if capacity<len(tape.tickers):raise ValueError('Shared ticker capacity cannot truncate a certified session')
    # Check the shared allocation before creating device tensors. Padding
    # appends inactive listings only; account arithmetic remains unchanged.
    padded_bytes=tape.bytes+(capacity-len(tape.tickers))*(tape.bytes-tape.clocks.numel()*tape.clocks.element_size())/len(tape.tickers)
    if padded_bytes>args.maximum_tape_gib*1024**3:raise MemoryError('Padded broker tape exceeds declared budget')
    tape=padded_tape(tape,capacity,args.device)
    if tape.bytes>args.maximum_tape_gib*1024**3:raise MemoryError('Padded broker tape exceeds declared budget')
    resident=FeatureResident(bank,identities,previous=prior,device=args.device,maximum_gib=args.feature_gib,start_us=int(tape.clocks[0])*1_000_000,end_us=int(tape.clocks[-1])*1_000_000)
    transfer=time.perf_counter()-transfer
    emit(timing=dict(load=load,transfer=transfer))
    gates,rule_seconds=resident.compile(population,tape,chunk_candles=args.chunk_candles,emit=lambda e:emit(**e))
    del resident;gc.collect()
    key=(len(tape.clocks),capacity,len(population),tuple(tape.level_lower.shape),tape.structural_targets is not None)
    compiled=0.
    if cache is not None and key in cache:
        runner=cache[key];bind_tape(runner.tape,tape)
        runner.start_boundary.fill_(int(tape.provenance.get('start_second',int(tape.clocks[0]))));runner.end_boundary.copy_(tape.clocks[-1])
        runner.set_population(population,gates)
    else:
        if cache is not None:cache.clear();gc.collect()
        runner=ProgramRunner(tape,space,population,gates,backend=args.backend,maximum_fills=args.maximum_fills,maximum_state_gib=args.maximum_state_gib,graph_steps=args.graph_steps)
        emit(stage='Compile financial replay',timing=dict(load=load,transfer=transfer,rule_prepare=rule_seconds));runner.compile();compiled=runner.setup_seconds
        if cache is not None:cache[key]=runner
    replay_started=time.perf_counter()
    emit(stage='Backtest',focus=spec['day'],replay_started_epoch=time.time(),timing=dict(load=load,transfer=transfer,rule_prepare=rule_seconds,compile=compiled))
    def replay_progress(progress):
        elapsed=time.perf_counter()-replay_started;done=progress['completed_seconds'];total=progress['total_seconds']
        emit(progress=progress,stage='Backtest',active_session=runner.live_metrics(),replay_elapsed=elapsed,
             replay_rate=done/elapsed if elapsed else None,replay_eta=(total-done)*elapsed/done if done else None)
    result=runner.run(progress=replay_progress)
    emit(active_session=runner.live_metrics(),progress=dict(completed_seconds=len(tape.clocks),total_seconds=len(tape.clocks)),replay_eta=0.)
    metrics={k:clean(v) for k,v in result.items() if isinstance(v,torch.Tensor) or k=='closed_position_duration_samples'}
    ledger=runner.ledger[:,:int(runner.fill_count.max().item())].detach().cpu()
    ledger_path=output/'fills.pt'
    ledger_hash=seal_ledger(ledger_path,ledger,runner.fill_count)
    timing=dict(load=load,prefetch_wait=prefetch_wait,transfer=transfer,rule_prepare=rule_seconds,compile=compiled,replay=result['replay_seconds'],end_to_end=time.perf_counter()-began+prefetch_wait if prepared is not None else time.perf_counter()-began)
    receipt.update(metrics=metrics,timing=timing,ledger_sha256=ledger_hash,population_sha256=fingerprint([state(v) for v in population]))
    del runner,gates,tape
    if args.device=='cuda':torch.cuda.empty_cache()
    return receipt

def metric_summary(results,stability,lane,population):
    summary={k:clean(v[lane] if isinstance(v,torch.Tensor) else v) for k,v in financial_metrics(results).items()}
    for k in ('median_return','ex_best_return','profitable_day_fraction','tail_loss','best_day_pnl','other_days_pnl'):summary[k]=clean(stability[k][lane])
    summary['objective_components']={k:clean(v[lane]) for k,v in stability['components'].items()}
    summary['active_nodes']=sum(p.validate(CATALOG)['active_nodes'] for p in population[lane].programs().values())
    return summary

