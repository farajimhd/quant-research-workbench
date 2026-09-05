#!/usr/bin/env python3
"""Bounded workstation-only SDOT/ASST continuation profiling; no campaign restart."""
from __future__ import annotations
import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
import time
import uuid

sys.dont_write_bytecode = True
os.environ['PYTHONDONTWRITEBYTECODE'] = '1'
if __package__:
    from . import profile_structure_checkpoint_campaign as profile
else:
    import profile_structure_checkpoint_campaign as profile


def run_probe(binary, ticker, date, set_id, directory, env, max_events, seconds, grace=15):
    output = directory / f'{ticker}.json'
    log_path = directory / f'{ticker}.log'
    started = time.monotonic()
    stopped_at = None
    reason = None
    samples = []
    import psutil
    with log_path.open('w', encoding='utf-8') as log:
        process = subprocess.Popen([str(binary), ticker, date, set_id, str(output), str(max_events), str(seconds)],
                                   env=env, stdout=log, stderr=subprocess.STDOUT,
                                   creationflags=subprocess.CREATE_NEW_PROCESS_GROUP if os.name == 'nt' else 0)
        try:
            child = psutil.Process(process.pid)
        except psutil.NoSuchProcess:
            child = None
        next_update = 0
        try:
            while process.poll() is None:
                now = time.monotonic()
                if stopped_at is None and now-started >= seconds:
                    reason = 'deadline'
                    output.with_suffix('.stop').touch()
                    stopped_at = now
                    print(f'{ticker}: deadline reached; requesting stop between events.', flush=True)
                if stopped_at is not None and now-stopped_at >= grace:
                    # Only the process handle created by this launcher is killed.
                    process.kill()
                    process.wait(timeout=10)
                    reason = 'hard_deadline_single_event_or_io'
                    break
                if now >= next_update:
                    state = profile.campaign.read_status(output.with_suffix('.status.json')) or {}
                    try:
                        if child is None:
                            raise psutil.NoSuchProcess(process.pid)
                        sample = {'elapsed_seconds': now-started, 'cpu_seconds': sum(child.cpu_times()[:2]), 'rss_bytes': child.memory_info().rss}
                        samples.append(sample)
                        if sample['rss_bytes'] > 4*1024**3:
                            reason='memory_budget'
                            process.kill()
                            process.wait(timeout=10)
                            break
                    except psutil.NoSuchProcess:
                        pass
                    print(f"{ticker} | {int(now-started)}s | events {state.get('events', 0):,}/{max_events:,} | phase {state.get('phase', 'starting')} | queued 0 | stop requested {stopped_at is not None}", flush=True)
                    next_update = now + 2
                try:
                    process.wait(timeout=0.2)
                except subprocess.TimeoutExpired:
                    pass
        except KeyboardInterrupt:
            output.with_suffix('.stop').touch()
            try:
                process.wait(timeout=grace)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=10)
            reason = 'user_interrupted'
        finally:
            if process.poll() is None:
                output.with_suffix('.stop').touch()
                try:
                    process.wait(timeout=grace)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=10)
    result = profile.campaign.read_status(output) or {'status':'failed', 'error':'Probe exited without a final report'}
    result.update(exit_code=process.returncode, launcher_stop_reason=reason, process_samples=samples,
                  last_status=profile.campaign.read_status(output.with_suffix('.status.json')))
    if process.returncode != 0:
        result['status'] = 'failed'
    profile.campaign.atomic_json(directory / f'{ticker}-result.json', result)
    return result


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--binary', type=Path, required=True)
    parser.add_argument('--source-commit')
    parser.add_argument('--max-events', type=int, default=20_000)
    parser.add_argument('--seconds', type=int, default=180)
    args=parser.parse_args()
    if os.name != 'nt' or os.environ.get('COMPUTERNAME', '').upper() != 'DESKTOP-SAAI85T':
        raise RuntimeError('Run on DESKTOP-SAAI85T')
    import psutil
    import msvcrt
    if not 1 <= args.max_events <= 100_000 or not 1 <= args.seconds <= 600:
        raise RuntimeError('Limits: 1..100000 events, 1..600 seconds per ticker')
    root=Path(r'D:\TradingML\runtimes').resolve(strict=True)
    production=root / 'qmd_gateway/structure-checkpoint-campaign-v18-v3'
    if profile.campaign_processes(production):
        raise RuntimeError('Production workers remain; this probe does not stop them')
    binary=args.binary.resolve(strict=True)
    commit=profile.campaign.source_commit(args.source_commit)
    secrets=Path(r'D:\TradingML\secrets\.env')
    env=dict(os.environ, PYTHONDONTWRITEBYTECODE='1')
    if not env.get('DOTENV_PATHS'):
        if not secrets.is_file(): raise RuntimeError('Missing workstation secrets file')
        env['DOTENV_PATHS']=str(secrets)
    target=root / 'structure-validation'
    target.mkdir(exist_ok=True)
    # Lock lifetime equals launcher lifetime, including interruption and crashes.
    with (target / 'warm-replay.lock').open('a+b') as lock:
        lock.seek(0); lock.write(b'0'); lock.flush(); lock.seek(0)
        msvcrt.locking(lock.fileno(), msvcrt.LK_NBLCK, 1)
        directory=target / ('warm-replay-'+uuid.uuid4().hex[:12])
        directory.mkdir()
        manifest=profile.campaign.read_status(production / 'campaign-manifest.json')
        if not manifest: raise RuntimeError('Missing source campaign manifest')
        report={'status':'running','source_commit':commit,'binary_sha256':profile.campaign.sha256_file(binary),
                'source_set':manifest['checkpoint_set_id'],'max_events':args.max_events,'seconds_per_ticker':args.seconds,'runs':[]}
        profile.campaign.atomic_json(directory / 'report.json',report)
        print(f'Warm replay output: {directory}\nProduction remains stopped. Ctrl+C stops this probe only.',flush=True)
        for ticker,date in [('SDOT','2026-05-05'),('ASST','2025-05-07')]:
            result=run_probe(binary,ticker,date,manifest['checkpoint_set_id'],directory,env,args.max_events,args.seconds)
            report['runs'].append(result)
            profile.campaign.atomic_json(directory / 'report.json',report)
            if result['status']!='completed' or result.get('launcher_stop_reason')=='user_interrupted':
                report['status']='blocked'
                break
        else:
            report['status']='completed'
        profile.campaign.atomic_json(directory / 'report.json',report)
        print(f"{report['status']}: {directory / 'report.json'}",flush=True)
        return 0 if report['status']=='completed' else 1


if __name__=='__main__':
    try:
        raise SystemExit(main())
    except (RuntimeError,OSError,ValueError) as error:
        print(f'Warm replay stopped: {error}',file=sys.stderr)
        raise SystemExit(1)
