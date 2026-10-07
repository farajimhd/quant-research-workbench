"""Training-only population/batch timing sweep. Partial runs are never qualification."""
import os
os.environ['PYTHONDONTWRITEBYTECODE']='1'
import argparse,json,time
from pathlib import Path
import numpy as np
import torch
from .runtime import require_runtime,configure_caches,write_json,code_hash,file_hash
from .offline_data import preflight,load_session
from .genome import StrategySpace
from .evolution import sample
from .search_operands import searchable_features
from .session_prefetch import SessionPrefetch
from .batched import BatchedEvaluator
from .run_search import clean,state,fingerprint


def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--sessions',type=Path,required=True);p.add_argument('--output',type=Path,required=True)
    p.add_argument('--populations',type=int,nargs='+',default=[4096,8192])
    p.add_argument('--batch-sizes',type=int,nargs='+',default=[128,256,512])
    p.add_argument('--session-count',type=int,default=2)
    p.add_argument('--profile-seconds',type=int,default=256,help='Prefix replay timestamps; 19800 measures full premarket')
    p.add_argument('--seed',type=int,default=20261005)
    p.add_argument('--device',choices=('cpu','cuda'),default='cuda');p.add_argument('--backend',choices=('eager','compiled_graph'),default='compiled_graph')
    p.add_argument('--ticker-capacity',type=int,default=2368);p.add_argument('--graph-steps',type=int,default=32)
    p.add_argument('--maximum-fills',type=int,default=65536);p.add_argument('--maximum-state-gib',type=float,default=20)
    p.add_argument('--maximum-gate-gib',type=float,default=32);p.add_argument('--maximum-tape-gib',type=float,default=12)
    p.add_argument('--feature-gib',type=float,default=24);p.add_argument('--chunk-candles',type=int,default=4096)
    args=p.parse_args(argv)
    if not 1<=args.session_count<=30 or not 32<=args.profile_seconds<=19800 or any(n<10 for n in args.populations) or any(not 1<=n<=1024 for n in args.batch_sizes):
        p.error('Invalid bounded profiling budget')
    spec=json.loads(args.sessions.read_text());preflight(spec,profile=True)
    output=require_runtime(args.output);configure_caches(output);torch.set_num_threads(1)
    identity=dict(version='v5-profile',code_hash=code_hash(),sessions_sha256=file_hash(args.sessions),arguments={k:str(v) if isinstance(v,Path) else v for k,v in vars(args).items()})
    if (output/'identity.json').exists():raise ValueError('Profile output immutable; use fresh directory')
    write_json(output/'identity.json',identity)
    lock=output/'owner.lock';fd=os.open(lock,os.O_CREAT|os.O_EXCL|os.O_WRONLY);os.write(fd,str(os.getpid()).encode());os.close(fd)
    status=dict(version='V5',mode='profile',status='profiling',started_epoch=time.time(),validation_status='SEALED',
                config=dict(generations=0,population=0,training_sessions=args.session_count),completed_sessions=0)
    def emit(**event):
        old=status.get('stage');status.update(event,updated_epoch=time.time(),worker_pid=os.getpid())
        if event.get('stage') and old!=event['stage']:
            from datetime import datetime,timezone
            message=dict(timestamp=datetime.now(timezone.utc).strftime('%H:%M:%S UTC'),text=event['stage'])
            status['messages']=(status.get('messages',[])+[message])[-100:]
            with (output/'events.jsonl').open('a') as f:f.write(json.dumps(message)+'\n')
        if args.device=='cuda':status['gpu_gib']=torch.cuda.memory_allocated()/1024**3
        write_json(output/'status.json',clean(status))
    rows=[];space=StrategySpace();features=searchable_features(spec['training'],'premarket')
    timing_totals={};timing_count=0
    sessions=spec['training'][:args.session_count]
    try:
        for size in args.populations:
            population=sample(np.random.default_rng(args.seed),space,size,features)
            for batch_size in args.batch_sizes:
                args.batch_size=batch_size;evaluator=BatchedEvaluator(space,args)
                job=require_runtime(output/f'population_{size}_batch_{batch_size}')
                emit(config=dict(generations=0,population=size,training_sessions=len(sessions)),focus=f'B{size} / GPU batch {batch_size}',completed_sessions=0)
                began=time.perf_counter()
                if args.device=='cuda':torch.cuda.reset_peak_memory_stats()
                receipts=[]
                try:
                    with SessionPrefetch(sessions,lambda s:load_session(s,isolated_banks=True)) as prefetch:
                        for index,session in enumerate(sessions):
                            emit(focus=f'B{size} / batch {batch_size} / {session["day"]}')
                            receipt=evaluator.evaluate(session,population,job/f'session_{index:03d}',prefetch.take(index),emit)
                            receipts.append(receipt)
                            timing_count+=1
                            for key,value in receipt['timing'].items():timing_totals[key]=timing_totals.get(key,0.)+value
                            emit(completed_sessions=index+1,timing=receipt['timing'],average_timing={key:value/timing_count for key,value in timing_totals.items()})
                            if (output/'STOP').exists():raise KeyboardInterrupt('Owned profile stop requested')
                    elapsed=time.perf_counter()-began
                    totals={key:sum(r['timing'][key] for r in receipts) for key in receipts[0]['timing']}
                    row=dict(population=size,batch_size=batch_size,session_count=len(sessions),replay_timestamps=args.profile_seconds,
                             elapsed_seconds=elapsed,timing_totals=totals,
                             candidate_timestamps_per_second=size*len(sessions)*args.profile_seconds/totals['replay'],
                             candidate_sessions_per_second=size*len(sessions)/elapsed,
                             peak_allocated_gib=torch.cuda.max_memory_allocated()/1024**3 if args.device=='cuda' else None,
                             peak_reserved_gib=torch.cuda.max_memory_reserved()/1024**3 if args.device=='cuda' else None,
                             population_sha256=fingerprint([state(v) for v in population]),
                             status='measured_full_session' if args.profile_seconds==19800 else 'measured_prefix_not_full_session',validation_opened=False)
                except (MemoryError,torch.cuda.OutOfMemoryError) as error:
                    row=dict(population=size,batch_size=batch_size,status='memory_limit',error=str(error),validation_opened=False)
                finally:evaluator.close()
                rows.append(row);write_json(output/'measurements.json',rows);emit(stage='Profile configuration complete',profile_rows=rows)
        write_json(output/'profile.json',dict(status='profile_complete_not_qualification',identity_sha256=file_hash(output/'identity.json'),measurements=rows))
        emit(status='profile_complete',stage='Timing sweep complete; final parameters not selected');return 0
    except BaseException as error:
        emit(status='interrupted' if isinstance(error,(KeyboardInterrupt,InterruptedError)) else 'failed',error=str(error));raise
    finally:lock.unlink(missing_ok=True)


if __name__=='__main__':raise SystemExit(main())
