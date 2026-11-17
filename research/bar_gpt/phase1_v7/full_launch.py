"""Durable one-run controller with keep-awake and exact child ownership receipt."""
import os
os.environ.update(PYTHONDONTWRITEBYTECODE='1',OMP_NUM_THREADS='1',MKL_NUM_THREADS='1',PYTHONUNBUFFERED='1',CUBLAS_WORKSPACE_CONFIG=':4096:8')
import argparse,ctypes,json,subprocess,sys,time
from pathlib import Path
from ..v5.shards.phase1_contract import write_json


def main():
    p=argparse.ArgumentParser();p.add_argument('--output',type=Path,required=True)
    p.add_argument('--source-revision',required=True);p.add_argument('--deployment',type=Path,required=True)
    p.add_argument('--batch-size',type=int,default=8192);p.add_argument('--workers',type=int,default=16)
    p.add_argument('--precision',choices=('fp32','bf16'),default='bf16');p.add_argument('--resume',action='store_true')
    p.add_argument('--preparation',choices=('cpu','gpu'),default='gpu')
    a=p.parse_args();setup=a.output.with_name(a.output.name+'-launcher');setup.mkdir(exist_ok=a.resume)
    if a.output.exists() and not a.resume:raise FileExistsError('Fresh run requires new output')
    cmd=[sys.executable.replace('pythonw.exe','python.exe'),'-B','-u','-m','research.bar_gpt.phase1_v7.full','train',
        '--output',str(a.output),'--source-revision',a.source_revision,'--deployment',str(a.deployment),
        '--batch-size',str(a.batch_size),'--workers',str(a.workers),'--precision',a.precision,'--preparation',a.preparation,'--epochs','50']
    if a.resume:cmd+=['--resume']
    import psutil
    own=psutil.Process();proof=dict(command=cmd,controller=dict(pid=own.pid,created=own.create_time()),started=time.time())
    if os.name=='nt':ctypes.windll.kernel32.SetThreadExecutionState(0x80000001)
    try:
        with (setup/'stdout.log').open('a') as stdout,(setup/'stderr.log').open('a') as stderr:
            child=subprocess.Popen(cmd,cwd=Path(__file__).resolve().parents[3],env=dict(os.environ),stdout=stdout,stderr=stderr,
                creationflags=subprocess.CREATE_NO_WINDOW if os.name=='nt' else 0)
            proof['child']=dict(pid=child.pid,created=psutil.Process(child.pid).create_time());write_json(setup/'launch.json',proof)
            code=child.wait();write_json(setup/'exit.json',dict(code=code,ended=time.time()));return code
    finally:
        if os.name=='nt':ctypes.windll.kernel32.SetThreadExecutionState(0x80000000)


if __name__=='__main__':raise SystemExit(main())
