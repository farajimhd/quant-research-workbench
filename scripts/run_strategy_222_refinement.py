"""Run pinned Strategy 222 comparisons through the production backtest controller.

Each trial starts at 04:00 to preserve discovery, liquidity, and strategy state.
Hindsight determines only the evaluation horizon, never a strategy input.
"""
import os
os.environ['PYTHONDONTWRITEBYTECODE'] = '1'
import sys
sys.dont_write_bytecode = True
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import argparse
import asyncio
from copy import deepcopy
from datetime import date, time
import hashlib
import json

BASELINE = '4e135529-a791-435e-93cc-77708b166673'
PROFILE = 'v7-setup-recovery-v9'
PLAN = PROFILE + '-backtest'
BOOK_HASH = 'd5473e71fb1a4935733bd1c0a3760a1cf93b971cca255c52f732d425d596058c'
TERMINAL = {'completed', 'stopped', 'failed', 'cancelled'}


def save(path, value):
    temporary = path.with_suffix('.tmp')
    temporary.write_text(json.dumps(value, indent=2), encoding='utf-8')
    temporary.replace(path)


def source_identity():
    root = Path(__file__).resolve().parents[1]
    paths = sorted([*root.joinpath('src/trading_runtime').glob('*.py'),
                    root/'src/backend/replay_run_service.py',
                    root/'src/backend/historical_liquidity_checkpoint.py'])
    return {str(p.relative_to(root)): hashlib.sha256(p.read_bytes()).hexdigest() for p in paths}


def prepare(name):
    from src.backend.trading_configuration_service import (
        configuration_candidate, configuration_base, create_test_candidate)
    baseline = configuration_candidate(BASELINE, required=True)
    if baseline['content_hash'] != 'd52acbd89292aa01d0dad9f760632006a0ee9c35bdccc7d81e70e6e941e994e3':
        raise ValueError('Baseline identity changed')
    if name == 'corrected-baseline':
        return baseline
    payload = deepcopy(baseline['payload'])
    original = next(p for p in payload['strategy']['profiles'] if p['profile_id'] == PROFILE)
    profile = deepcopy(original)
    profile.update(profile_id='v7-222-'+name, name='Strategy 222 research / '+name,
                   publication_status='draft', editable=True, derived_from_profile_id=PROFILE)
    published = {p['profile_id']: p for p in configuration_base()['strategy']['profiles']
                 if p.get('publication_status') == 'published'}
    payload['strategy']['profiles'] = [deepcopy(published.get(p['profile_id'], p))
                                       for p in payload['strategy']['profiles']] + [profile]
    next(p for p in payload['run_plans']['plans'] if p['run_plan_id'] == PLAN)['profile_id'] = profile['profile_id']
    profile['parameters']['liquidity_admission'].update(minimum_session_dollar_volume=200000.,
        minimum_session_share_volume=25000., maximum_admission_spread_bps=150.,
        maximum_current_spread_bps=150., maximum_spread_bps=150.)
    profile['parameters']['historical_hod'].update(setup_acquisition_quality_enabled=1,
        setup_initial_tranche_fraction=1/3, setup_early_base_enabled=int(name in ('early-v1','base-v7','trend-v8')))
    if name in ('guarded-v3','phase-v4','burst-v5','full-v6','base-v7','trend-v8'):
        profile['parameters']['historical_hod']['setup_minimum_quote_clearance_spreads']=1.
    if name in ('burst-v5','full-v6','base-v7','trend-v8'):
        profile['parameters']['liquidity_admission']['minimum_current_trade_rate_60s']=3.
    if name in ('full-v6','base-v7','trend-v8'):
        profile['parameters']['historical_hod']['setup_initial_tranche_fraction']=1.
    if name in ('base-v7','trend-v8'):
        profile['parameters']['historical_hod']['setup_base_maximum_range_pct']=3.
    if name=='trend-v8':
        profile['parameters']['historical_hod']['rejection_break_offset_bps']=300.
    return create_test_candidate(label='Strategy 222 refinement / '+name,
        canvas_revision=payload['canvas']['revision'], canvas_profile=payload['canvas']['profile'],
        configuration=payload, run_plan_id=PLAN, strategy_profile_id=profile['profile_id'])


async def run(args):
    from src.backend.historical_signal_occurrence_service import _load_repository_env
    _load_repository_env()
    from src.backend.trading_configuration_service import candidate_runtime_configuration_snapshot
    from src.backend.replay_run_service import ReplayRunController, ReplayRunDefinition
    from src.trading_runtime.runtime import RunMode
    root = args.runtime.resolve()
    root.relative_to(Path('D:/TradingML/runtimes').resolve())
    root.mkdir(parents=True, exist_ok=True)
    manifest = root/'manifest.json'
    identity = dict(source=source_identity(), symbol=args.symbol, end=args.end,
                    variants=args.variants, baseline=BASELINE, book_hash=BOOK_HASH)
    blobs=Path('D:/TradingML/runtimes/analysis/strategy-222-refinement/_source')
    blobs.mkdir(parents=True,exist_ok=True)
    for relative,digest in identity['source'].items():
        target=blobs/(digest+'.py')
        data=(Path(__file__).resolve().parents[1]/relative).read_bytes()
        if hashlib.sha256(data).hexdigest()!=digest:raise ValueError('Source changed while pinning the trial')
        if not target.exists():target.write_bytes(data)
    state = json.loads(manifest.read_text()) if manifest.exists() else dict(identity=identity, trials=[])
    if state['identity'] != identity:
        raise ValueError('Pinned inputs or source changed; use a new trial directory')
    for name in args.variants:
        prior = next((t for t in state['trials'] if t['name'] == name), None)
        if prior:
            if prior['status'] == 'completed':
                continue
            raise ValueError(f"Recorded {prior['status']} run {prior['run_id']}; preserve and investigate before a new trial")
        candidate = prepare(name)
        revision = candidate_runtime_configuration_snapshot('backtest', candidate_id=candidate['candidate_id'], run_plan_id=PLAN)
        definition = ReplayRunDefinition(session_date=date(2026,8,21), start_time=time(4),
            end_time=time.fromisoformat(args.end), initial_cash=10000, tickers=(args.symbol,),
            configuration_revision=revision, mode=RunMode.BACKTEST,
            experimental_structure_book='level-book-v7', experimental_structure_fingerprint=BOOK_HASH)
        controller = ReplayRunController(definition, runtime_root=Path('D:/TradingML/runtimes/trading/backtest'))
        trial = dict(name=name, candidate_id=candidate['candidate_id'], revision=candidate['candidate_revision'],
                     run_id=controller.run_id, status='launching')
        state['trials'].append(trial); save(manifest,state)
        print(f"Started {name} {args.symbol} run={controller.run_id}", flush=True)
        await controller.start()
        try:
            while not controller._task.done():
                await asyncio.wait({controller._task}, timeout=15)
                trial.update(status=controller.status, current_time=str(controller.current_time),
                             events=controller.processed_events, error=controller.error)
                save(manifest,state)
                completed = sum(t['status']=='completed' for t in state['trials'])
                failed = sum(t['status']=='failed' for t in state['trials'])
                print(f"{name} {args.symbol} status={controller.status} market={controller.current_time} "
                      f"events={controller.processed_events} active={int(not controller._task.done())} "
                      f"queued={len(args.variants)-len(state['trials'])} completed={completed} failed={failed} retries=0", flush=True)
            await controller._task
        finally:
            if not controller._task.done():
                await controller.command('stop')
                await asyncio.shield(controller._task)
            trial.update(status=controller.status, error=controller.error)
            save(manifest,state)
        if controller.status != 'completed':
            raise RuntimeError(f"Run {controller.run_id} {controller.status}: {controller.error}")
    print(f'Completed comparisons: {manifest}', flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--runtime', type=Path, required=True)
    parser.add_argument('--symbol')
    parser.add_argument('--end', help='New York time, HH:MM:SS')
    parser.add_argument('--position-symbols', nargs='+', help='Replay the frozen audited windows for these symbols')
    parser.add_argument('--variants', nargs='+', choices=['corrected-baseline','early-v1','liquidity-v2','guarded-v3','phase-v4','burst-v5','full-v6','base-v7','trend-v8'],
                        default=['corrected-baseline','early-v1'])
    args=parser.parse_args()
    if args.position_symbols:
        if args.symbol or args.end:parser.error('Use either --position-symbols or --symbol with --end')
        from zoneinfo import ZoneInfo
        from datetime import datetime
        identity_path=Path('D:/TradingML/runtimes/analysis/strategy-222-parameter-study-20260914/study-identity.json')
        windows=json.loads(identity_path.read_text())['windows']
        horizons={}
        for window in windows:
            symbol=window['symbol']
            horizons[symbol]=max(horizons.get(symbol,''),window['end'])
        for index,symbol in enumerate(args.position_symbols):
            if symbol not in horizons:parser.error(f'{symbol} is not in the frozen position population')
            trial=deepcopy(args)
            trial.symbol=symbol
            trial.end=datetime.fromisoformat(horizons[symbol]).astimezone(ZoneInfo('America/New_York')).strftime('%H:%M:%S')
            trial.runtime=args.runtime/symbol.lower()
            print(f'Position coverage {index}/{len(args.position_symbols)} symbols; next={symbol} end={trial.end}',flush=True)
            asyncio.run(run(trial))
    else:
        if not args.symbol or not args.end:parser.error('--symbol and --end are required for a single-symbol trial')
        asyncio.run(run(args))


if __name__ == '__main__':
    main()
