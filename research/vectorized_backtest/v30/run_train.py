"""Fresh global-only V30 controller using the existing Phase-2 engineering path."""
import os
os.environ['PYTHONDONTWRITEBYTECODE']='1'
for key in ('OMP_NUM_THREADS','MKL_NUM_THREADS','OPENBLAS_NUM_THREADS'):os.environ.setdefault(key,'1')
import argparse,json,time,signal
from pathlib import Path
from dataclasses import asdict
from .run_search import run,source_hash,bind_dataset
from .lineage import Selection
from .config import contract
from research.vectorized_backtest.v6.torch_backtest.materialize import owned_run
from research.vectorized_backtest.v6.torch_backtest.runtime import write_json,require_runtime


def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__)
    for name in ('training','validation','output'):p.add_argument('--'+name,type=Path,required=True)
    p.add_argument('--authorize-run',action='store_true');p.add_argument('--commit',required=True)
    for name,default in [('population',1024),('generations',100),('seed',2020),('batch-size',512),('session-workers',2)]:p.add_argument('--'+name,type=int,default=default)
    for name,default in [('capture-gib',1.),('host-reserve-gib',128.),('host-admission-gib',32.)]:p.add_argument('--'+name,type=float,default=default)
    a=p.parse_args(argv)
    if not a.authorize_run:p.error('Explicit run authorization required')
    train=bind_dataset(a.training,'training');valid=bind_dataset(a.validation,'validation')
    if len(train['days'])!=24 or len(valid['days'])!=12 or max(train['days'])>=min(valid['days']):p.error('Chronological certified 24/12 sessions required')
    output=require_runtime(a.output);code=1
    manifest=dict(version='v30-global-controller-v1',commit=a.commit,source_sha256=source_hash(),arguments={k:str(v) if isinstance(v,Path) else v for k,v in vars(a).items()},networks=contract(),selection=asdict(Selection()),training_days=train['days'],validation_days=valid['days'],fresh_initialization=True,validation_feedback=False)
    if (output/'manifest.json').exists() and json.loads((output/'manifest.json').read_bytes())!=manifest:raise ValueError('Exact campaign manifest changed')
    with owned_run(output,version='v30-global-controller-v1'):
        write_json(output/'manifest.json',manifest)
        def stop(*_):
            (output/'global').mkdir(exist_ok=True);(output/'global'/'STOP').touch();print('Stop requested at sealed generation boundary.',flush=True)
        previous=signal.signal(signal.SIGINT,stop)
        try:
            print('V30 global | fresh random | 24 training / 12 display-only | net profit | species | '+str(a.generations)+' generations',flush=True)
            code=run(a.training,a.validation,output/'global',population_size=a.population,generations=a.generations,batch_size=a.batch_size,session_workers=a.session_workers,seed=a.seed,
                selection=Selection(),capture_gib=a.capture_gib,host_reserve_gib=a.host_reserve_gib,host_admission_gib=a.host_admission_gib)
            return code
        except BaseException as error:
            write_json(output/'failure.json',dict(error=repr(error),epoch=time.time()));raise
        finally:
            signal.signal(signal.SIGINT,previous);write_json(output/'exit.json',dict(exit_code=code,epoch=time.time()))

if __name__=='__main__':raise SystemExit(main())
