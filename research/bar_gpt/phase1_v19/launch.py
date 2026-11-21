"""Detached-friendly finite training controller with durable owner/exit receipts."""
import os
os.environ['PYTHONDONTWRITEBYTECODE']='1'
import json,subprocess,sys,time
from pathlib import Path
import psutil
from ..v5.shards.phase1_contract import write_json


def main():
    argv=sys.argv[1:]
    if argv and argv[0]=='--':argv=argv[1:]
    output=Path(argv[argv.index('--output')+1])
    if not output.resolve().is_relative_to(Path('D:/TradingML/runtimes').resolve()):raise ValueError('Runtime authority required')
    root=output.with_name(output.name+'-launcher');root.mkdir(parents=True,exist_ok=True)
    if (output/'STOP').exists():raise RuntimeError('STOP preserved')
    command=[sys.executable,'-B','-u','-m','research.bar_gpt.phase1_v19.run',*argv]
    started=time.time();tag=str(int(started));source=Path(__file__).resolve().parents[3]
    with (root/f'stdout-{tag}.log').open('w') as out,(root/f'stderr-{tag}.log').open('w') as err:
        child=subprocess.Popen(command,cwd=source,env=dict(os.environ,PYTHONDONTWRITEBYTECODE='1'),stdout=out,stderr=err,
                               creationflags=subprocess.CREATE_NO_WINDOW if os.name=='nt' else 0)
        launch=dict(command=command,source=str(source),controller=dict(pid=os.getpid(),created=psutil.Process().create_time()),
                    child=dict(pid=child.pid,created=psutil.Process(child.pid).create_time()),started=started,stdout=str(out.name),stderr=str(err.name))
        write_json(root/'launch.json',launch);write_json(root/f'launch-{tag}.json',launch)
        try:code=child.wait()
        except KeyboardInterrupt:
            if output.exists() and not (output/'STOP').exists():(output/'STOP').write_text('Controller interrupted; graceful checkpoint requested\n')
            code=child.wait()
        write_json(root/f'exit-{tag}.json',dict(exit_code=code,finished=time.time(),seconds=time.time()-started))
    return code


if __name__=='__main__':raise SystemExit(main())
