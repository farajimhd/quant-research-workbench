"""Explicit owned full-run controller; no scheduler or recurring automation."""
import os
os.environ.update(PYTHONDONTWRITEBYTECODE='1',OMP_NUM_THREADS='1',MKL_NUM_THREADS='1',PYTHONUNBUFFERED='1')
import argparse,ctypes,subprocess,sys,time
from pathlib import Path
import psutil
from ..v5.shards.phase1_contract import write_json


def main():
    p=argparse.ArgumentParser();p.add_argument('--output',type=Path,required=True);p.add_argument('--resume',action='store_true');p.add_argument('args',nargs=argparse.REMAINDER);a=p.parse_args()
    if not a.output.resolve().is_relative_to(Path('D:/TradingML/runtimes').resolve()):raise ValueError('Runtime authority required')
    if (a.output.exists() and not a.resume) or (a.output/'STOP').exists() or (a.output.parent/'STOP').exists():raise RuntimeError('Existing identity or STOP preserved')
    setup=a.output.with_name(a.output.name+'-launcher');setup.mkdir(exist_ok=a.resume)
    args=a.args[1:] if a.args[:1]==['--'] else a.args
    command=[sys.executable.replace('pythonw.exe','python.exe'),'-B','-u','-m','research.bar_gpt.phase1_v11.full_run','--output',str(a.output),*args]
    if a.resume:command+=['--resume']
    print(subprocess.list2cmdline(command),flush=True);own=psutil.Process();ctypes.windll.kernel32.SetThreadExecutionState(0x80000001)
    proof=dict(controller=dict(pid=own.pid,created=own.create_time()),command=command,started=time.time())
    try:
        with (setup/'stdout.log').open('a') as out,(setup/'stderr.log').open('a') as err:
            child=subprocess.Popen(command,cwd=Path(__file__).resolve().parents[3],env=dict(os.environ),stdout=out,stderr=err,creationflags=subprocess.CREATE_NO_WINDOW)
            proof['child']=dict(pid=child.pid,created=psutil.Process(child.pid).create_time());write_json(setup/'launch.json',proof);write_json(setup/f'launch-{child.pid}.json',proof)
            code=child.wait();write_json(setup/'exit.json',dict(code=code,ended=time.time()));return code
    finally:ctypes.windll.kernel32.SetThreadExecutionState(0x80000000)


if __name__=='__main__':raise SystemExit(main())
