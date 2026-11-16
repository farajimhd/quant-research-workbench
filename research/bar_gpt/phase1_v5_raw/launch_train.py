"""Launch a fresh raw baseline with measured resources; no implicit resume."""
import os
os.environ.update(PYTHONDONTWRITEBYTECODE='1',OMP_NUM_THREADS='1',MKL_NUM_THREADS='1',
                  CUBLAS_WORKSPACE_CONFIG=':4096:8',PYTHONUNBUFFERED='1')
import argparse,ctypes,json,subprocess,sys,time
from pathlib import Path


def command(python,output,revision,calibration=None):
    result=[python,'-B','-u','-m','research.bar_gpt.phase1_v5_raw.run',
        '--mode','train','--execute','--output',str(output),'--encoder-width','128',
        '--embedding-width','256','--batch-size','8192','--workers','32','--prefetch','4',
        '--prepare-threads','1','--threads','1','--shard-prefetch','16','--shard-prefetch-mib','64',
        '--precision','bf16','--cuda-graph','--feature-drop','0.075','--epochs','50',
        '--learning-rate','0.0003','--min-learning-rate','0.00005','--warmup-updates','1000',
        '--train-steps','0','--validation-steps','0','--checkpoint-every','1000',
        '--wandb-mode','online','--source-revision',revision]
    if calibration:result+=['--calibration',str(calibration)]
    return result


def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--output',type=Path,default=Path('D:/TradingML/runtimes/bar_gpt/phase1-v5-raw-d128-e256-train-01'))
    p.add_argument('--source-revision',required=True);p.add_argument('--calibration',type=Path)
    a=p.parse_args(argv);base=Path('D:/TradingML/runtimes').resolve()
    if not a.output.resolve().is_relative_to(base):raise ValueError('Output must be under the runtime root')
    if a.output.exists():raise FileExistsError('Fresh run requires a new output; never overwrite or silently resume')
    setup=a.output.with_name(a.output.name+'-launcher');setup.mkdir(exist_ok=False)
    python=sys.executable.replace('pythonw.exe','python.exe');cmd=command(python,a.output,a.source_revision,a.calibration)
    print('Command: '+subprocess.list2cmdline(cmd),flush=True)
    import psutil
    own=psutil.Process();proof=dict(source_revision=a.source_revision,command=cmd,
        controller=dict(pid=own.pid,created=own.create_time()),started=time.time())
    if os.name=='nt':ctypes.windll.kernel32.SetThreadExecutionState(0x80000001)
    try:
        with (setup/'stdout.log').open('w') as stdout,(setup/'stderr.log').open('w') as stderr:
            child=subprocess.Popen(cmd,cwd=Path(__file__).resolve().parents[3],env=dict(os.environ),
                stdout=stdout,stderr=stderr,creationflags=subprocess.CREATE_NO_WINDOW if os.name=='nt' else 0)
            proof['child']=dict(pid=child.pid,created=psutil.Process(child.pid).create_time())
            (setup/'launch.json').write_text(json.dumps(proof,indent=2))
            code=child.wait();(setup/'exit.json').write_text(json.dumps(dict(code=code,ended=time.time())))
            return code
    finally:
        if os.name=='nt':ctypes.windll.kernel32.SetThreadExecutionState(0x80000000)


if __name__=='__main__':raise SystemExit(main())
