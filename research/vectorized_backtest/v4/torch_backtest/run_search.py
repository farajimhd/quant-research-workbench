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

def evaluate_session(spec,population,space,args,output,emit,cache=None):
    began=time.perf_counter();tape,bank,prior,identities,receipt=load_session(spec)
    load=time.perf_counter()-began;emit(stage='Transfer certified inputs',focus=spec['day'],timing=dict(load=load),active_session=None)
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
    timing=dict(load=load,transfer=transfer,rule_prepare=rule_seconds,compile=compiled,replay=result['replay_seconds'],end_to_end=time.perf_counter()-began)
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


def missing_validation_inputs(sessions):
    """After freeze only: inspect publication paths, never market contents."""
    missing=[]
    for item in sessions:
        paths=[Path(item['execution_root'])/'receipt.json',Path(item['execution_root'])/'tape.pt',
               Path(item['feature_root'])/'complete.json',Path(item['identity_map']),Path(item['split_certificate'])]
        if item.get('previous_feature_root'):
            paths.extend([Path(item['previous_feature_root'])/'complete.json',Path(item['previous_split_certificate'])])
        missing.extend(dict(day=item['day'],path=str(path)) for path in paths if not path.is_file())
    return missing

def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--sessions',type=Path,required=True);parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--execute',action='store_true');parser.add_argument('--resume',action='store_true');parser.add_argument('--profile',action='store_true')
    parser.add_argument('--device',choices=('cpu','cuda'),default='cuda');parser.add_argument('--backend',choices=('eager','compile','compiled_graph'),default='compiled_graph')
    parser.add_argument('--population',type=int,default=128);parser.add_argument('--generations',type=int,default=32);parser.add_argument('--seed',type=int,default=20261005)
    parser.add_argument('--feature-gib',type=float,default=24);parser.add_argument('--maximum-tape-gib',type=float,default=12);parser.add_argument('--maximum-state-gib',type=float,default=8)
    parser.add_argument('--maximum-fills',type=int,default=65536);parser.add_argument('--graph-steps',type=int,default=32);parser.add_argument('--chunk-candles',type=int,default=4096)
    parser.add_argument('--ticker-capacity',type=int,help='Fixed inactive-padded training axis; choose from all30 certified session widths for graph reuse')
    parser.add_argument('--qualification',type=Path)
    parser.add_argument('--operand-session',choices=('all','premarket'),default='all',help='Premarket removes session-regime flags from searchable operands; banks remain intact')
    args=parser.parse_args(argv)
    if args.population<4 or args.generations<1 or args.chunk_candles<120:parser.error('Invalid bounded search budget')
    if args.ticker_capacity is not None and args.ticker_capacity<1:parser.error('Positive shared ticker capacity required')
    spec=json.loads(args.sessions.read_text(encoding='utf-8'));split=preflight(spec,profile=args.profile)
    from .search_operands import searchable_features
    features=searchable_features(spec['training'],args.operand_session)
    output=require_runtime(args.output);objective=Objective().validate();space=StrategySpace()
    identity=dict(version='v4-variable-rulesets-v1',code_hash=code_hash(),sessions=spec,split=split,objective=asdict(objective),financial_settings=asdict(space.settings),features=[asdict(f) for f in CATALOG],
        searchable_feature_indices=list(features),policy_coordinates=list(range(4,50)),program_maximum_nodes=32,stages=list(STAGES),arguments={k:str(v) if isinstance(v,Path) else v for k,v in vars(args).items() if k not in ('resume','execute','output','qualification')})
    if not args.execute:write_json(output/'plan.json',identity);print(str(output/'plan.json'));return 0
    if args.device=='cuda' and not torch.cuda.is_available():raise RuntimeError('CUDA required; no fallback')
    if not args.profile:
        if args.qualification is None:raise ValueError('Full search requires a passed same-source workstation qualification')
        qualification=json.loads(args.qualification.read_text())
        if qualification.get('status')!='passed' or qualification.get('code_hash')!=code_hash() or qualification.get('population')!=args.population or qualification.get('sessions_sha256')!=file_hash(args.sessions):
            raise ValueError('Qualification source/population/session identity mismatch')
    configure_caches(output);torch.set_num_threads(1)
    identity_path=output/'identity.json'
    if (output/'report.json').exists():raise ValueError('Completed search/evaluation is immutable; do not rerun')
    if identity_path.exists():
        if not args.resume or json.loads(identity_path.read_text())!=identity:raise ValueError('Existing experiment requires exact immutable resume')
    elif args.resume:raise ValueError('Resume identity is missing')
    else:write_json(identity_path,identity)
    # Exclusive live controller. A stale lock is never removed automatically.
    lock=output/'owner.lock';owner=os.open(lock,os.O_CREAT|os.O_EXCL|os.O_WRONLY)
    os.write(owner,json.dumps(dict(pid=os.getpid(),started=time.time(),code_hash=code_hash())).encode());os.close(owner)
    status=dict(status='preflight',started_epoch=time.time(),mode='profile' if args.profile else 'optimization',objective=asdict(objective),
        config=dict(population=args.population,generations=0 if args.profile else args.generations,
                    training_sessions=1 if args.profile else 30,validation_sessions=6),
        completed_generations=0,completed_sessions=0,validation_status='SEALED',worker_pid=os.getpid())
    def emit(**event):
        from datetime import datetime,timezone
        changed=any(key in event and event[key]!=status.get(key) for key in ('stage','status','error','completed_generations','completed_sessions'))
        if changed:
            message=dict(timestamp=datetime.now(timezone.utc).strftime('%H:%M:%S UTC'),
                         text=event.get('error') or event.get('stage') or f"Completed sessions {event.get('completed_sessions',status.get('completed_sessions',0))}; generations {event.get('completed_generations',status.get('completed_generations',0))}")
            with (output/'events.jsonl').open('a',encoding='utf-8') as stream:stream.write(json.dumps(message)+'\n')
            status['messages']=(status.get('messages',[])+[message])[-100:]
        status.update(event,updated_epoch=time.time())
        if args.device=='cuda':status['gpu_gib']=torch.cuda.memory_allocated()/1024**3
        write_json(output/'status.json',clean(status))
    checkpoint=output/'checkpoint.json';rng=np.random.default_rng(args.seed);runner_cache={}
    saved=json.loads(checkpoint.read_text()) if checkpoint.exists() else None
    if saved:
        population=[restore(v) for v in saved['population']];rng.bit_generator.state=saved['rng'];start=saved['next_generation'];winner=saved['winner'];best=saved['best_score'];best_metrics=saved['best_metrics']
    else:population=sample(rng,space,args.population,features);start=0;winner=None;best=None;best_metrics=None
    timing_totals={};timing_count=0
    try:
        for generation in range(start,1 if args.profile else args.generations):
            emit(status='profiling' if args.profile else 'training',completed_generations=generation,completed_sessions=0,stage='Load certified inputs',best_score=best,best_metrics=best_metrics)
            folder=require_runtime(output/f'generation_{generation:03d}');pop_hash=fingerprint([state(v) for v in population]);results=[];receipts=[]
            for index,session in enumerate(spec['training'][:1] if args.profile else spec['training']):
                destination=require_runtime(folder/f'session_{index:03d}');path=destination/'receipt.json'
                if path.exists():
                    receipt=json.loads(path.read_text())
                    if receipt['population_sha256']!=pop_hash or receipt['day']!=session['day'] or file_hash(destination/'fills.pt')!=receipt['ledger_sha256']:raise ValueError('Session receipt/population/ledger mismatch')
                    # Revalidate current immutable inputs before reusing replay.
                    *_,fresh=load_session(session)
                    for key in ('execution','feature_certificate','prior_certificate','identity_map_sha256','split_certificate_sha256','previous_split_certificate_sha256'):
                        if fresh[key]!=receipt[key]:raise ValueError('Resumed session source changed')
                else:
                    def session_emit(**event):
                        if event.get('stage')=='Transfer certified inputs':event['prepared_sessions']=index+1
                        if 'focus' in event:
                            prefix=f'B{args.population} session profile | ' if args.profile else f'Generation {generation+1}/{args.generations} | session {index+1}/30 | '
                            event['focus']=prefix+event['focus']
                        emit(**event)
                    receipt=evaluate_session(session,population,space,args,destination,session_emit,runner_cache);write_json(path,receipt)
                receipts.append(dict(path=str(path),sha256=file_hash(path)));results.append(receipt['metrics'])
                timing_count+=1
                for key,value in receipt['timing'].items():timing_totals[key]=timing_totals.get(key,0.)+value
                averages={key:value/timing_count for key,value in timing_totals.items()}
                remaining=(args.generations-generation-1)*30+30-index-1
                emit(completed_sessions=index+1,timing=receipt['timing'],average_timing=averages,timed_sessions=timing_count,
                     campaign_eta=None if args.profile else remaining*averages.get('end_to_end',0.))
                if (output/'STOP').exists():
                    write_json(checkpoint,dict(next_generation=generation,population=[state(v) for v in population],rng=rng.bit_generator.state,winner=winner,best_score=best,best_metrics=best_metrics))
                    emit(status='interrupted',stage='Stopped at durable session boundary');return 130
            if args.profile:
                write_json(output/'profile.json',dict(code_hash=code_hash(),population=args.population,sessions_sha256=file_hash(args.sessions),receipts=receipts,timing=receipt['timing'],status='profile_complete_not_qualification'))
                emit(status='profile_complete',stage='Profile retained; qualification still required');return 0
            def matrix(name):return torch.tensor([r[name] for r in results],dtype=torch.float64)
            complexity=torch.tensor([sum(p.validate(CATALOG)['active_nodes'] for p in v.programs().values()) for v in population],dtype=torch.float64)
            scored=score(matrix('net_pnl'),matrix('drawdown'),matrix('stop_risk_dollar_seconds'),matrix('capital_dollar_seconds'),matrix('filled_batches'),matrix('terminal_valid'),complexity,config=objective)
            values=scored['score'].tolist();feasible=scored['feasible'].tolist();violations=scored['violation'].tolist()
            rank=sorted(range(args.population),key=lambda i:(feasible[i],-violations[i],values[i]),reverse=True)
            top=rank[0]
            closest_metrics=metric_summary(results,scored,top,population)
            if feasible[top] and (best is None or values[top]>best):
                best=values[top];winner=state(population[top]);best_metrics=metric_summary(results,scored,top,population)
            write_json(folder/'generation.json',clean(dict(population_sha256=pop_hash,receipts=receipts,scores=scored,population=[state(v) for v in population])))
            next_population=[population[i] for i in rank[:2]]
            while len(next_population)<args.population:
                if rng.random()<.2:next_population.extend(sample(rng,space,1))
                else:
                    rivals=rng.choice(args.population,3,replace=False);parent=max(rivals,key=lambda i:(feasible[i],-violations[i],values[i]));next_population.append(mutate(rng,population[int(parent)],space,features))
            population=next_population
            write_json(checkpoint,dict(next_generation=generation+1,population=[state(v) for v in population],rng=rng.bit_generator.state,winner=winner,best_score=best,best_metrics=best_metrics))
            emit(completed_generations=generation+1,feasible_candidates=sum(feasible),best_score=best,best_metrics=best_metrics,closest_score=values[top],closest_metrics=closest_metrics,closest_violation=violations[top],rejection_counts={k:clean(v) for k,v in scored['violations'].items()})
        if winner is None:
            emit(status='no_feasible_winner',stage='Budget exhausted; validation remains sealed');write_json(output/'report.json',dict(status='no_feasible_winner',validation_opened=False));return 2
        frozen=output/'frozen_winner.json'
        freeze=dict(winner=winner,criterion=asdict(objective),score=best,metrics=best_metrics,identity_sha256=file_hash(identity_path),frozen_epoch=time.time())
        if frozen.exists():freeze=json.loads(frozen.read_text());winner=freeze['winner']
        else:write_json(frozen,freeze)
        # Independent full-training arithmetic and winner audit authorizes the
        # separate producers. No validation files are opened by that audit yet.
        from .audit import audit
        audit(output)
        freeze_hash=file_hash(frozen)
        missing=missing_validation_inputs(spec['validation'])
        if missing:
            write_json(output/'validation_inputs_pending.json',dict(freeze_sha256=freeze_hash,missing=missing))
            emit(status='awaiting_validation_inputs',stage='Frozen winner; certified final inputs required',
                 validation_status='FROZEN; evaluation not started',waiting_reason=f'{len(missing)} unpublished input artifacts; prepare with this audited frozen winner, then exact resume')
            return 3
        emit(status='validation',stage='Evaluate frozen default/winner once',validation_status='FROZEN WINNER; evaluation active')
        # Training capacity is learned from training inputs only. Final sessions
        # choose their own complete width after freeze, without earlier reads.
        validation_args=argparse.Namespace(**vars(args));validation_args.ticker_capacity=None
        default=sample(np.random.default_rng(0),space,1)[0]
        # Baseline is a documented fixed valid-price entry and management policy,
        # not a selected/random strategy.
        default.policy=space.default.tolist()
        default.clauses={s:[([Node(0,feature=35) if s in ('entry','trail') else Node(1,value=0,unit='bool')],0)] for s in STAGES};default.connectors={s:[] for s in STAGES}
        validation=[]
        for index,session in enumerate(spec['validation']):
            folder=require_runtime(output/f'validation_{index:03d}');path=folder/'receipt.json'
            if path.exists():
                receipt=json.loads(path.read_text())
                if receipt.get('freeze_sha256')!=freeze_hash or file_hash(folder/'fills.pt')!=receipt['ledger_sha256']:raise ValueError('Frozen evaluation receipt changed')
                *_,fresh=load_session(session)
                for key in ('execution','feature_certificate','prior_certificate','identity_map_sha256','split_certificate_sha256','previous_split_certificate_sha256'):
                    if fresh[key]!=receipt[key]:raise ValueError('Frozen evaluation input changed')
            else:
                receipt=evaluate_session(session,[default,restore(winner)],space,validation_args,folder,emit,runner_cache);receipt['freeze_sha256']=freeze_hash;receipt['evaluation_epoch']=time.time();write_json(path,receipt)
            validation.append(receipt)
        write_json(output/'report.json',clean(dict(status='completed',freeze_sha256=freeze_hash,training=best_metrics,validation=validation,metrics=financial_metrics([r['metrics'] for r in validation]),validation_tuning=False)))
        emit(status='completed',stage='Frozen evaluation complete; audit/delivery pending',validation_status='Evaluated once')
        return 0
    except BaseException as error:
        import traceback
        write_json(output/'error.json',dict(type=type(error).__name__,message=str(error),traceback=traceback.format_exc()))
        emit(status='failed',error=str(error));raise
    finally:
        lock.unlink(missing_ok=True)

if __name__=='__main__':raise SystemExit(main())

