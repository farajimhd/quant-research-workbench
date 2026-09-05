#!/usr/bin/env python3
"""Gracefully stop the production campaign, then run isolated bounded profiles.

Run on the workstation with its ml4t Python. Never deletes checkpoints, force
kills production workers, or automatically resumes the full campaign.
"""
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
    from . import run_structure_checkpoint_campaign as campaign
else:
    import run_structure_checkpoint_campaign as campaign


def campaign_processes(runtime: Path) -> list[int]:
    """Include orphan workers; terminal status alone is insufficient."""
    import psutil
    found = []
    needle = str(runtime.resolve()).lower()
    identity = campaign.read_status(runtime / 'supervisor/supervisor.json') or {}
    supervisor = int(identity.get('pid', 0))
    for process in psutil.process_iter(['pid', 'name']):
        if process.pid == os.getpid():
            continue
        name = (process.info['name'] or '').lower()
        if process.pid != supervisor and 'structure_checkpoint_campaign' not in name and 'python' not in name:
            continue
        try:
            args = process.cmdline()
        except psutil.NoSuchProcess:
            continue
        except psutil.AccessDenied:
            if process.pid == supervisor or 'structure_checkpoint_campaign' in name:
                raise RuntimeError(f'Cannot inspect candidate campaign PID {process.pid}; no replacement will start')
            continue
        if any(arg.lower() == needle or arg.lower().startswith((needle+'\\', needle+'/')) for arg in args):
            found.append(process.pid)
    return found


def graceful_stop(runtime: Path, timeout: float) -> None:
    manifest = campaign.read_status(runtime / 'campaign-manifest.json')
    if not manifest:
        raise RuntimeError(f'Missing campaign manifest: {runtime}')
    campaign.request_campaign_stop(runtime, manifest['checkpoint_set_id'], 'graceful')
    deadline = time.monotonic() + timeout
    while pids := campaign_processes(runtime):
        print(f'Graceful stop | {len(pids)} processes remaining | finish current ticker-day | {max(0, int(deadline-time.monotonic()))}s left', flush=True)
        if time.monotonic() >= deadline:
            raise RuntimeError('Graceful-stop deadline reached. Stop request remains active; no workers killed and no next run started.')
        time.sleep(min(10, max(0.1, deadline-time.monotonic())))
    print('Graceful stop complete | no matching supervisor or workers remain', flush=True)


def summarize_profiles(runtime: Path, expected: set[str]) -> dict:
    phases = {name: 0.0 for name in ('fetch_ms', 'apply_ms', 'prepare_ms', 'certify_ms', 'persist_ms', 'total_ms')}
    hashes, events, retries = {}, 0, 0
    persistence = dict(encode_validate_ms=0.0, send_retry_ms=0.0, body_bytes=0)
    for path in sorted((runtime / 'workers').glob('worker-*/worker.log')):
        for line in path.open(encoding='utf-8', errors='replace'):
            try:
                row = json.loads(line)
            except ValueError:
                continue
            if not isinstance(row, dict):
                continue
            if row.get('event') == 'checkpoint_write_attempt_failed':
                retries += 1
            if row.get('event') == 'checkpoint_persist_profile':
                for field in persistence:
                    persistence[field] += row[field]
            if row.get('event') != 'campaign_day_profile':
                continue
            key = f"{row['ticker']}:{row['session_date']}"
            if key in hashes:
                raise RuntimeError(f'Duplicate completed profile {key}; inspect retries before comparing')
            hashes[key] = row['checkpoint_sha256']
            events += row['events']
            for name in phases:
                phases[name] += row[name]
    if {key.split(':')[0] for key in hashes} != expected or len(hashes) != len(expected):
        raise RuntimeError('Profile coverage is incomplete; no performance recommendation is valid')
    return {'phase_worker_ms': phases, 'persistence': persistence, 'checkpoint_hashes': hashes, 'events': events, 'write_attempt_failures': retries}


def clickhouse_request(env: dict, sql: str, *, readonly: bool = False, parameters: dict | None = None):
    import requests
    from dotenv import dotenv_values
    config = {}
    for filename in env['DOTENV_PATHS'].split(os.pathsep):
        for key, value in dotenv_values(filename).items():
            config.setdefault(key, value)
    config.update(env)
    def first(keys, default=''):
        return next((config[k] for k in keys if config.get(k)), default)
    url = first(['QMD_CLICKHOUSE_URL', 'REAL_LIVE_CLICKHOUSE_WRITE_URL', 'CLICKHOUSE_URL', 'CLICKHOUSE_ENDPOINT'])
    user = first(['QMD_CLICKHOUSE_USER', 'REAL_LIVE_CLICKHOUSE_WRITE_USER', 'CLICKHOUSE_WORKSTATION_USER', 'CLICKHOUSE_USER'], 'default')
    password = first(['QMD_CLICKHOUSE_PASSWORD', 'REAL_LIVE_CLICKHOUSE_WRITE_PASSWORD', 'CLICKHOUSE_WORKSTATION_PASSWORD', 'CLICKHOUSE_PASSWORD'])
    if not url:
        raise RuntimeError('Missing writer URL')
    # Never log connection credentials or response bodies.
    params = {'max_execution_time': 60, **(parameters or {})}
    if readonly:
        params.update(readonly=1, max_threads=1, max_memory_usage=1024*1024*1024, max_result_bytes=512*1024*1024, result_overflow_mode='throw')
    response = requests.post(url, auth=(user, password), data=sql, params=params, timeout=90)
    if response.status_code != 200:
        raise RuntimeError(f'ClickHouse profiling request failed (HTTP {response.status_code})')
    return response


def create_profile_database(env: dict, database: str) -> None:
    if not database.startswith('qmd_structure_profile_') or not database.replace('_', '').isalnum():
        raise RuntimeError('Invalid isolated database name')
    response = clickhouse_request(env, f'CREATE DATABASE `{database}`')
    if response.text.strip():
        raise RuntimeError('ClickHouse returned an error body during isolated database creation')
    env['QMD_CLICKHOUSE_DATABASE'] = database
    env['QMD_HISTORY_STRUCTURE_DATABASE'] = database


def profile_mature_checkpoints(env: dict, args, run_root: Path) -> list[dict]:
    manifest = campaign.read_status(args.current_runtime / 'campaign-manifest.json')
    results = []
    for ticker in ('JUNS', 'SUGP'):
        response = clickhouse_request(env,
            'SELECT session_date, snapshot_json, certification_json FROM {database:Identifier}.qmd_structure_daily_checkpoint_v2 FINAL '
            'WHERE checkpoint_set_id={set:String} AND sym={ticker:String} AND algorithm_version=18 '
            'AND source_complete=1 AND notEmpty(certification_json) AND session_date = '
            '(SELECT max(session_date) FROM {database:Identifier}.qmd_structure_daily_checkpoint_v2 FINAL '
            'WHERE checkpoint_set_id={set:String} AND sym={ticker:String} AND algorithm_version=18 AND source_complete=1 AND notEmpty(certification_json)) '
            'ORDER BY built_at DESC LIMIT 1 FORMAT JSONEachRow',
            readonly=True, parameters={'param_database': args.production_database, 'param_set': manifest['checkpoint_set_id'], 'param_ticker': ticker})
        if not response.text.strip():
            results.append({'ticker': ticker, 'status': 'unavailable_in_current_set'})
            continue
        row = response.json()
        capture = run_root / f'{ticker}-certified-checkpoint.json'
        campaign.atomic_json(capture, row)
        result = subprocess.run([str(args.checkpoint_probe), str(capture)], env=env, capture_output=True, text=True, timeout=300)
        (run_root / f'{ticker}-cost-probe.log').write_text(result.stdout+'\n'+result.stderr, encoding='utf-8')
        if result.returncode:
            raise RuntimeError(f'{ticker} mature checkpoint parity/probe failed; inspect its cost-probe.log')
        results.append(json.loads(result.stdout))
        print(f'Mature checkpoint {ticker} | parity passed | CPU phase timings saved', flush=True)
    return results


def run_owned(command: list[str], env: dict, runtime: Path, log: Path, timeout: float, grace: float) -> dict:
    import psutil
    started = time.monotonic()
    last_report = started - 10
    last_sample = started
    cpu_samples, memory_samples = [], []
    psutil.cpu_percent()
    with log.open('w', encoding='utf-8') as output:
        process = subprocess.Popen(command, env=env, stdout=output, stderr=subprocess.STDOUT,
                                   creationflags=subprocess.CREATE_NEW_PROCESS_GROUP if os.name == 'nt' else 0)
        try:
            while process.poll() is None:
                if time.monotonic() - started > timeout:
                    raise TimeoutError('Bounded run exceeded its deadline')
                now = time.monotonic()
                if now - last_report >= 10:
                    status = campaign.read_status(runtime / 'campaign-status.json') or {}
                    counts = status.get('counts', {})
                    print(f"Profile {runtime.name} | {int(now-started)}s | active {counts.get('active', 0)} | queued {counts.get('queued', 0)} | completed {counts.get('completed', 0)} | skipped {counts.get('skipped', 0)} | retried {counts.get('retried', 0)} | failed {counts.get('failed', 0)}", flush=True)
                    last_report = now
                if now - last_sample >= 10:
                    cpu_samples.append(psutil.cpu_percent())
                    memory_samples.append(psutil.virtual_memory().used)
                    last_sample = now
                try:
                    process.wait(timeout=1)
                except subprocess.TimeoutExpired:
                    pass
        except BaseException:
            # Do not propagate Ctrl+C into the supervisor's fast-stop handler.
            # If still planning, wait for a manifest before writing its control.
            deadline = time.monotonic() + grace
            while process.poll() is None and not (runtime / 'campaign-manifest.json').exists():
                if time.monotonic() >= deadline:
                    raise RuntimeError(f'Planner still running; see {log}. No next run started.')
                time.sleep(1)
            if process.poll() is None:
                graceful_stop(runtime, max(1, deadline-time.monotonic()))
            raise
        if process.returncode:
            raise RuntimeError(f'Profile failed with exit {process.returncode}; inspect {log}')
    if campaign_processes(runtime):
        raise RuntimeError('Child processes remain; refusing to overlap profiles')
    return {'wall_seconds': time.monotonic()-started, 'host_cpu_percent_samples': cpu_samples,
            'host_memory_used_bytes_samples': memory_samples}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--current-runtime', type=Path, default=Path(r'D:\TradingML\runtimes\qmd_gateway\structure-checkpoint-campaign-v18-v3'))
    parser.add_argument('--binary', type=Path, required=True, help='New profiled algorithm-18 executable')
    parser.add_argument('--source-commit')
    parser.add_argument('--checkpoint-probe', type=Path, help='Defaults to structure_checkpoint_cost_probe.exe beside --binary')
    parser.add_argument('--production-database', default='q_live')
    parser.add_argument('--date', default='2026-08-21')
    parser.add_argument('--workers', type=int, nargs='+', default=[16, 32, 64, 96])
    parser.add_argument('--max-events', type=int, default=20_000_000)
    parser.add_argument('--grace-seconds', type=int, default=1800)
    parser.add_argument('--run-seconds', type=int, default=1800)
    args = parser.parse_args()
    import psutil  # preflight before stopping anything
    import rich
    import requests
    import dotenv
    if os.name != 'nt':
        raise RuntimeError('Run this launcher on the Windows workstation')
    if os.environ.get('COMPUTERNAME', '').upper() != 'DESKTOP-SAAI85T':
        raise RuntimeError('Run on DESKTOP-SAAI85T; this launcher must not stop laptop services')
    root = Path(r'D:\TradingML\runtimes').resolve(strict=True)
    if not args.current_runtime.resolve(strict=True).is_relative_to(root):
        raise RuntimeError('Campaign must be under D:\\TradingML\\runtimes')
    if not args.workers or any(w < 1 or w > 96 for w in args.workers) or len(set(args.workers)) != len(args.workers):
        raise RuntimeError('Use unique worker counts between 1 and 96')
    if min(args.max_events, args.grace_seconds, args.run_seconds) <= 0:
        raise RuntimeError('Work and time bounds must be positive')
    commit = campaign.source_commit(args.source_commit)
    binary = args.binary.resolve(strict=True)
    args.checkpoint_probe = (args.checkpoint_probe or binary.with_name('structure_checkpoint_cost_probe.exe')).resolve(strict=True)
    build = json.loads(subprocess.check_output([str(binary), '--campaign-build-info'], text=True))
    if build.get('algorithm_version') != 18 or build.get('campaign_version') != campaign.CAMPAIGN_VERSION or build.get('profile_schema_version') != 1:
        raise RuntimeError('Wrong campaign executable')
    source_plan = json.loads((args.current_runtime / 'planner/campaign-plan.json').read_text(encoding='utf-8'))
    # One full source day, same frozen tradable universe in every comparison.
    candidates = [p for p in source_plan if args.date in p['sessions'] and p['estimated_events'] > 0]
    candidates = [p for p in candidates if 100 <= p['estimated_events']/max(1, len(p['sessions'])) <= args.max_events/max(args.workers)/2]
    candidates.sort(key=lambda p: (-p['estimated_events']/max(1, len(p['sessions'])), p['ticker']))
    selected = candidates[:max(args.workers)]
    if len(selected) != max(args.workers):
        raise RuntimeError('Too few active tickers for the requested worker comparison')
    run_root = root / 'structure-validation' / ('campaign-profile-' + uuid.uuid4().hex[:12])
    run_root.mkdir(parents=True)
    tickers = run_root / 'tickers.txt'
    tickers.write_text('\n'.join(p['ticker'] for p in selected)+'\n', encoding='utf-8')
    report = {'source_commit': commit, 'binary_sha256': campaign.sha256_file(binary), 'date': args.date,
              'production_runtime': str(args.current_runtime), 'runs': [],
              'limitation': 'Cold one-day replay; not a full-history ETA. Production stays stopped. No checkpoints deleted.'}
    campaign.atomic_json(run_root / 'report.json', report)
    env = dict(os.environ, QMD_CAMPAIGN_PROFILE='1', PYTHONDONTWRITEBYTECODE='1')
    if 'DOTENV_PATHS' not in env:
        secrets = Path(r'D:\TradingML\secrets\.env')
        if not secrets.is_file():
            raise RuntimeError('Missing workstation secrets file; configure DOTENV_PATHS before running')
        env['DOTENV_PATHS'] = str(secrets)
    print(f'Profile output: {run_root}\nProduction will remain stopped after profiling.', flush=True)
    if clickhouse_request(env, 'SELECT 1 FORMAT TSV', readonly=True).text.strip() != '1':
        raise RuntimeError('ClickHouse preflight failed; production was not stopped')
    graceful_stop(args.current_runtime, args.grace_seconds)
    report['mature_checkpoint_profiles'] = profile_mature_checkpoints(env, args, run_root)
    campaign.atomic_json(run_root / 'report.json', report)
    database = 'qmd_structure_profile_' + run_root.name.rsplit('-', 1)[1]
    create_profile_database(env, database)
    report['isolated_database'] = database
    campaign.atomic_json(run_root / 'report.json', report)
    base = ['--start-date', args.date, '--end-date', args.date, '--ticker-file', str(tickers), '--explicit-universe-only']
    plan_dir = run_root / 'planning'
    plan_dir.mkdir()
    # Planning reads canonical ClickHouse authority only. No checkpoint writes.
    with (run_root / 'planning.log').open('w', encoding='utf-8') as log:
        subprocess.run([str(binary), *base, '--checkpoint-set-id', run_root.name+'-plan', '--runtime-dir', str(plan_dir), '--plan-only'], env=env, stdout=log, stderr=subprocess.STDOUT, check=True, timeout=args.run_seconds)
    plan_path = plan_dir / 'campaign-plan.json'
    plan = json.loads(plan_path.read_text(encoding='utf-8'))
    # Fail instead of silently running a much larger experiment.
    total = sum(p['estimated_events'] for p in plan)
    if total > args.max_events:
        raise RuntimeError(f'Frozen sample has {total:,} events, above cap {args.max_events:,}. Production remains stopped. Review {plan_path} before explicitly raising --max-events or reducing --workers.')
    expected = {p['ticker'] for p in plan}
    if len(expected) != max(args.workers):
        raise RuntimeError('Planner changed the frozen sample; refusing an unequal comparison')
    reference = None
    for workers in args.workers:
        runtime = run_root / f'workers-{workers}'
        runtime.mkdir()
        set_id = f'{run_root.name}-w{workers}'
        command = [sys.executable, str(Path(campaign.__file__)), '--binary', str(binary), '--no-build', '--source-commit', commit,
                   '--foreground-supervisor', '--process-workers', str(workers), '--workers', str(workers),
                   *base, '--plan-file', str(plan_path), '--checkpoint-set-id', set_id, '--runtime-dir', str(runtime)]
        measurement = run_owned(command, env, runtime, run_root / f'workers-{workers}.log', args.run_seconds, args.grace_seconds)
        result = summarize_profiles(runtime, expected)
        if reference is not None and result['checkpoint_hashes'] != reference:
            raise RuntimeError('Checkpoint parity failed across worker counts; inspect retained outputs')
        reference = result['checkpoint_hashes']
        result.update(workers=workers, **measurement, events_per_second=result['events']/measurement['wall_seconds'])
        report['runs'].append(result)
        campaign.atomic_json(run_root / 'report.json', report)
        print(f"Completed {workers} workers | {result['events_per_second']:,.0f} events/s | write retries {result['write_attempt_failures']} | hashes match", flush=True)
    report['status'] = 'completed'
    campaign.atomic_json(run_root / 'report.json', report)
    print(f'Profiles complete: {run_root / "report.json"}\nProduction remains stopped; review measurements before choosing concurrency.', flush=True)
    return 0


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except (RuntimeError, OSError, ValueError, subprocess.SubprocessError) as exc:
        print(f'Profiling stopped: {exc}', file=sys.stderr)
        raise SystemExit(1)
