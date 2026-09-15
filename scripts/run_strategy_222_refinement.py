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
import sqlite3

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


def prepare(name, overrides=None):
    from src.backend.trading_configuration_service import (
        configuration_candidate, configuration_base, create_test_candidate)
    baseline = configuration_candidate(BASELINE, required=True)
    if baseline['content_hash'] != 'd52acbd89292aa01d0dad9f760632006a0ee9c35bdccc7d81e70e6e941e994e3':
        raise ValueError('Baseline identity changed')
    if name == 'corrected-baseline':
        return baseline
    base_name='full-v6' if overrides is not None else ('recovery-v10' if name in ('support-v11','support-body-v12') else name)
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
        setup_initial_tranche_fraction=1/3, setup_early_base_enabled=int(base_name in ('early-v1','base-v7','trend-v8','recovery-v10')))
    if base_name in ('guarded-v3','phase-v4','burst-v5','full-v6','base-v7','trend-v8','body-v9','recovery-v10'):
        profile['parameters']['historical_hod']['setup_minimum_quote_clearance_spreads']=1.
    if base_name in ('burst-v5','full-v6','base-v7','trend-v8','body-v9','recovery-v10'):
        profile['parameters']['liquidity_admission']['minimum_current_trade_rate_60s']=3.
    if base_name in ('full-v6','base-v7','trend-v8','body-v9','recovery-v10'):
        profile['parameters']['historical_hod']['setup_initial_tranche_fraction']=1.
    if base_name in ('base-v7','trend-v8'):
        profile['parameters']['historical_hod']['setup_base_maximum_range_pct']=3.
    if base_name=='trend-v8':
        profile['parameters']['historical_hod']['rejection_break_offset_bps']=300.
    if base_name in ('body-v9','recovery-v10'):
        profile['parameters']['historical_hod']['setup_minimum_body_bps']=30.
    if base_name=='recovery-v10':
        profile['parameters']['historical_hod'].update(setup_base_maximum_range_pct=8.,setup_base_maximum_risk_pct=8.,
            setup_maximum_bar_gap_s=3,setup_recovery_stop_gain_guard=1,
            setup_base_recovery_maximum_range_pct=3.)
    if name=='support-body-v12':
        profile['parameters']['historical_hod']['setup_minimum_body_bps']=5.
    for section,values in (overrides or {}).items():
        profile['parameters'][section].update(values)
    return create_test_candidate(label='Strategy 222 refinement / '+name,
        canvas_revision=payload['canvas']['revision'], canvas_profile=payload['canvas']['profile'],
        configuration=payload, run_plan_id=PLAN, strategy_profile_id=profile['profile_id'])


async def run(args):
    # QMD's prepared-book authority admits one stream per manager. Serialize
    # study processes without altering the service limit; SQLite releases the
    # lease automatically if a worker dies.
    lease=Path('D:/TradingML/runtimes/analysis/strategy-222-refinement/_prepared-run-lease.sqlite3')
    lease.parent.mkdir(parents=True,exist_ok=True)
    connection=sqlite3.connect(lease,timeout=.1,isolation_level=None)
    try:
        while True:
            if args.stop_request_file and args.stop_request_file.exists():raise RuntimeError('Research stop requested while queued')
            try:
                connection.execute('BEGIN EXCLUSIVE')
                break
            except sqlite3.OperationalError as exc:
                if 'locked' not in str(exc).lower():raise
                print(f'Queued {args.symbol}: another Strategy 222 replay holds the prepared-stream lease',flush=True)
                await asyncio.sleep(15)
        await run_locked(args)
    finally:
        connection.rollback();connection.close()


async def run_locked(args):
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
    if args.portfolio_symbols:identity['tickers']=args.portfolio_symbols
    if args.recipe_parameters is not None:identity['recipe_parameters']=args.recipe_parameters
    if args.restart_at:identity['restart_at']=args.restart_at
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
        if args.stop_request_file and args.stop_request_file.exists():raise RuntimeError('Research stop requested before next trial')
        prior = next((t for t in state['trials'] if t['name'] == name), None)
        if prior:
            if prior['status'] == 'completed':
                continue
            raise ValueError(f"Recorded {prior['status']} run {prior['run_id']}; preserve and investigate before a new trial")
        candidate = prepare(name,args.recipe_parameters)
        revision = candidate_runtime_configuration_snapshot('backtest', candidate_id=candidate['candidate_id'], run_plan_id=PLAN)
        definition = ReplayRunDefinition(session_date=date(2026,8,21), start_time=time(4),
            end_time=time.fromisoformat(args.end), initial_cash=10000,
            tickers=tuple(args.portfolio_symbols or (args.symbol,)),
            configuration_revision=revision, mode=RunMode.BACKTEST,
            experimental_structure_book='level-book-v7', experimental_structure_fingerprint=BOOK_HASH)
        controller = ReplayRunController(definition, runtime_root=Path('D:/TradingML/runtimes/trading/backtest'))
        if args.restart_at:
            from datetime import datetime
            restart_time=datetime.combine(definition.session_date,time.fromisoformat(args.restart_at),
                                          tzinfo=definition.session_start.tzinfo)
            if not definition.requested_start<restart_time<definition.session_end:
                raise ValueError('Restart checkpoint must be inside the evaluation window')
            after_event=controller._after_event
            async def stop_at_checkpoint(at):
                await after_event(at)
                if at>=restart_time:controller._stop_requested=True
            controller._after_event=stop_at_checkpoint
        trial = dict(name=name, candidate_id=candidate['candidate_id'], revision=candidate['candidate_revision'],
                     run_id=controller.run_id, status='launching')
        state['trials'].append(trial); save(manifest,state)
        print(f"Started {name} {args.symbol} run={controller.run_id}", flush=True)
        await controller.start()
        try:
            while True:
                await asyncio.wait({controller._task}, timeout=15)
                if args.stop_request_file and args.stop_request_file.exists() and not controller._task.done():
                    await controller.command('stop')
                trial.update(status=controller.status, current_time=str(controller.current_time),
                             events=controller.processed_events, error=controller.error)
                save(manifest,state)
                completed = sum(t['status']=='completed' for t in state['trials'])
                failed = sum(t['status']=='failed' for t in state['trials'])
                print(f"{name} {args.symbol} status={controller.status} market={controller.current_time} "
                      f"events={controller.processed_events} active={int(not controller._task.done())} "
                      f"queued={len(args.variants)-len(state['trials'])} completed={completed} failed={failed} retries=0", flush=True)
                if controller._task.done():
                    await controller._task
                    if (args.restart_at and controller.status=='stopped' and not trial.get('resumed')
                            and not (args.stop_request_file and args.stop_request_file.exists())):
                        from src.backend.replay_run_service import ReplayRunService
                        trial.update(resumed=True,restart_time=str(controller.current_time))
                        save(manifest,state)
                        if controller._monitoring is not None:
                            await controller._monitoring.close()
                        if controller._journal is not None:
                            controller._journal.close()
                            controller._journal=None
                        service=ReplayRunService(runtime_root=controller.runtime_root)
                        controller=await service.resume(controller.run_id)
                        print(f'Resumed {name} {args.symbol} from its persisted checkpoint',flush=True)
                    else:
                        break
        finally:
            if not controller._task.done():
                await controller.command('stop')
                await asyncio.shield(controller._task)
            trial.update(status=controller.status, error=controller.error)
            # Unlike an interactive review service, this one-shot worker has no
            # reason to retain terminal journal connections between trials.
            if controller._monitoring is not None:
                await controller._monitoring.close()
            if controller._journal is not None:
                controller._journal.close()
                controller._journal=None
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
    parser.add_argument('--portfolio-symbols', nargs='+', help='Replay these symbols together with shared $10,000 capital; requires --end')
    parser.add_argument('--restart-at', help='Optional New York checkpoint time for a real stop/resume parity trial')
    parser.add_argument('--recipe-file',type=Path,help='Supervised research parameter recipe; mutually exclusive with --variants')
    parser.add_argument('--stop-request-file',type=Path,help='Gracefully stop if this supervisor-owned file appears')
    parser.add_argument('--variants', nargs='+', choices=['corrected-baseline','early-v1','liquidity-v2','guarded-v3','phase-v4','burst-v5','full-v6','base-v7','trend-v8','body-v9','recovery-v10','support-v11','support-body-v12'],
                        default=None)
    args=parser.parse_args()
    args.recipe_parameters=None
    if args.recipe_file:
        if args.variants is not None:parser.error('--recipe-file and --variants are mutually exclusive')
        args.recipe_parameters=load_recipe(args.recipe_file)
        key=hashlib.sha256(json.dumps(args.recipe_parameters,sort_keys=True).encode()).hexdigest()[:10]
        args.variants=['supervised-'+key]
    elif args.variants is None:args.variants=['corrected-baseline','early-v1']
    if args.portfolio_symbols:
        if args.position_symbols or args.symbol or not args.end:
            parser.error('--portfolio-symbols requires --end and cannot be combined with another symbol option')
        if len(set(args.portfolio_symbols)) != len(args.portfolio_symbols):
            parser.error('Portfolio symbols must be unique')
        args.symbol='PORTFOLIO'
    if args.position_symbols:
        if args.symbol or args.end:parser.error('Use either --position-symbols or --symbol with --end')
        from zoneinfo import ZoneInfo
        from datetime import datetime, timedelta
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
            horizon=datetime.fromisoformat(horizons[symbol]).astimezone(ZoneInfo('America/New_York'))
            if horizon.microsecond:horizon=horizon.replace(microsecond=0)+timedelta(seconds=1)
            trial.end=horizon.strftime('%H:%M:%S')
            trial.runtime=args.runtime/symbol.lower()
            print(f'Position coverage {index}/{len(args.position_symbols)} symbols; next={symbol} end={trial.end}',flush=True)
            asyncio.run(run(trial))
    else:
        if not args.symbol or not args.end:parser.error('--symbol and --end are required for a single-symbol trial')
        asyncio.run(run(args))


def load_recipe(path):
    from math import isfinite
    allowed={'liquidity_admission':{'minimum_session_dollar_volume','minimum_session_share_volume',
        'maximum_admission_spread_bps','maximum_current_spread_bps','maximum_spread_bps',
        'minimum_current_trade_rate_60s'},'historical_hod':{'setup_minimum_body_bps'}}
    parameters=json.loads(path.read_text())['parameters']
    if not isinstance(parameters,dict) or not parameters:raise ValueError('Empty research recipe')
    for section,values in parameters.items():
        if section not in allowed or not isinstance(values,dict) or not values or set(values)-allowed[section]:
            raise ValueError('Recipe contains an unapproved parameter path')
        if any(type(v) not in (int,float) or not isfinite(v) or v<0 for v in values.values()):
            raise ValueError('Invalid research parameter value')
    return parameters


if __name__ == '__main__':
    main()
