"""V36 calibrated local sessions, then certified archive-seeded global evolution."""
import os
os.environ['PYTHONDONTWRITEBYTECODE']='1'
for name in ('OMP_NUM_THREADS','MKL_NUM_THREADS','OPENBLAS_NUM_THREADS'):os.environ.setdefault(name,'1')
import argparse,json,time,threading,sys,signal
from pathlib import Path
from dataclasses import asdict
import psutil,torch
from .run_search import bind_dataset,source_hash
from .local_runner import run_session
from .lineage import Selection
from .evaluator import Execution
from .config import contract as network_contract
from research.vectorized_backtest.v6.torch_backtest.runtime import write_json,require_runtime
from research.vectorized_backtest.v6.torch_backtest.materialize import owned_run
ROOT=Path('D:/TradingML/runtimes/vectorized_backtest/torch_backtest_v36')

def config(dataset,args):
    return dict(inputs=dataset['inputs'],history=dataset['history'],feature_cache=dataset.get('feature_cache'),indicator_root=dataset['indicator_root'],enriched_root=dataset['enriched_root'],
        input_receipts=dataset['receipts'],indicator_receipts=dataset['indicator_receipts'],enriched_receipts=dataset['enriched_receipts'],authorization='v36-training',training_manifest=str(args.training),
        source_sha256=source_hash(),device='cuda',backend='compile',batch_size=args.batch_size,maximum_data_gib=4.,maximum_gate_gib=8.,capture_gib=args.capture_gib,
        plan_limit=1,episode_capacity=65536,execution=asdict(Execution(fee_per_share=.005,minimum_order_fee=1)),selection=asdict(Selection()),
        host_reserve_gib=args.host_reserve_gib,host_admission_gib=args.host_admission_gib,host_prefetch=False,shared_replay=args.shared_replay)

def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--training',type=Path,required=True);p.add_argument('--validation',type=Path)
    p.add_argument('--output',type=Path,required=True);p.add_argument('--mode',choices=('local','full'),default='full');p.add_argument('--day',default='2026-08-04')
    p.add_argument('--authorize-run',action='store_true');p.add_argument('--commit',required=True)
    for k,v in [('generations',50),('global-generations',50),('population',1024),('initial-multiplier',6),('batch-size',1024),('session-workers',1),('seed',2020)]:p.add_argument('--'+k,type=int,default=v)
    p.add_argument('--host-reserve-gib',type=float,default=128.);p.add_argument('--host-admission-gib',type=float,default=32.)
    p.add_argument('--capture-gib',type=float,default=1.);p.add_argument('--host-prefetch',action='store_true',help='Bounded Phase-2 CPU prefetch; locals retain their one session')
    p.add_argument('--reuse-initial-manifest',type=Path,help='Pinned sealed V31 generation-one evaluations; unmatched sessions evaluate normally')
    p.add_argument('--shared-replay',action='store_true',help='One CUDA owner, two local sessions, two 512-candidate cohorts each')
    args=p.parse_args(argv)
    if not args.authorize_run:p.error('--authorize-run required')
    if not 10<=args.population<=65536 or not 1<=args.generations<=500 or args.global_generations<1:p.error('Invalid population or generation count')
    if args.mode!='local' or args.population!=1024 or args.initial_multiplier!=6 or args.day!='2026-08-04' or args.generations!=50:p.error('V36 experiment is Aug 4 local only, population 1024, 6x initialization, 50 generations')
    if args.initial_multiplier<1 or args.initial_multiplier*args.population>65536 or not 1<=args.batch_size<=1024 or not 1<=args.session_workers<=8:p.error('Invalid bounded initialization/batch/workers')
    if args.shared_replay and (args.batch_size!=512 or args.session_workers!=2):p.error('Shared replay requires batch 512 and two session workers')
    output=require_runtime(args.output)
    if not output.is_relative_to(ROOT.resolve()):p.error('Use the V36 runtime root')
    train=bind_dataset(args.training,'training');cfg=config(train,args)
    if args.reuse_initial_manifest:
        from research.vectorized_backtest.v6.torch_backtest.runtime import file_hash
        cfg.update(reuse_initial_manifest=str(args.reuse_initial_manifest),reuse_initial_manifest_sha256=file_hash(args.reuse_initial_manifest))
    if args.mode=='local' and args.day not in train['days']:p.error('Requested day absent from pinned training manifest')
    days=[args.day] if args.mode=='local' else train['days']
    if args.mode=='full':
        if args.validation is None:p.error('Full mode requires validation manifest')
        valid=bind_dataset(args.validation,'validation')
        if len(days)!=24 or len(valid['days'])!=12 or max(days)>=min(valid['days']):p.error('Full mode requires chronological 24/12 inputs')
    manifest=dict(version='v36-two-phase-controller-v1',commit=args.commit,source_sha256=cfg['source_sha256'],networks=network_contract(),arguments={k:str(v) if isinstance(v,Path) else v for k,v in vars(args).items()},config=cfg,days=days,
        local_generations=args.generations,global_generations=args.global_generations,initial_count=args.initial_multiplier*args.population,validation_feedback=False)
    f=output/'manifest.json'
    if f.exists() and json.loads(f.read_bytes())!=manifest:raise ValueError('Exact controller contract changed')
    pointer=ROOT/'active-campaign.json'
    if pointer.exists():
        a=json.loads(pointer.read_bytes())
        try:
            if abs(psutil.Process(a['pid']).create_time()-a['creation_time'])<.01:raise RuntimeError('V36 controller already running')
        except psutil.NoSuchProcess:pass
    with owned_run(output,version='v36-controller-v1'):
        write_json(f,manifest);owner=dict(pid=os.getpid(),creation_time=psutil.Process().create_time(),campaign=str(output),source_sha256=cfg['source_sha256'],commit=args.commit)
        write_json(output/f'admission-{time.time_ns()}.json',dict(host_reserve_gib=args.host_reserve_gib,host_admission_gib=args.host_admission_gib,session_workers=args.session_workers,local_workers=2 if args.shared_replay else 1))
        write_json(pointer,owner);write_json(output/'active.json',owner)
        stop=threading.Event()
        from .monitor import monitor
        def interrupt(*_):
            (output/'STOP').touch()
            for day in days:
                folder=output/'local'/day;folder.mkdir(parents=True,exist_ok=True);(folder/'STOP').touch()
            global_root=output/'global'
            if global_root.exists():(global_root/'STOP').touch()
            print('Stop requested; finishing the current generation seal.',flush=True)
        previous_handler=signal.signal(signal.SIGINT,interrupt)
        thread=threading.Thread(target=monitor,args=(output,days,args.generations,stop),daemon=True);thread.start();code=1
        print('V36 independent networks | '+args.mode+' | '+', '.join(days)+f' | population {args.population} | initial {args.initial_multiplier}x | batch {args.batch_size}',flush=True)
        try:
            def local(day,worker_factory=None):
                if (output/'STOP').exists():return dict(status='interrupted')
                from .probes import local_probe
                from .host_memory import wait_for_headroom
                def probe_progress(**fields):write_json(output/'local'/day/'status.json',dict(status='running',day=day,updated_epoch=time.time(),**fields))
                probe_progress(stage='Preparing session-only calibration probe')
                wait_for_headroom(output,probe_progress,reserve_gib=args.host_reserve_gib,admission_gib=args.host_admission_gib)
                path=local_probe(args.training,day,output/'initialization-probes')
                if args.shared_replay:
                    from .session_owner import run_owned_session
                    return run_owned_session(service,dict(cfg,initialization_probe=str(path)),day,output/'local'/day,args.generations,args.population,args.seed,64,1024,initial_multiplier=args.initial_multiplier)
                return run_session(dict(cfg,initialization_probe=str(path)),day,output/'local'/day,args.generations,args.population,args.seed,64,1024,initial_multiplier=args.initial_multiplier,worker_factory=worker_factory)
            if args.shared_replay:
                from concurrent.futures import ThreadPoolExecutor,as_completed
                from .stream_sessions import SharedReplayService
                service=SharedReplayService()
                try:
                    with ThreadPoolExecutor(max_workers=2,thread_name_prefix='v36-local-evolution') as locals_pool:
                        futures=[locals_pool.submit(local,day,service.factory) for day in days]
                        try:
                            for future in as_completed(futures):
                                if future.result()['status']!='complete':interrupt();code=130
                        except BaseException:
                            interrupt()
                            raise
                    if (output/'STOP').exists():code=130;return code
                finally:service.close()
            else:
                for day in days:
                    if local(day)['status']!='complete':code=130;return code
            if args.mode=='full':
                if (output/'STOP').exists():code=130;return code
                stop.set();thread.join()
                write_json(output/'status.json',dict(status='running',stage='Phase 2: mixing sealed local archives',completed=len(days),queued=0,failed=0,workers={},updated_epoch=time.time()))
                from .phase2 import run_global
                from .probes import global_probe
                path=global_probe(args.training,days,output/'initialization-probes')
                code=run_global(args.training,args.validation,output/'local',output/'global',args.population,args.global_generations,args.seed,batch_size=args.batch_size,session_workers=args.session_workers,capture_gib=args.capture_gib,host_reserve_gib=args.host_reserve_gib,host_admission_gib=args.host_admission_gib,initialization_probe=path,host_prefetch=args.host_prefetch,shared_replay=args.shared_replay)
            else:code=0
            print('V36 complete' if code==0 else 'V36 interrupted',flush=True)
            return code
        except BaseException as error:
            write_json(output/'failure.json',dict(error=repr(error),epoch=time.time()));raise
        finally:
            from .preparation import close as close_preparation
            close_preparation()
            stop.set();thread.join();signal.signal(signal.SIGINT,previous_handler)
            write_json(output/'status.json',dict(status='complete' if code==0 else 'interrupted' if code==130 else 'failed',
                stage='Campaign complete' if code==0 else 'Campaign stopped',completed=sum((output/'local'/d/'complete.json').exists() for d in days),
                active=0,queued=0 if code==0 else sum(not (output/'local'/d/'complete.json').exists() for d in days),failed=int(code not in (0,130)),workers={},updated_epoch=time.time()))
            write_json(output/'exit.json',dict(exit_code=code,epoch=time.time()))

if __name__=='__main__':sys.exit(main())
