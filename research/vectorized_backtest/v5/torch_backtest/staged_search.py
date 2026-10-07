"""Explicit immutable staged search. Training-only until finalist audit and freeze."""
import os
os.environ['PYTHONDONTWRITEBYTECODE']='1'
import argparse
import json
import time
from dataclasses import asdict
from pathlib import Path
import numpy as np
import torch
from .runtime import require_runtime, write_json, code_hash, file_hash, configure_caches
from .offline_data import load_session, preflight
from .session_prefetch import SessionPrefetch
from .genome import StrategySpace
from .stability import Objective
from .run_search import state, restore, fingerprint, clean, metric_summary
from .staged import Stage, validate_schedule, balanced_panels, migrate, objective_matrix
from .evolution import sample
from .search_operands import searchable_features
from .batched import BatchedEvaluator


def evaluate_panel(sessions, population, evaluator, folder, emit):
    receipts, results = [], []
    with SessionPrefetch(sessions, lambda s: load_session(s, isolated_banks=True)) as prefetch:
        for index, session in enumerate(sessions):
            emit(completed_sessions=index, focus=session['day'], selected_days=[s['day'] for s in sessions])
            receipt = evaluator.evaluate(session, population, folder/f'session_{index:03d}', prefetch.take(index), emit)
            path = folder/f'session_{index:03d}'/'receipt.json'
            receipts.append(dict(path=str(path), sha256=file_hash(path)))
            results.append(receipt['metrics'])
            emit(completed_sessions=index+1, timing=receipt['timing'],session_completed_timing=receipt['timing'])
    return results, receipts


def rank_valid(scored):
    return sorted((i for i, valid in enumerate(scored['feasible'].tolist()) if valid),
                  key=lambda i: (-float(scored['score'][i]), i))


def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--sessions',type=Path,required=True);p.add_argument('--output',type=Path,required=True)
    p.add_argument('--schedule',type=Path,required=True);p.add_argument('--execute',action='store_true');p.add_argument('--resume',action='store_true')
    p.add_argument('--qualification',type=Path);p.add_argument('--seed',type=int,default=20261005)
    p.add_argument('--batch-size',type=int,default=128);p.add_argument('--device',choices=('cuda','cpu'),default='cuda')
    p.add_argument('--backend',choices=('eager','compiled_graph'),default='compiled_graph')
    p.add_argument('--ticker-capacity',type=int,default=2368);p.add_argument('--graph-steps',type=int,default=32)
    p.add_argument('--chunk-candles',type=int,default=4096);p.add_argument('--maximum-fills',type=int,default=65536)
    p.add_argument('--maximum-tape-gib',type=float,default=12);p.add_argument('--feature-gib',type=float,default=24)
    p.add_argument('--maximum-state-gib',type=float,default=16);p.add_argument('--maximum-gate-gib',type=float,default=32)
    args=p.parse_args(argv)
    if not 1<=args.batch_size<=1024: p.error('GPU batch size must be 1..1024')
    spec=json.loads(args.sessions.read_text());preflight(spec)
    stages=validate_schedule([Stage(**s) for s in json.loads(args.schedule.read_text())])
    output=require_runtime(args.output);objective=Objective().validate();space=StrategySpace()
    features=searchable_features(spec['training'],'premarket')
    identity=dict(version='v5-staged-v1',code_hash=code_hash(),sessions_sha256=file_hash(args.sessions),
                  sessions=spec,financial_settings=asdict(space.settings),searchable_features=list(features) if features is not None else None,
                  schedule=[asdict(s) for s in stages],objective=asdict(objective),
                  arguments={k:str(v) if isinstance(v,Path) else v for k,v in vars(args).items() if k not in ('execute','resume','output','qualification')})
    if not args.execute:
        write_json(output/'plan.json',identity);return 0
    if not args.qualification:raise ValueError('Measured same-source qualification required before full search')
    qualification=json.loads(args.qualification.read_text())
    if (qualification.get('status')!='passed' or qualification.get('code_hash')!=code_hash() or
            qualification.get('sessions_sha256')!=file_hash(args.sessions) or qualification.get('batch_size')!=args.batch_size):
        raise ValueError('Qualification identity mismatch')
    identity_path=output/'identity.json'
    if identity_path.exists():
        if not args.resume or json.loads(identity_path.read_text())!=identity:raise ValueError('Exact immutable resume required')
    elif args.resume:raise ValueError('Resume identity missing')
    else:write_json(identity_path,identity)
    if (output/'frozen_winner.json').exists():raise ValueError('Already frozen; no repeated selection or validation')
    configure_caches(output);torch.set_num_threads(1)
    lock=output/'owner.lock';fd=os.open(lock,os.O_CREAT|os.O_EXCL|os.O_WRONLY)
    os.write(fd,json.dumps(dict(pid=os.getpid(),code_hash=code_hash())).encode());os.close(fd)
    rng=np.random.default_rng(args.seed);checkpoint=output/'checkpoint.json'
    saved=json.loads(checkpoint.read_text()) if checkpoint.exists() else None
    status=dict(status='training',version='V5',started_epoch=time.time(),validation_status='SEALED',completed_generations=0,
                config=dict(generations=stages[-1].end_generation,population=stages[0].population,training_sessions=stages[0].sessions),objective=asdict(objective))
    def emit(**event):
        if 'session_completed_timing' in event:
            values=event.pop('session_completed_timing');status['timed_sessions']=status.get('timed_sessions',0)+1
            totals=status.setdefault('timing_totals',{})
            for key,value in values.items():totals[key]=totals.get(key,0.)+value
            event['average_timing']={key:value/status['timed_sessions'] for key,value in totals.items()}
        previous=status.get('stage');status.update(event,updated_epoch=time.time(),worker_pid=os.getpid())
        if event.get('stage') and event['stage']!=previous:
            from datetime import datetime,timezone
            message=dict(timestamp=datetime.now(timezone.utc).strftime('%H:%M:%S UTC'),text=event['stage'])
            with (output/'events.jsonl').open('a') as f:f.write(json.dumps(message)+'\n')
            status['messages']=(status.get('messages',[])+[message])[-100:]
        if args.device=='cuda':status['gpu_gib']=torch.cuda.memory_allocated()/1024**3
        write_json(output/'status.json',clean(status))
    evaluator=BatchedEvaluator(space,args)
    try:
        if saved:
            rng.bit_generator.state=saved['rng'];population=[restore(v) for v in saved['population']]
            stage_index=saved['stage_index'];generation=saved['generation'];archive=saved['archive'];panels=saved['panels']
        else:
            stage_index=0;generation=0;archive={}
            panels=balanced_panels(rng,stages[0].end_generation,stages[0].sessions,len(spec['training']))
            population=sample(rng,space,stages[0].population,features)
        def save():
            write_json(checkpoint,dict(stage_index=stage_index,generation=generation,archive=archive,panels=panels,
                                       population=[state(v) for v in population],rng=rng.bit_generator.state))
        save()
        while stage_index<len(stages):
            stage=stages[stage_index];stage_start=0 if stage_index==0 else stages[stage_index-1].end_generation
            while generation<stage.end_generation:
                # Save RNG/population before every panel, so partial replay is exact.
                save()
                selected=[spec['training'][i] for i in panels[generation-stage_start]]
                emit(stage='Search panel',completed_generations=generation,evaluation_basis=f'{stage.sessions}-day search ranking',
                     config=dict(generations=stages[-1].end_generation,population=len(population),training_sessions=len(selected)))
                folder=require_runtime(output/f'generation_{generation:03d}')
                results,receipts=evaluate_panel(selected,population,evaluator,folder,emit)
                scored=objective_matrix(results,population,objective);rank=rank_valid(scored)
                if not rank:raise ValueError('All strategies invalid under crucial constraints')
                leaders=[dict(rank=j+1,score=float(scored['score'][i]),metrics=metric_summary(results,scored,i,population)) for j,i in enumerate(rank[:3])]
                emit(top_strategies=leaders,best_score=leaders[0]['score'],best_metrics=leaders[0]['metrics'],feasible_candidates=len(rank))
                write_json(folder/'generation.json',clean(dict(population=[state(v) for v in population],population_sha256=fingerprint([state(v) for v in population]),
                                                              receipts=receipts,scores=scored,selected_days=[s['day'] for s in selected])))
                top=rank[:stage.archive_top];others=[i for i in rank if i not in top]
                random=rng.choice(others,min(stage.archive_random,len(others)),replace=False).tolist()
                for i in top+random:
                    value=state(population[i]);archive[fingerprint(value)]=value
                population=migrate(rng,population,rank,stage.population,space,features)
                generation+=1;save();emit(completed_generations=generation)
                if (output/'STOP').exists():emit(status='interrupted',stage='Stopped at durable generation boundary');return 130
            finalists=[restore(value) for _,value in sorted(archive.items())]
            emit(stage='Full-training checkpoint',evaluation_basis='30-day checkpoint ranking',
                 config=dict(generations=stages[-1].end_generation,population=len(finalists),training_sessions=30))
            folder=require_runtime(output/f'checkpoint_stage_{stage_index:02d}')
            results,receipts=evaluate_panel(spec['training'],finalists,evaluator,folder,emit)
            scored=objective_matrix(results,finalists,objective);rank=rank_valid(scored)
            if not rank:raise ValueError('No valid full-training finalist')
            write_json(folder/'ranking.json',clean(dict(population=[state(v) for v in finalists],population_sha256=fingerprint([state(v) for v in finalists]),
                                                       receipts=receipts,scores=scored)))
            leaders=[dict(rank=j+1,score=float(scored['score'][i]),metrics=metric_summary(results,scored,i,finalists)) for j,i in enumerate(rank[:3])]
            emit(top_strategies=leaders,best_score=leaders[0]['score'],best_metrics=leaders[0]['metrics'])
            if stage_index==len(stages)-1:
                # No validation is opened here. Independent ledger audit is required to authorize freeze.
                write_json(output/'finalist.json',dict(winner=state(finalists[rank[0]]),score=leaders[0]['score'],metrics=leaders[0]['metrics'],
                           identity_sha256=file_hash(identity_path),ranking_sha256=file_hash(folder/'ranking.json')))
                emit(status='awaiting_finalist_audit',stage='Full-training finalist ready; audit required before freeze');return 0
            stage_index+=1;next_stage=stages[stage_index]
            population=migrate(rng,finalists,rank,next_stage.population,space,features)
            archive={fingerprint(state(finalists[i])):state(finalists[i]) for i in rank[:next_stage.archive_top]}
            panels=balanced_panels(rng,next_stage.end_generation-generation,next_stage.sessions,len(spec['training']))
            save()
        return 0
    except BaseException as error:
        emit(status='interrupted' if isinstance(error,(InterruptedError,KeyboardInterrupt)) else 'failed',error=str(error))
        if isinstance(error,InterruptedError):return 130
        raise
    finally:
        evaluator.close();lock.unlink(missing_ok=True)


if __name__=='__main__':raise SystemExit(main())
