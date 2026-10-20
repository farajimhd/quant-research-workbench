"""V33 global-only launcher; V32 optimized evaluation and evolution unchanged."""
import os
os.environ['PYTHONDONTWRITEBYTECODE']='1'
import argparse,json,time,sys
from pathlib import Path
import psutil
from .phase2 import run_global
from .run_search import bind_dataset,source_hash
from .publication import write_json
from research.vectorized_backtest.v6.torch_backtest.runtime import require_runtime
from research.vectorized_backtest.v6.torch_backtest.materialize import owned_run


def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__)
    for key in ('training','validation','initial-pool','output'):p.add_argument('--'+key,type=Path,required=True)
    p.add_argument('--commit',required=True);p.add_argument('--authorize-run',action='store_true')
    p.add_argument('--generations',type=int,default=50);p.add_argument('--seed',type=int,default=2020)
    args=p.parse_args(argv)
    if not args.authorize_run:p.error('--authorize-run required')
    root=require_runtime(Path('D:/TradingML/runtimes/vectorized_backtest/torch_backtest_v33'))
    output=require_runtime(args.output)
    if not output.is_relative_to(root):p.error('V33 runtime root required')
    train=bind_dataset(args.training,'training');valid=bind_dataset(args.validation,'validation')
    if len(train['days'])!=24 or len(valid['days'])!=12:raise ValueError('Pinned 24/12 split required')
    pool=json.loads(args.initial_pool.read_bytes())
    from .initial_pool import verify,seed_results
    verify(pool,train['days'],dataset=train)
    pointer=root/'active-campaign.json'
    if pointer.exists():
        old=json.loads(pointer.read_bytes())
        try:
            if abs(psutil.Process(old['pid']).create_time()-old['creation_time'])<.01:raise RuntimeError('V33 already running')
        except psutil.NoSuchProcess:pass
    owner=dict(pid=os.getpid(),creation_time=psutil.Process().create_time(),campaign=str(output),source_sha256=source_hash(),commit=args.commit)
    with owned_run(output,version='v33-global-controller-v1'):
        write_json(pointer,owner);write_json(output/'active.json',owner)
        write_json(output/'manifest.json',dict(**owner,arguments={k:str(v) if isinstance(v,Path) else v for k,v in vars(args).items()},validation_feedback=False,global_only=True))
        write_json(output/'status.json',dict(status='running',stage='Phase 2: global evolution from original initial pools',global_output=str(output/'global'),completed=0,failed=0,workers={},updated_epoch=time.time()))
        code=1
        try:
            if not (output/'global'/'checkpoint.json').exists():
                count=seed_results(pool,args.initial_pool.parent,output/'global')
                write_json(output/'initial-result-reuse.json',dict(reused=count,validation_opened=False))
            code=run_global(args.training,args.validation,args.initial_pool,output/'global',population_size=1024,generations=args.generations,seed=args.seed,
                batch_size=512,session_workers=2,capture_gib=1.,host_reserve_gib=128.,host_admission_gib=32.,host_prefetch=True,shared_replay=True)
            return code
        finally:
            from .preparation import close
            from .cpu_work import close as close_cpu
            close();close_cpu();write_json(output/'exit.json',dict(exit_code=code,epoch=time.time()))


if __name__=='__main__':sys.exit(main())
