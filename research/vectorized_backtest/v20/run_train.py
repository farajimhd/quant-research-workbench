"""V20 controller: wait for V19, then profile local sessions or run both phases."""
import os
os.environ['PYTHONDONTWRITEBYTECODE']='1'
for name,value in [('POLARS_MAX_THREADS','2'),('OMP_NUM_THREADS','1'),('MKL_NUM_THREADS','1'),('OPENBLAS_NUM_THREADS','1')]:
    os.environ.setdefault(name,value)
import argparse,json,multiprocessing as mp,sys,time,threading
from concurrent.futures import ProcessPoolExecutor,wait,FIRST_COMPLETED
from dataclasses import asdict
from pathlib import Path
import psutil
from .completion_gate import completion,require_complete
from .run_search import bind_dataset,source_hash,digest
from .local_runner import run_session
from .evaluator import Execution
from research.vectorized_backtest.v6.torch_backtest.runtime import require_runtime,write_json,file_hash
from research.vectorized_backtest.v6.torch_backtest.materialize import owned_run
ROOT=Path('D:/TradingML/runtimes/vectorized_backtest/torch_backtest_v20')


def snapshot(output,status,**values):
    write_json(output/'status.json',dict(status=status,updated_epoch=time.time(),**values))
    if status=='waiting':text=f"V20 waiting | V19 {values.get('completed_generations',0)}/50 | {values.get('reason','')}"
    else:text=f"V20 {status} | {values.get('stage','')} | sessions {values.get('completed',0)}/{values.get('total',0)} | active {values.get('active',0)} | queued {values.get('queued',0)} | failed {values.get('failed',0)}"
    print(text,flush=True)


def local_phase(config,days,output,*,generations,population,workers,seed,archive_capacity,archive_shortlist):
    """One task evolves a whole session; at most two persistent processes."""
    require_complete(config['predecessor_job'],config.get('predecessor_proof'))
    if not 1<=workers<=2:raise ValueError('At most two session workers until profiling proves otherwise')
    import torch
    config=dict(config,lane_memory_bytes=int(torch.cuda.mem_get_info()[0]*.8/workers) if config['device']=='cuda' else 0)
    completed=[];failed=[];pending={};todo=iter(days);interrupted=False
    with ProcessPoolExecutor(max_workers=workers,mp_context=mp.get_context('spawn')) as pool:
        def submit():
            day=next(todo,None)
            if day is not None:
                folder=require_runtime(output/'local'/day)
                future=pool.submit(run_session,config,day,folder,generations,population,seed,archive_capacity,archive_shortlist)
                pending[future]=day
        for _ in range(min(workers,len(days))):submit()
        while pending:
            if (output/'STOP').exists():
                interrupted=True
                for day in pending.values():(output/'local'/day/'STOP').touch()
            done,_=wait(pending,timeout=30,return_when=FIRST_COMPLETED)
            for future in done:
                day=pending.pop(future)
                try:
                    result=future.result()
                    if result['status']=='complete':completed.append(day)
                    else:interrupted=True
                except Exception as error:
                    failed.append(dict(day=day,error=str(error)));interrupted=True
                    for active in pending.values():(output/'local'/active/'STOP').touch()
                if not interrupted:submit()
            states={}
            for day in pending.values():
                path=output/'local'/day/'status.json'
                if path.exists():states[day]=json.loads(path.read_bytes())
            snapshot(output,'running' if not interrupted else 'stopping',stage='Local session evolution',completed=len(completed),total=len(days),
                     active=len(pending),queued=max(0,len(days)-len(completed)-len(pending)-len(failed)),failed=len(failed),workers=states)
    write_json(output/'local-results.json',dict(completed=completed,failed=failed,interrupted=interrupted))
    if failed:raise RuntimeError('Local sessions failed; see local-results.json')
    return 130 if interrupted else 0


def execute(args):
    output=require_runtime(args.output)
    if not output.is_relative_to(ROOT.resolve()):raise ValueError('Use the V20 runtime root')
    # WAITING creates no CUDA context or workers and never changes V19.
    while True:
        proof=completion(args.predecessor_job)
        if proof['status']=='ready':break
        snapshot(output,'waiting',**{k:v for k,v in proof.items() if k!='status'})
        if args.check_only:return 0
        if (output/'STOP').exists():snapshot(output,'interrupted',stage='Stopped while waiting');return 130
        time.sleep(args.poll_seconds)
    if args.check_only:snapshot(output,'ready',stage='V19 completed; no V20 work started');return 0
    pointer=ROOT/'active-campaign.json'
    if pointer.exists():
        from .completion_gate import alive
        if alive(json.loads(pointer.read_bytes())):raise RuntimeError('Another V20 controller is already active')
    train=bind_dataset(args.training,'training');valid=bind_dataset(args.validation,'validation')
    if len(train['days'])!=24 or len(valid['days'])!=12 or max(train['days'])>=min(valid['days']):raise ValueError('Approved chronological 24/12 dataset required')
    prior=json.loads((Path(proof['campaign'])/'checkpoint.json').read_bytes())['contract']
    if train!=prior['training'] or valid!=prior['validation']:raise ValueError('V20 must retain the approved V19 dataset and exposure provenance')
    days=train['days'][:args.profile_sessions] if args.mode=='profile' else train['days']
    generations=args.profile_generations if args.mode=='profile' else args.local_generations
    contract=dict(version='v20-two-phase-controller-v1',commit=args.commit,source_sha256=source_hash(),mode=args.mode,days=days,
                  local_generations=generations,global_generations=args.global_generations,population=args.population,batch_size=args.batch_size,
                  workers=args.workers,seed=args.seed,archive_capacity=args.archive_capacity,archive_shortlist=args.archive_shortlist,
                  training_sha256=file_hash(args.training),validation_sha256=file_hash(args.validation),predecessor=proof,validation_feedback=False)
    path=output/'manifest.json'
    if path.exists() and json.loads(path.read_bytes())!=contract:raise ValueError('Exact controller resume identity changed')
    write_json(path,contract)
    config=dict(inputs=train['inputs'],history=train['history'],feature_cache=train.get('feature_cache'),indicator_root=train['indicator_root'],
                enriched_root=train['enriched_root'],input_receipts=train['receipts'],indicator_receipts=train['indicator_receipts'],enriched_receipts=train['enriched_receipts'],
                predecessor_job=str(args.predecessor_job),predecessor_proof=proof,source_sha256=contract['source_sha256'],device='cuda',backend='compile',batch_size=args.batch_size,
                maximum_data_gib=4.,maximum_gate_gib=8.,capture_gib=4.,plan_limit=2,episode_capacity=65536,
                execution=asdict(Execution(fee_per_share=.005,minimum_order_fee=1)))
    with owned_run(output,version=contract['version']):
        owner=dict(pid=os.getpid(),creation_time=psutil.Process().create_time(),campaign=str(output),code=str(Path(__file__).resolve().parents[3]),
                   commit=args.commit,source_sha256=contract['source_sha256'],mode=args.mode)
        write_json(output/'active.json',owner);write_json(ROOT/'active-campaign.json',owner)
        result=1
        try:
            result=local_phase(config,days,output,generations=generations,population=args.population,workers=args.workers,seed=args.seed,
                               archive_capacity=args.archive_capacity,archive_shortlist=args.archive_shortlist)
            if result:return result
            if args.mode=='profile':
                evidence={day:[dict(seal=json.loads((output/'local'/day/f'generation-{g:04d}'/'complete.json').read_bytes()),
                                   wall=json.loads((output/'local'/day/f'generation-{g:04d}'/'wall.json').read_bytes())) for g in range(1,generations+1)] for day in days}
                write_json(output/'profile.json',dict(status='complete',contract_sha256=digest(contract),sessions=evidence,
                    cold_generation_included=True,phase2_started=False,scope='Actual bounded local generations; no speed acceptance inferred'))
                snapshot(output,'complete',stage='Local profile complete; full V20 run awaits user request',completed=len(days),total=len(days))
                result=0;return result
            from .phase2 import run_global
            if (output/'STOP').exists():result=130;return result
            global_root=require_runtime(output/'global');stop_monitor=threading.Event()
            def forward_stop():
                while not stop_monitor.wait(1):
                    if (output/'STOP').exists():(global_root/'STOP').touch();return
            thread=threading.Thread(target=forward_stop,name='v20-stop-forwarder',daemon=True);thread.start()
            snapshot(output,'running',stage='Global evolution',completed=len(days),total=len(days))
            try:
                result=run_global(args.training,args.validation,output/'local',global_root,population_size=args.population,
                                  generations=args.global_generations,seed=args.seed,predecessor_job=args.predecessor_job,
                                  batch_size=args.batch_size,session_workers=args.workers,predecessor_proof=proof)
            finally:stop_monitor.set();thread.join()
            snapshot(output,'complete' if result==0 else 'interrupted',stage='Global evolution ended',completed=len(days),total=len(days))
            return result
        except BaseException as error:
            snapshot(output,'failed',stage=str(error),failed=1);raise
        finally:write_json(output/'exit.json',dict(exit_code=result,finished_epoch=time.time()))


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    for name in ('training','validation','output','predecessor-job'):parser.add_argument('--'+name,type=Path,required=True)
    parser.add_argument('--commit',required=True);parser.add_argument('--mode',choices=('profile','full'),default='profile')
    for name,default in [('local-generations',30),('global-generations',50),('profile-generations',3),('profile-sessions',2),('population',1024),
                         ('batch-size',512),('workers',2),('archive-capacity',64),('archive-shortlist',256),('seed',2020),('poll-seconds',30)]:parser.add_argument('--'+name,type=int,default=default)
    parser.add_argument('--check-only',action='store_true',help='One readiness read; never profile or run')
    args=parser.parse_args()
    if not 1<=args.poll_seconds<=60 or not 1<=args.profile_sessions<=24 or not 1<=args.profile_generations<=5:parser.error('Bounded wait/profile required')
    if not 1<=args.workers<=2 or not 10<=args.population<=65536 or not 1<=args.batch_size<=1024:parser.error('Bounded population/workers/batches required')
    if not 1<=args.archive_capacity<=args.archive_shortlist<=1024 or not 1<=args.local_generations<=500 or not 1<=args.global_generations<=500:parser.error('Bounded archive/generations required')
    print('COMMAND',sys.executable,'-B -u -m research.vectorized_backtest.v20.run_train',' '.join(sys.argv[1:]),flush=True)
    return execute(args)


if __name__=='__main__':raise SystemExit(main())
