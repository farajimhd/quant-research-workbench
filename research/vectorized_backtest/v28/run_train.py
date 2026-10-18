"""Laptop/local or full local+global V28 controller; never starts another version."""
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
ROOT=Path('D:/TradingML/runtimes/vectorized_backtest/torch_backtest_v28')

def config(dataset,args):
    return dict(inputs=dataset['inputs'],history=dataset['history'],feature_cache=dataset.get('feature_cache'),indicator_root=dataset['indicator_root'],enriched_root=dataset['enriched_root'],
        input_receipts=dataset['receipts'],indicator_receipts=dataset['indicator_receipts'],enriched_receipts=dataset['enriched_receipts'],authorization='v28-training',
        source_sha256=source_hash(),device='cuda',backend='compile',batch_size=args.batch_size,maximum_data_gib=4.,maximum_gate_gib=0.,capture_gib=1.,
        plan_limit=1,episode_capacity=65536,execution=asdict(Execution(fee_per_share=.005,minimum_order_fee=1)),selection=asdict(Selection()),
        host_reserve_gib=args.host_reserve_gib,host_admission_gib=args.host_admission_gib,lane_memory_bytes=int(torch.cuda.mem_get_info()[0]*.8))

def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--training',type=Path,required=True);p.add_argument('--validation',type=Path)
    p.add_argument('--output',type=Path,required=True);p.add_argument('--mode',choices=('local','full'),default='local');p.add_argument('--day',default='2026-08-04')
    p.add_argument('--authorize-run',action='store_true');p.add_argument('--commit',required=True)
    for k,v in [('generations',30),('global-generations',50),('population',1024),('initial-multiplier',6),('batch-size',512),('seed',2020)]:p.add_argument('--'+k,type=int,default=v)
    p.add_argument('--host-reserve-gib',type=float,default=12.);p.add_argument('--host-admission-gib',type=float,default=8.)
    args=p.parse_args()
    if not args.authorize_run:p.error('--authorize-run required')
    output=require_runtime(args.output)
    if not output.is_relative_to(ROOT.resolve()):p.error('Use the V28 runtime root')
    train=bind_dataset(args.training,'training');cfg=config(train,args)
    if args.mode=='local' and args.day not in train['days']:p.error('Requested day absent from pinned training manifest')
    days=[args.day] if args.mode=='local' else train['days']
    if args.mode=='full':
        if args.validation is None:p.error('Full mode requires validation manifest')
        valid=bind_dataset(args.validation,'validation')
        if len(days)!=24 or len(valid['days'])!=12 or max(days)>=min(valid['days']):p.error('Full mode requires chronological 24/12 inputs')
    manifest=dict(version='v28-controller-v1',commit=args.commit,source_sha256=cfg['source_sha256'],networks=network_contract(),arguments={k:str(v) if isinstance(v,Path) else v for k,v in vars(args).items()},config={k:v for k,v in cfg.items() if k!='lane_memory_bytes'},days=days,validation_feedback=False)
    f=output/'manifest.json'
    if f.exists() and json.loads(f.read_bytes())!=manifest:raise ValueError('Exact controller contract changed')
    pointer=ROOT/'active-campaign.json'
    if pointer.exists():
        a=json.loads(pointer.read_bytes())
        try:
            if abs(psutil.Process(a['pid']).create_time()-a['creation_time'])<.01:raise RuntimeError('V28 controller already running')
        except psutil.NoSuchProcess:pass
    with owned_run(output,version='v28-controller-v1'):
        write_json(f,manifest);owner=dict(pid=os.getpid(),creation_time=psutil.Process().create_time(),campaign=str(output),source_sha256=cfg['source_sha256'],commit=args.commit)
        write_json(output/f'admission-{time.time_ns()}.json',dict(lane_memory_bytes=cfg['lane_memory_bytes'],host_reserve_gib=args.host_reserve_gib,host_admission_gib=args.host_admission_gib))
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
        print('V28 independent networks | '+args.mode+' | '+', '.join(days)+f' | population {args.population} | initial {args.initial_multiplier}x | batch {args.batch_size}',flush=True)
        try:
            for day in days:
                result=run_session(cfg,day,output/'local'/day,args.generations,args.population,args.seed,64,1024,initial_multiplier=args.initial_multiplier)
                if result['status']!='complete':code=130;return code
            if args.mode=='full':
                from .phase2 import run_global
                code=run_global(args.training,args.validation,output/'local',output/'global',args.population,args.global_generations,args.seed,batch_size=args.batch_size,session_workers=1,capture_gib=1.,host_reserve_gib=args.host_reserve_gib,host_admission_gib=args.host_admission_gib)
            else:code=0
            print('V28 complete' if code==0 else 'V28 interrupted',flush=True)
            return code
        except BaseException as error:
            write_json(output/'failure.json',dict(error=repr(error),epoch=time.time()));raise
        finally:
            stop.set();thread.join();signal.signal(signal.SIGINT,previous_handler);write_json(output/'exit.json',dict(exit_code=code,epoch=time.time()))

if __name__=='__main__':sys.exit(main())
