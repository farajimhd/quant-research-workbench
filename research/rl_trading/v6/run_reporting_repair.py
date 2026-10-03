"""Restart-safe workstation repair: event flags, immutable bars, V6 banks, labels.

Does not train, inspect sealed-test labels, or restart the laptop app. An already
running ingestion migration is awaited rather than competing for its lock.
"""
from __future__ import annotations

import argparse
from datetime import date, datetime, timezone
import json
import os
from pathlib import Path
import subprocess
import sys
import time

os.environ['PYTHONDONTWRITEBYTECODE'] = '1'
os.environ.setdefault('POLARS_MAX_THREADS', '1')
os.environ.setdefault('OMP_NUM_THREADS', '1')
sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from scripts import backfill_trade_reporting_flags as flags
from scripts import build_market_day as bars
from research.rl_trading.v6.opportunity_dataset import write_json

ROOT = Path('D:/TradingML/runtimes')
STAGES = ('event_flags', 'source_audit', 'canary_bars', 'canary_audit',
          'bars', 'bar_audit', 'feature_banks', 'labels', 'publication_audit')


def bar_arguments(runtime, *, canary=False, plan=False):
    arguments = ['--start-date', '2026-07-30', '--end-date', '2026-09-18',
        '--runtime', str(runtime), '--env-file', 'D:/TradingML/secrets/.env',
        '--workers', '2' if canary else '32', '--max-threads', '1',
        '--max-memory-gb', '2', '--query-timeout', '1800',
        '--allow-carried-forward-universe', '--progress', 'text']
    if canary:
        arguments += ['--tickers', 'NVDA,AAPL']
    if plan:
        return arguments + ['--plan-only']
    latest = runtime/'latest.json'
    if latest.is_file():
        previous = json.loads(latest.read_text())
        if previous.get('build_id') and previous.get('status') != 'preflight':
            return arguments + ['--build-id', previous['build_id']]
    return arguments + ['--rebuild']


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--output', type=Path, default=ROOT/'rl-v6-reporting-repair-20261002')
    p.add_argument('--source-commit', required=True)
    p.add_argument('--await-feature-bank-pid',type=int,
        help='Wait for the already-owned workstation bank worker before resuming; never duplicate it')
    p.add_argument('--await-bar-verification-pid',type=int,
        help='Retain the owned bar verifier and await its successful completion without restarting its checks')
    a = p.parse_args(argv)
    if a.await_feature_bank_pid is not None and a.await_bar_verification_pid is not None:
        raise ValueError('Only one existing worker can be handed off')
    output = a.output.resolve()
    if not ROOT.is_dir() or not output.is_relative_to(ROOT.resolve()):
        raise ValueError('Available workstation runtime root required')
    if len(a.source_commit) != 40 or any(c not in '0123456789abcdef' for c in a.source_commit):
        raise ValueError('Exact pushed source commit required')
    output.mkdir(parents=True, exist_ok=True)
    os.environ['QW_RUNTIME_ROOT'] = str(ROOT)
    # Explicit IPv4 endpoint; localhost may resolve to an unbound IPv6 listener.
    os.environ['QMD_CLICKHOUSE_URL'] = 'http://127.0.0.1:8123'
    os.environ['REAL_LIVE_CLICKHOUSE_WRITE_URL'] = 'http://127.0.0.1:8123'
    repository = Path(__file__).resolve().parents[3]
    inventory_path = output/'original-inputs.json'
    if not inventory_path.exists():
        original = json.loads((ROOT/'rl-v6-active-labels.json').read_text())
        dataset = json.loads(Path(original['dataset']).read_text())
        old_builds = sorted({json.loads((Path(e['bank_root'])/'plan.json').read_text())['source_build_id']
            for e in [dataset['context']]+dataset['days']})
        write_json(inventory_path,dict(active_labels=original,original_bar_build_ids=old_builds))
    inventory = json.loads(inventory_path.read_text())
    audit_original = [item for build in inventory['original_bar_build_ids'] for item in ('--previous-build-id',build)]
    state_path = output/'progress.json'
    done = []
    state = dict(version='rl-v6-reporting-repair-v1', source_commit=a.source_commit,
        source_root=str(repository), started_at=datetime.now(timezone.utc).isoformat())

    def progress(stage, status='running', **extra):
        value = dict(state, stage=stage, status=status, completed=done.copy(),
            active=[] if status in ('failed','complete') else [stage],
            queued=[s for s in STAGES if s not in done and s != stage],
            failed=[stage] if status == 'failed' else [],
            updated_at=datetime.now(timezone.utc).isoformat(), **extra)
        write_json(state_path,value)
        print(json.dumps(value),flush=True)

    def run(stage, arguments):
        progress(stage)
        command = [sys.executable, '-u', '-B', *arguments]
        with (output/(stage+'.log')).open('a',encoding='utf-8') as log:
            process = subprocess.Popen(command,cwd=repository,stdout=log,stderr=subprocess.STDOUT)
            progress(stage,pid=process.pid,log=str(output/(stage+'.log')))
            while process.poll() is None:
                time.sleep(15)
                progress(stage,pid=process.pid,log=str(output/(stage+'.log')))
            if process.returncode:
                raise RuntimeError(f'{stage} exited {process.returncode}; inspect {output/(stage+".log")}')
        done.append(stage)

    client = None
    retained_bars = None
    if a.await_bar_verification_pid is not None:
        retained_bars=json.loads((output/'bars/latest.json').read_text())
    stage = 'event_flags'
    try:
        with flags.exclusive(output):
            wait_pid=a.await_bar_verification_pid if a.await_bar_verification_pid is not None else a.await_feature_bank_pid
            if wait_pid is not None:
                if os.name!='nt' or wait_pid<=0:
                    raise ValueError('Positive Windows feature worker PID required')
                import ctypes
                from ctypes import wintypes
                kernel=ctypes.WinDLL('kernel32',use_last_error=True)
                kernel.OpenProcess.argtypes=[wintypes.DWORD,wintypes.BOOL,wintypes.DWORD]
                kernel.OpenProcess.restype=wintypes.HANDLE
                kernel.WaitForSingleObject.argtypes=[wintypes.HANDLE,wintypes.DWORD]
                kernel.GetExitCodeProcess.argtypes=[wintypes.HANDLE,ctypes.POINTER(wintypes.DWORD)]
                kernel.CloseHandle.argtypes=[wintypes.HANDLE]
                handle=kernel.OpenProcess(0x100000|0x1000,False,wait_pid)
                if not handle: raise OSError('Existing owned worker must still be running at handoff')
                try:
                    stage='bars' if retained_bars else 'feature_banks'
                    if retained_bars:
                        ownership=json.loads((output/'bar-worker-handoff.json').read_text())
                        if ownership['pid']!=wait_pid or ownership['build_id']!=retained_bars['build_id']:
                            raise ValueError('Bar handoff ownership differs from exact retained build')
                        creation=wintypes.FILETIME();exit_time=wintypes.FILETIME()
                        kernel_time=wintypes.FILETIME();user_time=wintypes.FILETIME()
                        kernel.GetProcessTimes.argtypes=[wintypes.HANDLE,*([ctypes.POINTER(wintypes.FILETIME)]*4)]
                        if not kernel.GetProcessTimes(handle,ctypes.byref(creation),ctypes.byref(exit_time),ctypes.byref(kernel_time),ctypes.byref(user_time)):
                            raise OSError('Cannot verify bar worker creation time')
                        if ((creation.dwHighDateTime<<32)|creation.dwLowDateTime) != ownership['creation_filetime']:
                            raise ValueError('Bar worker PID was reused')
                    while True:
                        progress(stage,'awaiting_existing_bar_verifier' if retained_bars else 'awaiting_existing_feature_worker',pid=wait_pid)
                        outcome=kernel.WaitForSingleObject(handle,15000)
                        if outcome==0: break
                        if outcome!=258: raise OSError('Cannot wait for existing feature worker')
                    code=wintypes.DWORD()
                    if not kernel.GetExitCodeProcess(handle,ctypes.byref(code)) or code.value:
                        raise RuntimeError(f'Existing {stage} worker exited {code.value}; inspect its stage log')
                    if retained_bars:
                        finished=json.loads((output/'bars/latest.json').read_text())
                        if (finished.get('status')!='core_complete' or
                            finished.get('build_id')!=retained_bars['build_id'] or
                            bars.digest(finished['definition'])!=bars.digest(retained_bars['definition'])):
                            raise ValueError('Retained bar verifier did not complete its exact build')
                        done.append('bars')
                finally: kernel.CloseHandle(handle)
                stage='event_flags'
            arguments = flags.parse_args(['--start-date','2026-07-01','--end-date','2026-07-31'])
            arguments.env_file = Path('D:/TradingML/secrets/.env')
            client = bars.Client(arguments,persistent=False)
            while True:
                rows = client.query(f"SELECT source_date,status FROM {flags.COVERAGE} FINAL WHERE source_date BETWEEN '2026-07-01' AND '2026-07-31' AND revision={flags.lit(flags.REVISION)}")
                if len(rows) == 22 and all(r['status']=='complete' for r in rows):
                    done.append(stage)
                    break
                try:
                    with flags.exclusive(ROOT/'trade-reporting-flags'):
                        pass
                except OSError:
                    progress(stage,status='awaiting_existing_migration',
                        certified=sum(r['status']=='complete' for r in rows),
                        staged=sum(r['status']=='staged' for r in rows),total=22)
                    time.sleep(15)
                    continue
                run(stage,['scripts/backfill_trade_reporting_flags.py',
                    '--start-date','2026-07-01','--end-date','2026-07-31',
                    '--env-file','D:/TradingML/secrets/.env',
                    '--runtime',str(ROOT/'trade-reporting-flags'),
                    '--cache-flatfiles-root-win','D:/market-data/flatfiles/us_stocks_sip',
                    '--max-threads','4','--max-memory-gb','8','--progress','text'])
                break
            stage = 'source_audit'; progress(stage)
            reporting = bars.reporting_coverage(client,date(2026,7,1),date(2026,9,18))
            if len(reporting) != 56:
                raise ValueError('Expected 56 certified July–September source sessions')
            write_json(output/'source-audit.json',dict(status='passed',coverage=reporting))
            done.append(stage)
            stage = 'canary_bars'
            run(stage,['scripts/build_market_day.py',*bar_arguments(output/'canary-bars',canary=True)])
            stage = 'canary_audit'
            run(stage,['scripts/audit_market_day_reporting_repair.py','--manifest',str(output/'canary-bars/latest.json'),
                '--output',str(output/'canary-audit.json'),'--tickers','NVDA,AAPL',*audit_original])
            stage = 'bars'
            if not retained_bars:
                run(stage,['scripts/build_market_day.py',*bar_arguments(output/'bars')])
            stage = 'bar_audit'
            run(stage,['scripts/audit_market_day_reporting_repair.py','--manifest',str(output/'bars/latest.json'),
                '--output',str(output/'bar-audit.json'),*audit_original])
            stage = 'feature_banks'
            manifest = str(output/'bars/latest.json')
            run(stage,['-m','research.rl_trading.v6.build_campaign','--early-manifest',manifest,
                '--late-manifest',manifest,'--ledger',str(output/'build-ledger-v2.sqlite3'),
                '--output-root',str(output/'banks-us-listed-v1'),'--workers','16'])
            stage = 'labels'
            run(stage,['research/rl_trading/v6/run_prepare_labels.py',
                '--source-manifest',str(output/'banks-us-listed-v1/day-roots.json'),
                '--output',str(output/'labels-us-listed-v1'),'--workers','8','--listings-per-shard','32',
                '--source-commit',a.source_commit])
            stage = 'publication_audit'; progress(stage)
            active = json.loads((ROOT/'rl-v6-active-labels.json').read_text())
            if Path(active['dataset']).resolve() != (output/'labels-us-listed-v1/dataset.json').resolve():
                raise ValueError('Active labels do not point to repaired publication')
            audit = json.loads((output/'labels-us-listed-v1/publication-audit.json').read_text())
            if audit['status'] != 'passed':
                raise ValueError('Label publication audit failed')
            done.append(stage)
            progress(stage,'complete',dataset=active['dataset'],dataset_sha256=active['sha256'])
    except Exception as error:
        progress(stage,'failed',reason=str(error))
        raise
    finally:
        if client is not None:
            client.close()
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
