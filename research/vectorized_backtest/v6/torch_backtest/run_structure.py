"""Bounded training-only structural preparation with live durable progress."""
import os
os.environ['PYTHONDONTWRITEBYTECODE']='1'
import argparse,json,subprocess,sys,time
import shutil
import traceback
from uuid import uuid4
from datetime import datetime,timezone
from pathlib import Path
from rich.console import Console
from rich.live import Live
from rich.table import Table
from .runtime import require_runtime,write_json,file_hash
from .materialize import owned_run
from .structural import worker_budget


def training_days(inputs):
    days=[]
    for folder in sorted(inputs.iterdir()):
        if len(folder.name)!=10 or not (folder/'complete.json').is_file():continue
        receipt=json.loads((folder/'complete.json').read_text())
        if receipt.get('validation_opened',True) or not receipt.get('ready_for_replay'):
            raise ValueError('Require sealed training-only compact inputs')
        if receipt['identity']['session']['day']!=folder.name:raise ValueError('Training day identity mismatch')
        days.append(folder.name)
    if len(days)!=30 or len(set(days))!=30:raise ValueError('Exactly thirty training days required')
    return days


def display(snapshot):
    table=Table(title='V6 causal levels • top 10 target extraction',expand=True)
    for name in ('Session','State','Tickers done','In flight','Stage'):table.add_column(name)
    for day,item in snapshot['sessions'].items():
        table.add_row(day,item['state'],f"{item.get('completed',0)}/{item.get('total','?')}",
            str(item.get('active',0)),item.get('stage',''))
    table.caption=(f"Sessions {snapshot['completed']}/30 • active {snapshot['active']} • queued {snapshot['queued']}"
        f" • failed {snapshot['failed']} • elapsed {snapshot['elapsed_seconds']:.0f}s")
    return table


def validate_workers(session_workers,ticker_workers):
    if not 1<=session_workers<=16 or not 1<=ticker_workers<=8:raise ValueError('Invalid worker count')
    worker_budget(ticker_workers)
    import psutil
    if session_workers*ticker_workers>(os.cpu_count() or 1):raise ValueError('Ticker workers exceed logical CPUs')
    # Account for per-session resident input arrays as well as fitter workers.
    required=session_workers*(4*1024**3+ticker_workers*512*1024**2)
    if required>psutil.virtual_memory().available*.75:raise MemoryError('Session and ticker workers exceed memory headroom')


def completed_day(folder,input_folder,day):
    path=folder/'complete.json';record=json.loads(path.read_text())
    if (record.get('status')!='complete' or record.get('day')!=day or record.get('validation_opened',True)
        or record.get('input_receipt_sha256')!=file_hash(input_folder/'complete.json')
        or record.get('target_projection_scope')!='ranked-top-n-only; full causal bar history retained'):
        raise ValueError('Completed day resume identity/scope mismatch')
    for name,digest in record['files'].items():
        target=(folder/name).resolve()
        if not target.is_relative_to(folder.resolve()) or file_hash(target)!=digest:
            raise ValueError('Completed output integrity mismatch')
    return dict(receipt_sha256=file_hash(path),tickers=len(record['listing_ids']))


def run(inputs,output,*,session_workers=2,ticker_workers=4,resume=False):
    validate_workers(session_workers,ticker_workers)
    inputs=Path(inputs);days=training_days(inputs);output=require_runtime(output)
    if (output/'complete.json').exists():raise ValueError('Completed run cannot be overwritten')
    queued=list(days);active={};finished={};failed={};began=time.perf_counter()
    if resume:
        for day in days:
            folder=output/day
            if (folder/'complete.json').is_file():finished[day]=completed_day(folder,inputs/day,day)
        queued=[day for day in days if day not in finished]
    def publish():
        sessions={day:dict(state='completed',completed=value['tickers'],total=value['tickers'],stage='sealed') for day,value in finished.items()}
        sessions.update({day:dict(state='failed',stage=value) for day,value in failed.items()})
        for day,(child,handles) in active.items():
            path=output/day/'status.json'
            try:item=json.loads(path.read_text())
            except FileNotFoundError:item=dict(stage='certifying inputs and reading bars')
            except PermissionError:item=dict(stage='waiting for progress snapshot')
            sessions[day]=dict(item,state='running',pid=child.pid)
        snapshot=dict(completed=len(finished),active=len(active),queued=len(queued),failed=len(failed),total=30,
            sessions=dict(sorted(sessions.items())),elapsed_seconds=time.perf_counter()-began,validation_opened=False)
        write_json(output/'status.json',snapshot);return snapshot
    with owned_run(output,version='v6-ranked-structure-controller-v1'):
        write_json(output/'launch.json',dict(pid=os.getpid(),code=str(Path(__file__).resolve().parents[4]),
            arguments=sys.argv,started_utc=datetime.now(timezone.utc).isoformat(),session_workers=session_workers,ticker_workers=ticker_workers))
        console=Console()
        try:
            with Live(display(publish()),console=console,refresh_per_second=1) as live:
                while queued or active:
                    while queued and len(active)<session_workers and not failed:
                        day=queued.pop(0);folder=require_runtime(output/day)
                        if (folder/'complete.json').exists():raise ValueError('Existing day cannot be replaced')
                        if resume:
                            for name in ('worker.log','worker.err','launch.json','exit.json'):
                                path=folder/name
                                if path.is_file():shutil.copyfile(path,folder/(name+'.previous-'+uuid4().hex))
                        handles=[(folder/'worker.log').open('w'),(folder/'worker.err').open('w')]
                        args=[sys.executable,'-B','-u','-m','research.vectorized_backtest.v6.torch_backtest.sparse_structure',
                            '--inputs',str(inputs/day),'--output',str(folder),'--workers',str(ticker_workers)]
                        child=subprocess.Popen(args,stdout=handles[0],stderr=handles[1])
                        active[day]=(child,handles)
                        write_json(folder/'launch.json',dict(pid=child.pid,arguments=args,started_utc=datetime.now(timezone.utc).isoformat()))
                    for day,(child,handles) in list(active.items()):
                        code=child.poll()
                        if code is None:continue
                        for handle in handles:handle.close()
                        del active[day]
                        write_json(output/day/'exit.json',dict(exit_code=code,finished_utc=datetime.now(timezone.utc).isoformat()))
                        if code:failed[day]=f'worker exit {code}; see worker.err';continue
                        finished[day]=completed_day(output/day,inputs/day,day)
                    live.update(display(publish()))
                    if failed and not active:break
                    time.sleep(1)
        finally:
            for child,handles in active.values():
                if os.name=='nt':
                    subprocess.run(['taskkill','/PID',str(child.pid),'/T','/F'],capture_output=True,check=False)
                else:child.terminate()
                child.wait()
                for handle in handles:handle.close()
        if failed:
            write_json(output/'failure.json',dict(failures=failed,queued=queued,validation_opened=False))
            raise RuntimeError('Structural preparation failed; queued days were not launched')
        write_json(output/'complete.json',dict(status='complete',version='v6-ranked-structure-controller-v1',
            training_days=days,sessions=finished,validation_opened=False))


def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--inputs',type=Path,required=True);p.add_argument('--output',type=Path,required=True)
    p.add_argument('--session-workers',type=int,default=2);p.add_argument('--ticker-workers',type=int,default=4)
    p.add_argument('--resume',action='store_true',help='Verify completed days and reuse exact completed ticker caches')
    a=p.parse_args(argv);status=1
    try:
        run(a.inputs,a.output,session_workers=a.session_workers,ticker_workers=a.ticker_workers,resume=a.resume);status=0
    except BaseException as error:
        write_json(require_runtime(a.output)/'controller-error.json',dict(error=type(error).__name__,message=str(error),
            traceback=traceback.format_exc(),finished_utc=datetime.now(timezone.utc).isoformat()))
        raise
    finally:
        write_json(require_runtime(a.output)/'exit.json',dict(exit_code=status,finished_utc=datetime.now(timezone.utc).isoformat()))
    return status


if __name__=='__main__':raise SystemExit(main())
