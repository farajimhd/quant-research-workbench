"""Bounded, resumable research runs against an immutable V7 candidate.

Uses the application's real backtest/OMS path; never scores a candle-only proxy.
Artifacts and trial recipes live outside source. Candidate 206 is the default
baseline, not a mutable copy of whichever candidate happens to be latest.
"""
import os
os.environ['PYTHONDONTWRITEBYTECODE'] = '1'
import sys
sys.dont_write_bytecode = True
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import argparse
from copy import deepcopy
import json
from math import isfinite
import time
import requests

BASELINE = '4a288104-3351-411e-ab3b-73a2cf8e11ce'
PROFILE = 'v7-setup-recovery-v9'
PLAN = PROFILE + '-backtest'
TERMINAL = {'completed', 'failed', 'stopped', 'cancelled'}


def save(path, value):
    temporary = path.with_suffix('.tmp')
    temporary.write_text(json.dumps(value, indent=2), encoding='utf-8')
    temporary.replace(path)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--runtime', type=Path, required=True)
    parser.add_argument('--baseline', default=BASELINE)
    parser.add_argument('--prepare-only', action='store_true')
    parser.add_argument('--spread-bps', type=float, help='Override current trading spread gate; retain admission policy')
    args = parser.parse_args()
    root = args.runtime.resolve()
    root.relative_to(Path('D:/TradingML/runtimes').resolve())
    recipe = json.loads((root / 'recipe.json').read_text())
    manifest = root / 'manifest.json'
    state = json.loads(manifest.read_text()) if manifest.exists() else dict(baseline=args.baseline, recipe=recipe, trials=[])
    if args.spread_bps is not None and (not isfinite(args.spread_bps) or args.spread_bps <= 0):
        raise ValueError('Spread limit must be finite and positive')
    if manifest.exists() and state.get('spread_bps') != args.spread_bps:
        raise ValueError('Pinned spread limit changed; use a new study directory')
    state['spread_bps'] = args.spread_bps
    if state['baseline'] != args.baseline or state['recipe'] != recipe:
        raise ValueError('Pinned baseline or recipe changed; use a new study directory')
    if state.get('launching'):
        raise ValueError('Uncertain run launch; reconcile the recorded request before resuming')
    from src.backend.trading_configuration_service import configuration_candidate, configuration_base, create_test_candidate
    from src.trading_runtime.historical_hod import DEFAULTS
    baseline = configuration_candidate(args.baseline, required=True)
    if state.get('baseline_hash', baseline['content_hash']) != baseline['content_hash']:
        raise ValueError('Baseline content changed')
    state['baseline_hash'] = baseline['content_hash']
    # Historical releases also embed unrelated published profiles. Preserve
    # today's immutable catalog for those; the selected strategy remains pinned.
    published = {p['profile_id']:p for p in configuration_base()['strategy']['profiles']
                 if p.get('publication_status') == 'published'}
    if PROFILE in published:
        raise ValueError('Study profile is now published; clone it explicitly')
    for name, changes in recipe.items():
        if any(t['name'] == name for t in state['trials']):
            continue
        payload = deepcopy(baseline['payload'])
        payload['strategy']['profiles'] = [deepcopy(published.get(p['profile_id'], p))
                                           for p in payload['strategy']['profiles']]
        profile = next(p for p in payload['strategy']['profiles'] if p['profile_id'] == PROFILE)
        settings = profile['parameters']['historical_hod']
        if set(changes) - set(DEFAULTS):
            raise ValueError(f'Unknown parameter in {name}')
        settings.update(changes)
        if args.spread_bps is not None:
            profile['parameters']['liquidity_admission'].update(
                maximum_current_spread_bps=args.spread_bps,maximum_spread_bps=args.spread_bps)
        candidate = create_test_candidate(label=f'V7 study / {name}',
            canvas_revision=payload['canvas']['revision'], canvas_profile=payload['canvas']['profile'],
            configuration=payload, run_plan_id=PLAN, strategy_profile_id=PROFILE)
        state['trials'].append(dict(name=name, changes=changes, candidate_id=candidate['candidate_id'],
            revision=candidate['candidate_revision'], runs=[]))
        save(manifest, state)
    if args.prepare_only:
        print('Prepared candidates:', [(t['name'],t['revision']) for t in state['trials']], flush=True)
        return
    print(f'Spread gate: {args.spread_bps:g} bps' if args.spread_bps is not None else 'Spread gate: frozen baseline', flush=True)
    session = requests.Session()
    base = 'http://127.0.0.1:8000/api/trading/backtest/runs'
    while True:
        active = []
        failed = []
        for trial in state['trials']:
            for run in trial['runs']:
                if run.get('status') not in TERMINAL:
                    response = session.get(f"{base}/{run['run_id']}?compact=true", timeout=30)
                    response.raise_for_status()
                    snapshot = response.json()
                    run.update({k:snapshot.get(k) for k in ('status', 'progress', 'current_time', 'error')})
                if run['status'] == 'completed' and 'performance' not in run:
                    response = session.get(f"{base}/{run['run_id']}/results", params={'symbol':run['symbol']}, timeout=60)
                    response.raise_for_status()
                    report = response.json()['performance_journal']
                    run['performance'] = report['summary']
                    run['episodes'] = [{k:e.get(k) for k in ('opened_at','closed_at','net_pnl',
                        'entry_price','exit_price','quantity','exit_reason','mae','mfe','planned_risk','risk_multiple')}
                        for e in report['episodes']]
                if run['status'] not in TERMINAL:
                    active.append(run)
                elif run['status'] != 'completed':
                    failed.append(run)
        save(manifest, state)
        if failed:
            raise RuntimeError(f'Trial failed; preserve evidence and investigate: {failed}')
        pending = [(t, symbol) for t in state['trials'] for symbol in ('SUGP','JUNS')
                   if not any(r['symbol'] == symbol for r in t['runs'])]
        if not active and not pending:
            print('Completed all trials:', manifest, flush=True)
            break
        # Two event workers, only one watchlist materialization at a time.
        if pending and len(active) < 2 and all(r['status'] == 'running' for r in active):
            trial, symbol = pending[0]
            start, end = ('04:00:00','04:30:00') if symbol == 'SUGP' else ('07:00:00','07:30:00')
            state['launching'] = dict(candidate_id=trial['candidate_id'], symbol=symbol,
                                     requested_at=time.time())
            save(manifest, state)
            response = session.post(base, json=dict(anchor_date='2026-08-22',session_count=1,initial_cash=10000,
                configuration_revision_id=trial['candidate_id'],run_plan_id=PLAN,tickers=[symbol],
                start_time=start,end_time=end,experimental_structure_book=f'level-book-v7-{symbol}',
                simulation_profile='baseline',new_order_activation_delay_ms=0), timeout=120)
            response.raise_for_status()
            run = response.json()
            trial['runs'].append(dict(symbol=symbol,run_id=run['run_id'],status=run['status']))
            state.pop('launching')
            save(manifest, state)
            print('Started', trial['revision'], trial['name'], symbol, run['run_id'], flush=True)
        print('Progress', [(r['symbol'],r['status'],round(r.get('progress') or 0,3)) for r in active], flush=True)
        time.sleep(15)


if __name__ == '__main__':
    main()
