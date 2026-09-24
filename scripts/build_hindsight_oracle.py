"""Build exact fixed-episode Phase 3 oracle labels from a completed arte Phase 1."""
import os
os.environ['PYTHONDONTWRITEBYTECODE'] = '1'
import sys
sys.dont_write_bytecode = True
from pathlib import Path
REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

import argparse
from datetime import date
import signal
from time import monotonic

from rich.console import Console
from scripts.build_hindsight_greedy import phase1_plan, file_hash, verify_files, required_runtime
from scripts.build_hindsight_phase1 import exclusive
from src.market_engine.hindsight_phase1 import bounds, digest
from src.market_engine.hindsight_oracle import VERSION, MODES, Episode, solve, benchmark, validate, positive
from src.market_engine.level_book_store import read, write

STOP = False
SOURCES = ('scripts/build_hindsight_oracle.py', 'src/market_engine/hindsight_oracle.py',
    'scripts/build_hindsight_greedy.py', 'scripts/build_hindsight_phase1.py',
    'src/market_engine/hindsight_phase1.py', 'src/market_engine/level_book_store.py')


def load_episodes(source, plan, pins, max_episodes, check, progress):
    episodes = []
    left, _ = bounds(date.fromisoformat(plan['date']))
    for i, listing in enumerate(plan['selected']):
        check()
        directory = digest(listing)[:20]
        folder = source/'listings'/directory
        ready = read(folder/'ready.json')
        if file_hash(folder/'ready.json') != pins[directory]:
            raise ValueError('Source ready certificate changed')
        if ready['plan_hash'] != plan['plan_hash'] or ready['listing'] != listing or ready['rows'] != 57601:
            raise ValueError('Source listing certificate mismatch')
        if not {'targets.json', 'opportunities.parquet'} <= ready['files'].keys():
            raise ValueError('Source lacks certified targets or observations')
        verify_files(folder, ready['files'])
        targets = read(folder/'targets.json')
        if targets['position_count'] != len(targets['positions']):
            raise ValueError('Source target count mismatch')
        if len(episodes)+len(targets['positions']) > max_episodes:
            raise ValueError(f'Episode limit {max_episodes} exceeded; no episodes truncated. Raise --max-episodes explicitly.')
        for p in targets['positions']:
            if type(p['position_number']) is not int or p['position_number'] <= 0:
                raise ValueError('Invalid persisted episode number')
            e = Episode(f"{directory}:{p['position_number']}", listing['ticker'], listing['listing_id'],
                p['direction'], round(p['entry_time']*1e6), round(p['exit_time']*1e6),
                p['entry_price'], p['exit_price'], round(p['label_available_at']*1e6))
            if not left <= e.entry_us < e.exit_us < plan['liquidation_us'] or e.label_available_us > plan['liquidation_us']:
                raise ValueError('Episode outside certified session/liquidation boundary')
            if e.entry_us % 100_000 or e.exit_us % 100_000:
                raise ValueError('Episode does not use the completed 100ms bar clock')
            episodes.append(e)
        progress(i+1, len(plan['selected']))
    validate(episodes)
    return episodes


def run(args, console):
    global STOP
    STOP = False
    positive(args.initial_cash, 'Initial cash')
    positive(args.allocation, 'Benchmark allocation')
    if args.max_episodes < 1:
        raise ValueError('max-episodes must be positive')
    # Validate even an empty source/mode.
    solve([], initial_cash=args.initial_cash, cost_per_share=args.cost_per_share)
    source = args.phase1.resolve()
    original = phase1_plan(source)
    if original['version'] != 'hindsight-phase1-arte-price-action-v3':
        raise ValueError('Oracle requires certified arte price-action V3 with 19:58 cutoff')
    pins = {digest(r)[:20]: file_hash(source/'listings'/digest(r)[:20]/'ready.json') for r in original['selected']}
    plan = dict(version=VERSION, phase1_root=str(source), phase1_plan_hash=original['plan_hash'],
        source_ready_hashes=pins, date=original['date'], scope=original['scope'],
        liquidation_us=original['liquidation_us'], initial_cash=args.initial_cash,
        benchmark_allocation=args.allocation, cost_per_share=args.cost_per_share,
        max_episodes=args.max_episodes, modes=list(MODES),
        objective='maximum terminal equity; no time discount',
        action_space='fixed retained Phase 1 episodes; commit entry-to-exit; continuous fractional allocation',
        capital_contract='no leverage; 100% entry-plus-cost short reserve; proceeds locked; exits before entries at equal timestamps',
        price_contract='hindsight 100ms extrema at completion; optimistic label prices, not execution fills',
        constraints_excluded=['capacity', 'borrow availability', 'margin calls', 'drawdown limits', 'fixed fees', 'early exits'],
        label_boundary='all oracle choices and continuation values are future-derived; never model input features',
        oracle_label_available_us=original['liquidation_us'],
        code_hashes={p:file_hash(REPO/p) for p in SOURCES})
    plan['plan_hash'] = digest(plan)
    root = required_runtime()/'hindsight-oracle'/original['date']/plan['plan_hash'][:20]
    root.mkdir(parents=True, exist_ok=True)
    def check():
        if STOP or (root/'STOP').exists():
            raise InterruptedError('Stopped; remove STOP and rerun the same command to resume')
    started = monotonic()
    last = [0.]
    def progress(done, total):
        if monotonic()-last[0] >= 5 or done == total:
            console.print(f'Source listings verified: {done}/{total}; active {int(done < total)}, queued {total-done}')
            last[0] = monotonic()
    counts = dict(completed=0, reused=0, failed=0, skipped=0, retried=0)
    total = (len(original['selected'])+1)*len(MODES)
    def status(state, active=0):
        write(root/'progress.json', dict(status=state, counts=counts, active=active,
            queued=max(0,total-counts['completed']-counts['reused']-counts['failed']-active),
            elapsed_seconds=monotonic()-started), immutable=False)
    console.print(f"Exact episode oracle | {original['date']} | {original['scope']} | initial cash ${args.initial_cash:,.2f}")
    console.print('Output: '+str(root), soft_wrap=True, markup=False)
    with exclusive(root/'run.lock'):
        write(root/'plan.json', plan)
        (root/'complete.json').unlink(missing_ok=True)
        try:
            status('verifying_source')
            episodes = load_episodes(source, original, pins, args.max_episodes, check, progress)
            by_ticker = {r['ticker']:[] for r in original['selected']}
            for e in episodes:
                by_ticker[e.ticker].append(e)
            jobs = [(r['ticker'], digest(r)[:20], by_ticker[r['ticker']]) for r in original['selected']]
            jobs.append(('ALL', 'market', episodes))
            results = []
            for name, directory, candidates in jobs:
                for mode in MODES:
                    check()
                    status('solving', active=1)
                    folder = root/directory/mode
                    marker = folder/'ready.json'
                    if marker.exists():
                        ready = read(marker)
                        if ready['plan_hash'] != plan['plan_hash']:
                            raise ValueError('Output plan mismatch')
                        verify_files(folder, ready['files'])
                        metrics = read(folder/'metrics.json')
                        counts['reused'] += 1
                    else:
                        result = solve(candidates, initial_cash=args.initial_cash, cost_per_share=args.cost_per_share,
                            mode=mode, check=check)
                        check()
                        write(folder/'trajectory.json.gz', result.pop('ledger'))
                        write(folder/'action-labels.json.gz', result.pop('action_labels'))
                        metrics = dict(oracle=result, benchmark=benchmark(candidates, allocation=args.allocation,
                            initial_cash=args.initial_cash, cost_per_share=args.cost_per_share, mode=mode),
                            terminal_flat=True, liquidation_us=original['liquidation_us'],
                            trajectory_kind='variable-duration committed trades; gaps are cash waits; flat through cutoff')
                        write(folder/'metrics.json', metrics)
                        files = {p:file_hash(folder/p) for p in ('trajectory.json.gz','action-labels.json.gz','metrics.json')}
                        write(marker, dict(plan_hash=plan['plan_hash'], files=files))
                        counts['completed'] += 1
                    results.append(dict(ticker=name, mode=mode, directory=str(folder), **metrics))
                    if directory == 'market' or monotonic()-last[0] >= 5:
                        console.print(f"{name} {mode}: {metrics['oracle']['selected_episodes']:,} trades; terminal equity ${metrics['oracle']['terminal_equity']:,.2f} | completed {counts['completed']}, reused {counts['reused']}, queued {total-counts['completed']-counts['reused']}")
                        last[0] = monotonic()
            check()
            if phase1_plan(source)['plan_hash'] != original['plan_hash']:
                raise ValueError('Source plan changed during run')
            for directory, pin in pins.items():
                folder = source/'listings'/directory
                if file_hash(folder/'ready.json') != pin:
                    raise ValueError('Source certificate changed during run')
                verify_files(folder, read(folder/'ready.json')['files'])
            write(root/'summary.json', dict(status='complete', episode_count=len(episodes), results=results))
            files = {'summary.json':file_hash(root/'summary.json')}
            write(root/'complete.json', dict(plan_hash=plan['plan_hash'], units=total, files=files))
            status('complete')
            console.print(f'Complete | {len(episodes):,} episodes | {monotonic()-started:.2f}s')
            return 0
        except (InterruptedError, KeyboardInterrupt) as exc:
            status('interrupted')
            console.print(str(exc) or 'Interrupted; rerun to resume.', markup=False)
            return 2
        except Exception as exc:
            counts['failed'] += 1
            status('failed')
            write(root/'failure.json', dict(error=str(exc), elapsed_seconds=monotonic()-started), immutable=False)
            raise


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--phase1', type=Path, required=True, help='Completed arte price-action V3 Phase 1 directory')
    parser.add_argument('--initial-cash', type=float, default=10_000.)
    parser.add_argument('--allocation', type=float, default=1_000., help='Equal capital per benchmark episode')
    parser.add_argument('--cost-per-share', type=float, default=0.)
    parser.add_argument('--max-episodes', type=int, default=250_000, help='Fail closed above this input bound; never truncate')
    args = parser.parse_args(argv)
    console = Console()
    previous = signal.getsignal(signal.SIGINT)
    def stop(*_):
        global STOP
        STOP = True
    signal.signal(signal.SIGINT, stop)
    try:
        return run(args, console)
    except (ValueError, OSError, KeyError) as exc:
        console.print(f'Failed: {exc}', markup=False)
        return 2
    finally:
        signal.signal(signal.SIGINT, previous)


if __name__ == '__main__':
    raise SystemExit(main())
