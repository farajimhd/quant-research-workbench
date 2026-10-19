"""Persistent scheduled controller: certified bars -> plan -> profile -> V7 fill."""
import os
os.environ['PYTHONDONTWRITEBYTECODE']='1'
import sys
sys.dont_write_bytecode=True
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import argparse
from datetime import datetime,timezone
import json
import subprocess
import time

from scripts.build_market_day import build_lock,save,RUNTIME
from scripts.backfill_structural_sessions import read_json


def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--bars-run-dir',type=Path,required=True)
    p.add_argument('--run-dir',type=Path,required=True)
    p.add_argument('--endpoint',default='http://192.168.1.218:18123')
    p.add_argument('--start-date',default='2026-01-01')
    p.add_argument('--end-date')
    p.add_argument('--workers',type=int,choices=range(1,65),default=8)
    p.add_argument('--writer-compatibility-proof',type=Path)
    a=p.parse_args(argv)
    if os.environ.get('COMPUTERNAME','').upper()!='DESKTOP-SAAI85T':
        raise ValueError('Run the persistent controller on DESKTOP-SAAI85T')
    if not RUNTIME.is_dir() or not a.run_dir.resolve().is_relative_to(RUNTIME.resolve()):
        raise ValueError('Required runtime root is unavailable')
    a.run_dir.mkdir(parents=True,exist_ok=True)
    def state(phase,**kw):
        save(a.run_dir/'controller-state.json',dict(phase=phase,pid=os.getpid(),
            utc=datetime.now(timezone.utc).isoformat(),**kw))
    command=[sys.executable,'-B',str(Path(__file__).with_name('backfill_structural_sessions.py')),
        '--bars-run-dir',str(a.bars_run_dir),'--run-dir',str(a.run_dir),
        '--start-date',a.start_date,'--endpoint',a.endpoint,'--workers',str(a.workers)]
    frozen=a.run_dir/'structural-request.json'
    end=a.end_date or (read_json(frozen)['end'] if frozen.exists() else None)
    if end:command+=['--end-date',end]
    if a.writer_compatibility_proof:
        command+=['--resume','--writer-compatibility-proof',str(a.writer_compatibility_proof)]
    with build_lock(a.run_dir/'deferred-controller.lock'):
        state('waiting_for_bars')
        try:
            while True:
                if (a.run_dir/'STOP').exists():
                    state('interrupted');return 130
                path=a.bars_run_dir/'status.json'
                bars=read_json(path) if path.exists() else {}
                if bars.get('status')=='complete' and not bars.get('failed'):
                    # The bar owner must also have released its OS ownership.
                    try:
                        with build_lock(RUNTIME/'market-day-arte.lock'):pass
                    except (OSError,RuntimeError,ValueError):
                        time.sleep(20);continue
                    break
                time.sleep(20)
            with (a.run_dir/'build.log').open('a',encoding='utf-8') as log:
                for phase,extra in (('plan',['--plan-only']),('profile',['--profile-only']),
                    ('backfill',['--migrate-schema']+(['--resume'] if (a.run_dir/'structural-request.json').exists() else []))):
                    state(phase)
                    child=subprocess.Popen(command+extra,stdout=log,stderr=subprocess.STDOUT,
                        env={**os.environ,'PYTHONDONTWRITEBYTECODE':'1',
                             'OMP_NUM_THREADS':'1','OPENBLAS_NUM_THREADS':'1',
                             'MKL_NUM_THREADS':'1','NUMEXPR_NUM_THREADS':'1'})
                    state(phase,child_pid=child.pid)
                    code=child.wait()
                    if code:
                        status_path=a.run_dir/'status.json'
                        status=read_json(status_path) if phase=='backfill' and status_path.exists() else {}
                        if code==2 and status.get('status')=='blocked':
                            state('blocked',failed_phase=phase,returncode=code,
                                blocked_units=status['blocked_units'],blocked_tickers=status['blocked_tickers'])
                            return code
                        state('interrupted' if code==130 else 'failed',failed_phase=phase,returncode=code)
                        return code
            receipt=json.loads((a.run_dir/'structural-completion.json').read_text())
            status=json.loads((a.run_dir/'status.json').read_text())
            if (receipt['failed'] or status['status']!='complete'
                    or receipt['completed']+receipt['reused']!=status['total']):
                raise ValueError('Structural completion receipt does not reconcile')
            state('complete',completed=receipt['completed'],reused=receipt['reused'])
            return 0
        except BaseException as error:
            state('failed',error=type(error).__name__+': '+str(error))
            raise


if __name__=='__main__':
    result=main()
    raise SystemExit(result)
