"""One finite workstation process, owned receipt and logs; no scheduled task."""
import os
os.environ.update(PYTHONDONTWRITEBYTECODE='1',OMP_NUM_THREADS='1',MKL_NUM_THREADS='1',PYTHONUNBUFFERED='1')
import argparse,ctypes,subprocess,sys,time
from pathlib import Path
import psutil
from ..v5.shards.phase1_contract import write_json


def main():
    p=argparse.ArgumentParser();p.add_argument('--output',type=Path,required=True);p.add_argument('args',nargs=argparse.REMAINDER);a=p.parse_args()
    if not a.output.resolve().is_relative_to(Path('D:/TradingML/runtimes').resolve()):raise ValueError('Runtime authority required')
    if a.output.exists() or (a.output.parent/'STOP').exists():raise RuntimeError('Existing identity or STOP; preserved')
    setup=a.output.with_name(a.output.name+'-launcher');setup.mkdir()
    args=a.args[1:] if a.args[:1]==['--'] else a.args
    command=[sys.executable.replace('pythonw.exe','python.exe'),'-B','-u','-m','research.bar_gpt.phase1_v11.run','experiment','--output',str(a.output),*args]
    print(subprocess.list2cmdline(command),flush=True);own=psutil.Process()
    ctypes.windll.kernel32.SetThreadExecutionState(0x80000001)
    proof=dict(controller=dict(pid=own.pid,created=own.create_time()),command=command,started=time.time())
    try:
        with (setup/'stdout.log').open('w') as out,(setup/'stderr.log').open('w') as err:
            child=subprocess.Popen(command,cwd=Path(__file__).resolve().parents[3],env=dict(os.environ),stdout=out,stderr=err,creationflags=subprocess.CREATE_NO_WINDOW)
            proof['child']=dict(pid=child.pid,created=psutil.Process(child.pid).create_time());write_json(setup/'launch.json',proof)
            # Hard upper bound is a last-resort safeguard; scientific runner reserves evaluation time.
            code=child.wait(timeout=60*60);write_json(setup/'exit.json',dict(code=code,ended=time.time()));return code
    except subprocess.TimeoutExpired:
        child.terminate();child.wait(timeout=30)
        write_json(setup/'exit.json',dict(code='deadline_terminated',ended=time.time()));raise
    finally:ctypes.windll.kernel32.SetThreadExecutionState(0x80000000)


if __name__=='__main__':raise SystemExit(main())
