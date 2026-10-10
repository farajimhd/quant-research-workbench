"""Visible bounded all30 history materialization; existing V7 remains untouched."""
import os
os.environ['PYTHONDONTWRITEBYTECODE']='1'
os.environ.setdefault('OMP_NUM_THREADS','1')
os.environ.setdefault('POLARS_MAX_THREADS','1')
import argparse,json,subprocess,sys,time
from pathlib import Path
from datetime import datetime,timezone
from rich.console import Console
from rich.live import Live
from .runtime import require_runtime,write_json,file_hash
from .materialize import owned_run
from .run_structure import training_days,validate_workers,display
from .history_bank import VERSION


def completed(folder,inputs,day):
    record=json.loads((folder/'complete.json').read_text())
    if (record['version']!=VERSION or record['day']!=day or record.get('validation_opened') is not False
            or record['input_receipt_sha256']!=file_hash(inputs/day/'complete.json')):raise ValueError('History session contract mismatch')
    for name,digest in record['files'].items():
        if Path(name).name!=name or file_hash(folder/name)!=digest:raise ValueError('History session hash mismatch')
    return dict(tickers=len(record['listing_ids']),resident_bytes=record['resident_bytes'],receipt_sha256=file_hash(folder/'complete.json'))


def profile_dependency_released(folder):
    """Accept a stopped profile only with bound stop evidence and no live owner."""
    import psutil
    folder=Path(folder)
    terminal=folder/'exit.json'
    if not terminal.exists():return False
    record=json.loads(terminal.read_text())
    active=json.loads((folder/'active.json').read_text())
    pid=int(active['pid']);creation=float(active['creation_time'])
    if psutil.pid_exists(pid):
        process=psutil.Process(pid)
        if abs(process.create_time()-creation)<0.01:
            raise ValueError('Profile exit receipt conflicts with live owner')
    if record.get('exit_code') not in (0,1):
        stop=json.loads((folder/'user-stop.json').read_text(encoding='utf-8-sig'))
        if (stop.get('pid')!=pid or abs(float(stop['verified_creation_time'])-creation)>0.01
                or stop.get('reason')!='User requested profiler stop; measurements partial'):
            raise ValueError('Profile stop identity requires explicit review')
    return True


def run(inputs,output,session_workers=16,ticker_workers=8,after_profile=None):
    validate_workers(session_workers,ticker_workers);inputs=Path(inputs);days=training_days(inputs)
    output=require_runtime(output)
    if any(output.iterdir()):raise ValueError('History controller requires new empty output; never overwrite')
    active={};done={};failed={};queue=list(days);start=time.perf_counter()
    def snapshot():
        sessions={d:dict(state='completed',completed=v['tickers'],total=v['tickers'],stage='verified and sealed') for d,v in done.items()}
        sessions.update({d:dict(state='queued',stage='awaiting session worker') for d in queue})
        sessions.update({d:dict(state='failed',stage=v) for d,v in failed.items()})
        for day,(child,handles) in active.items():
            try:item=json.loads((output/day/'status.json').read_text())
            except (FileNotFoundError,PermissionError):item=dict(stage='verify inputs / start ticker pool')
            sessions[day]=dict(item,state='running',pid=child.pid)
        value=dict(completed=len(done),active=len(active),queued=len(queue),failed=len(failed),total=30,
            sessions=dict(sorted(sessions.items())),elapsed_seconds=time.perf_counter()-start,validation_opened=False)
        write_json(output/'status.json',value)
        panel=display(value);panel.title='V6 persisted market history • 60-second windows • 121 swing pairs'
        return panel
    with owned_run(output,version=VERSION):
        write_json(output/'launch.json',dict(pid=os.getpid(),arguments=sys.argv,session_workers=session_workers,ticker_workers=ticker_workers,
            started_utc=datetime.now(timezone.utc).isoformat(),validation_opened=False))
        try:
            with Live(snapshot(),console=Console(),refresh_per_second=1) as live:
                while after_profile is not None:
                    if profile_dependency_released(after_profile):break
                    panel=snapshot();panel.title='V6 history queued • waiting for wall profiler to exit to preserve isolated timings'
                    live.update(panel);time.sleep(1)
                while queue or active:
                    while queue and len(active)<session_workers and not failed:
                        day=queue.pop(0);folder=require_runtime(output/day)
                        handles=[(folder/'worker.log').open('w'),(folder/'worker.err').open('w')]
                        args=[sys.executable,'-B','-u','-m','research.vectorized_backtest.v6.torch_backtest.materialize_history',
                            '--inputs',str(inputs/day),'--output',str(folder),'--workers',str(ticker_workers)]
                        child=subprocess.Popen(args,stdout=handles[0],stderr=handles[1]);active[day]=(child,handles)
                        import psutil
                        write_json(folder/'launch.json',dict(pid=child.pid,creation_time=psutil.Process(child.pid).create_time(),arguments=args))
                    for day,(child,handles) in list(active.items()):
                        code=child.poll()
                        if code is None:continue
                        for handle in handles:handle.close()
                        del active[day];write_json(output/day/'exit.json',dict(exit_code=code))
                        if code:failed[day]=f'worker exit {code}; inspect worker.err'
                        else:done[day]=completed(output/day,inputs,day)
                    live.update(snapshot());time.sleep(1)
                    if failed and not active:break
        finally:
            for child,handles in active.values():
                if os.name=='nt':subprocess.run(['taskkill','/PID',str(child.pid),'/T','/F'],capture_output=True,check=False)
                else:child.terminate()
                child.wait()
                for handle in handles:handle.close()
        if failed:raise RuntimeError('History preparation failed: '+str(failed))
        write_json(output/'complete.json',dict(status='complete',version=VERSION,sessions=done,training_days=days,
            resident_bytes=sum(v['resident_bytes'] for v in done.values()),validation_opened=False))


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--inputs',type=Path,required=True);p.add_argument('--output',type=Path,required=True)
    p.add_argument('--session-workers',type=int,default=16);p.add_argument('--ticker-workers',type=int,default=8)
    p.add_argument('--after-profile',type=Path,help='One-shot dependency: await existing profiler exit receipt, no recurring monitoring')
    a=p.parse_args();result=1
    try:run(a.inputs,a.output,a.session_workers,a.ticker_workers,a.after_profile);result=0
    finally:write_json(require_runtime(a.output)/'exit.json',dict(exit_code=result,finished_utc=datetime.now(timezone.utc).isoformat()))

if __name__=='__main__':main()
