"""Lightweight waiting queue: no Torch import or CUDA work before V19 exits."""
import os
os.environ['PYTHONDONTWRITEBYTECODE']='1'
import argparse,json,sys,time
from pathlib import Path
import psutil

# Importing v6.torch_backtest.runtime executes that package's Torch/runner
# initializer. Waiting uses only this queue's narrow stdlib I/O contract.
ROOT=Path('D:/TradingML/runtimes/vectorized_backtest/torch_backtest_v22')


def require_queue(path):
    if not Path('D:/TradingML/runtimes').is_dir():raise ValueError('Required runtime unavailable')
    target=Path(path).resolve()
    if not target.is_relative_to(ROOT.resolve()) or target==ROOT.resolve():raise ValueError('Use the V22 queue runtime root')
    target.mkdir(parents=True,exist_ok=True);return target


def write_json(path,value):
    temporary=path.with_suffix('.json.tmp')
    temporary.write_text(json.dumps(value,sort_keys=True,allow_nan=False),encoding='utf-8');temporary.replace(path)


def ready_to_check(job):
    current=json.loads(Path(job['predecessor_job']).read_bytes());root=Path(current['campaign'])
    path=root/'checkpoint.json'
    if not path.exists():return False,0
    checkpoint=json.loads(path.read_bytes());done=checkpoint['state']['completed']
    if checkpoint['contract']['generations']!=50:raise ValueError('Approved 50-generation V19 required')
    if done!=50 or (root/'owner.lock').exists() or not (root/'exit.json').exists():return False,done
    if json.loads((root/'exit.json').read_bytes())['exit_code']!=0:return False,done
    active=json.loads((root/'active.json').read_bytes())
    try:
        if abs(psutil.Process(active['pid']).create_time()-active['creation_time'])<.01:return False,done
    except psutil.NoSuchProcess:pass
    return True,done


def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--job',type=Path,required=True)
    parser.add_argument('--once',action='store_true',help='Readiness smoke only, never profile or run')
    args=parser.parse_args();job=json.loads(args.job.read_bytes());queue=require_queue(args.job.parent)
    if job['mode']!='profile':raise ValueError('Deferred queue authorizes only the bounded profile, not a full campaign')
    if Path(job['code']).resolve()!=Path(__file__).resolve().parents[3]:raise ValueError('Queue must use its immutable code copy')
    write_json(queue/'waiting-active.json',dict(pid=os.getpid(),creation_time=psutil.Process().create_time()))
    result=1
    try:
        while True:
            ready,completed=ready_to_check(job)
            write_json(queue/'status.json',dict(status='ready-for-verification' if ready else 'waiting',completed_generations=completed,
                       total_generations=50,stage='V19 completion; V22 compute not started',updated_epoch=time.time()))
            print(f'V22 queued | V19 {completed}/50 | no V22 compute started',flush=True)
            if args.once:result=0;return result
            if (queue/'STOP').exists():result=130;return result
            if ready:break
            time.sleep(30)
        # Import the heavy controller only now. It verifies all final evidence
        # and pinned data before creating its CUDA workers.
        from .run_search import source_hash
        if source_hash()!=job['source_sha256']:raise ValueError('Queued immutable source changed')
        from .run_train import main as launch
        command=['--training',job['training'],'--validation',job['validation'],'--output',job['output'],
                 '--predecessor-job',job['predecessor_job'],'--commit',job['commit'],'--mode','profile']
        sys.argv=['v22.run_train',*command];result=launch() or 0;return result
    except BaseException as error:
        write_json(queue/'failure.json',dict(error=str(error),type=type(error).__name__,finished_epoch=time.time()));raise
    finally:write_json(queue/'waiting-exit.json',dict(exit_code=result,finished_epoch=time.time()))


if __name__=='__main__':raise SystemExit(main())
