"""Launch an immutable, qualified V17 campaign with complete safe defaults."""
import os
os.environ['PYTHONDONTWRITEBYTECODE']='1'
import argparse,json,sys,time,psutil
from pathlib import Path
from .run_search import main as search,source_hash,bind_dataset
from research.vectorized_backtest.v6.torch_backtest.runtime import require_runtime,write_json,file_hash


def main():
    p=argparse.ArgumentParser(description=__doc__)
    for name in ('training','validation','output','qualification','v16-stop'):p.add_argument('--'+name,type=Path,required=True)
    p.add_argument('--commit',required=True)
    p.add_argument('--generations',type=int,default=50)
    a=p.parse_args();root=Path('D:/TradingML/runtimes/vectorized_backtest/torch_backtest_v17')
    out=require_runtime(a.output)
    if not out.is_relative_to(root):raise ValueError('V17 campaign must use its own runtime root')
    q=json.loads(a.qualification.read_text());stop=json.loads(a.v16_stop.read_text())
    if q['status']!='passed' or q['source_sha256']!=source_hash() or not q['exact_accounting_parity']:raise ValueError('Matching V17 qualification required')
    if not stop['workers_stopped'] or not stop['lock_released'] or file_hash(Path(stop['campaign'])/'checkpoint.json')!=q['baseline_checkpoint_sha256']:
        raise ValueError('Verified immutable V16 stop required')
    for version in range(7,18):
        pointer=root.parent/f'torch_backtest_v{version}/active-campaign.json'
        if pointer.exists():
            old=json.loads(pointer.read_text())
            if 'creation_time' not in old:continue
            try:
                process=psutil.Process(old['pid'])
                if abs(process.create_time()-old['creation_time'])<.01 and process.pid!=os.getpid():raise RuntimeError(f'Optimizer V{version} already alive')
            except psutil.NoSuchProcess:pass
    for path,role in ((a.training,'training'),(a.validation,'validation')):
        write_json(out/(role+'.json'),bind_dataset(path,role))
    argv=['--training',str(out/'training.json'),'--validation',str(out/'validation.json'),'--output',str(out),
        '--population','1024','--batch-size','512','--session-workers','2','--generations',str(a.generations),'--seed','172237',
        '--capture-gib','4','--maximum-gate-gib','8']
    record=dict(pid=os.getpid(),creation_time=psutil.Process().create_time(),code=str(Path(__file__).resolve().parents[3]),commit=a.commit,
        campaign=str(out),source_sha256=source_hash(),arguments=argv,qualification_sha256=file_hash(a.qualification),
        v16_stop_sha256=file_hash(a.v16_stop),validation_feedback=False,initialization='random-only-v1')
    write_json(out/'active.json',record);write_json(root/'active-campaign.json',record)
    print('COMMAND',sys.executable,'-B -u -m research.vectorized_backtest.v17.run_search',' '.join(argv),flush=True)
    result=1
    try:
        sys.argv=['v17.run_search',*argv];result=search() or 0
        return result
    finally:write_json(out/'exit.json',dict(exit_code=result,finished_epoch=time.time()))


if __name__=='__main__':raise SystemExit(main())
