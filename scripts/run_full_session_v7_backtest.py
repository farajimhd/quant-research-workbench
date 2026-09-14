"""Prepare and run a complete, first-squeeze/session-watch V7 backtest.

Canonical SIP is the historical timing policy. No REST clock recovery, flatfiles,
live Signal Stream mutation, or scanner display cap is used. Runtime files pin
the reference population, detector input, immutable candidate and API launch.
"""
import os
os.environ['PYTHONDONTWRITEBYTECODE'] = '1'
import sys
sys.dont_write_bytecode = True
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import argparse
from copy import deepcopy
from collections import Counter
from datetime import date, datetime, time as wall_time, timedelta
import hashlib
import json
import subprocess
import time
from zoneinfo import ZoneInfo
import requests

BASELINE = '427b4521-2a3c-4230-953e-ef9d696b69d5'
PROFILE = 'v7-setup-recovery-v9'
PLAN = PROFILE + '-backtest'
STREAM = 'v7-full-session-early-squeeze'
RULE = STREAM + '-under20'


def save(path, value):
    temporary = path.with_suffix('.tmp')
    temporary.write_text(json.dumps(value, indent=2, allow_nan=False), encoding='utf-8')
    temporary.replace(path)


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def prepare(root, session_date):
    from src.backend.historical_signal_occurrence_service import _load_repository_env
    from src.backend.experimental_structure_book import rows
    from src.backend.trading_configuration_service import configuration_candidate, configuration_base, _build_configuration_release
    _load_repository_env()
    baseline = configuration_candidate(BASELINE, required=True)
    payload = deepcopy(baseline['payload'])
    published = {p['profile_id']: p for p in configuration_base()['strategy']['profiles'] if p.get('publication_status') == 'published'}
    if PROFILE in published:
        raise ValueError('Baseline profile is published; explicit successor migration required')
    payload['strategy']['profiles'] = [deepcopy(published.get(p['profile_id'], p)) for p in payload['strategy']['profiles']]
    profile = next(p for p in payload['strategy']['profiles'] if p['profile_id'] == PROFILE)
    profile['parameters']['liquidity_admission']['maximum_price'] = 20.0
    profile['lifecycle']['trading_behavior'].update(eligible_sessions=['premarket', 'regular', 'after_hours'],
        entry_cutoff_time='19:55:00', flatten_time='19:59:00')
    plan = next(p for p in payload['run_plans']['plans'] if p['run_plan_id'] == PLAN)
    plan['activation'] = dict(event_policy='new_occurrences', watchlist_policy='not_required',
        watch_duration='session', maximum_signal_price_exclusive=20.0)
    plan['signal_stream_ids'] = [STREAM]
    plan['name'] = 'V7 full session / first Early Squeeze'
    plan['description'] = 'US tradable stocks below $20; first Early Squeeze admits through session end; Candidate 219 strategy gates and management.'
    discovery = payload['market_discovery']
    stream = deepcopy(next(s for s in discovery['signal_streams'] if s['signal_stream_id'] == 'price-squeeze-early'))
    stream.update(signal_stream_id=STREAM, name='Early Squeeze / stocks under $20', origin='user', protected=False,
        inclusion_rule_sets=[RULE], maximum_events=25000)
    rule = deepcopy(next(r for r in discovery['rule_sets'] if r['rule_set_id'] == 'watchlist-squeeze-early-impulse-100ms'))
    rule.update(rule_set_id=RULE, name='Early Squeeze below $20', origin='user', protected=False, editable=True,
        publication_status='draft')
    rule['conditions'].append(dict(condition_id='signal-price-under20', enabled=True, comparator='less_than',
        left_source_id='market.last_price', left_field_ref='data.market.last_price@1:value', value=20.0, right_source_id=''))
    discovery['signal_streams'].append(stream)
    discovery['rule_sets'].append(rule)
    _, payload, _ = _build_configuration_release(canvas_revision=payload['canvas']['revision'],
        canvas_profile=payload['canvas']['profile'], configuration=payload, run_plan_id=PLAN, strategy_profile_id=PROFILE)
    discovery = payload['market_discovery']
    stream = next(s for s in discovery['signal_streams'] if s['signal_stream_id'] == STREAM)
    rule = next(r for r in discovery['rule_sets'] if r['rule_set_id'] == RULE)
    # Published tradability is the point-in-time stock authority. Check its actual
    # classification rather than inferring stocks from archive presence or price.
    candidates = rows(f"SELECT u.ticker,u.symbol_id,u.listing_id,u.security_id,u.ibkr_conid,u.product_type,u.asset_class,u.currency_code,u.exchange_code,u.source_run_id,"
        f"s.instrument_type,s.ticker_type_id,toString(s.first_seen_at_utc) classification_first_seen "
        f"FROM q_live.feature_tradable_universe_v1 u FINAL LEFT JOIN q_live.id_symbol_v1 s FINAL ON u.symbol_id=s.symbol_id "
        f"WHERE u.universe_date='{session_date}' AND u.is_tradable=1 ORDER BY u.ticker")
    population = []
    exclusions = []
    for row in candidates:
        if row['ticker_type_id'] not in {'ticker_type:stocks:cs', 'ticker_type:stocks:adrc'}:
            exclusions.append(dict(ticker=row['ticker'], reason='not_confirmed_common_share', ticker_type_id=row['ticker_type_id']))
        elif row['classification_first_seen'][:10] > str(session_date):
            exclusions.append(dict(ticker=row['ticker'], reason='classification_not_available', ticker_type_id=row['ticker_type_id']))
        else:
            population.append(row)
    if not population or len(population) != len({r['ticker'] for r in population}):
        raise ValueError('Missing or ambiguous point-in-time tradable population')
    if any(r['product_type'] != 'STK' or r['asset_class'] != 'stocks' or r['currency_code'] != 'USD' for r in population):
        raise ValueError('Published tradable universe contains non-US-stock classifications; explicit population resolution required')
    save(root / 'population.json', population)
    save(root / 'population-exclusions.json', exclusions)
    exclusion_counts = dict(Counter(row['ticker_type_id'] for row in exclusions))
    zone = ZoneInfo('America/New_York')
    start = datetime.combine(session_date, wall_time(4), zone)
    end = datetime.combine(session_date, wall_time(20), zone)
    request = dict(configuration=dict(configuration_revision=baseline['content_hash'], session_key=str(session_date),
        session_start_utc=start.isoformat(), session_end_utc=end.isoformat(), streams=[stream], rule_sets=[rule],
        column_catalog=discovery['column_catalog']), tickers=[r['ticker'] for r in population], maximum_price_exclusive=20.0,
        population_authority=dict(table='q_live.feature_tradable_universe_v1', universe_date=str(session_date),
            classification_table='q_live.id_symbol_v1', accepted_types=['CS', 'ADRC'],
            content_sha256=digest(root / 'population.json'), row_count=len(population),
            excluded_count=len(exclusions), exclusions_sha256=digest(root / 'population-exclusions.json'), exclusion_counts=exclusion_counts))
    save(root / 'request.json', request)
    save(root / 'candidate-payload.json', payload)
    state = dict(stage='planned', session_date=str(session_date), baseline=BASELINE, baseline_hash=baseline['content_hash'],
        population_count=len(population), request_sha256=digest(root / 'request.json'), initial_cash=10000,
        timing_policy='canonical_sip', candidate_id=None, run_id=None)
    save(root / 'state.json', state)
    return state


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=['plan', 'signals', 'candidate', 'run', 'status'])
    parser.add_argument('--date', type=date.fromisoformat, default=date(2026, 8, 21))
    parser.add_argument('--new-run', action='store_true', help='Start a successor only after the previous run is terminal; preserve its ID')
    parser.add_argument('--runtime', type=Path, default=Path('D:/TradingML/runtimes/research/v7-full-session-20260821-stocks'))
    parser.add_argument('--binary', type=Path, default=Path('D:/TradingML/runtimes/qmd_history_gateway/cargo-target/release/historical_squeeze_replay.exe'))
    args = parser.parse_args()
    root = args.runtime.resolve()
    root.relative_to(Path('D:/TradingML/runtimes').resolve())
    root.mkdir(parents=True, exist_ok=True)
    state = json.loads((root / 'state.json').read_text()) if (root / 'state.json').exists() else prepare(root, args.date)
    if state['session_date'] != str(args.date) or state['request_sha256'] != digest(root / 'request.json'):
        raise ValueError('Frozen session or request changed; use a new runtime')
    print(f"Full session {args.date} | {state['population_count']:,} confirmed common-share listings | canonical SIP | 04:00-20:00 ET", flush=True)
    if args.action == 'plan':
        print(f"Plan saved: {root / 'state.json'}", flush=True)
        return
    if args.action == 'signals':
        if (root / 'signals/manifest.json').exists():
            print('Complete signals already exist; proceeding requires their pinned manifest validation.', flush=True)
            return
        env = dict(os.environ, QMD_HISTORY_ARCHIVE_CLOCK_POLICY='canonical_sip')
        state['stage'] = 'signals_running'
        save(root / 'state.json', state)
        process = subprocess.Popen([str(args.binary), str(root / 'request.json'), str(root / 'signals')], env=env)
        try:
            if process.wait() != 0: raise RuntimeError('Signal preparation failed; see terminal and signals/status.json')
        except BaseException:
            state['stage'] = 'signals_failed'
            save(root / 'state.json', state)
            if process.poll() is None:
                process.terminate()
                process.wait(timeout=30)
            raise
        state['stage'] = 'signals_complete'
        save(root / 'state.json', state)
        return
    if args.action == 'candidate':
        from src.backend.trading_configuration_service import create_test_candidate, backtest_configuration_snapshot, configuration_base
        from src.backend.historical_signal_occurrence_service import historical_source_native_signal_occurrences
        payload = json.loads((root / 'candidate-payload.json').read_text())
        # Saved normalized drafts can differ from unrelated published profiles.
        # Preserve the current published definitions verbatim at publication.
        published = {p['profile_id']: p for p in configuration_base()['strategy']['profiles']
                     if p.get('publication_status') == 'published'}
        if PROFILE in published:
            raise ValueError('Selected baseline profile became published; create an explicit successor')
        payload['strategy']['profiles'] = [deepcopy(published.get(p['profile_id'], p))
                                           for p in payload['strategy']['profiles']]
        stream = next(s for s in payload['market_discovery']['signal_streams'] if s['signal_stream_id'] == STREAM)
        manifest = root / 'signals/manifest.json'
        stream['historical_occurrence_artifact'] = dict(manifest_path=str(manifest), manifest_sha256=digest(manifest))
        request = json.loads((root / 'request.json').read_text())['configuration']
        loaded = historical_source_native_signal_occurrences(stream, start=datetime.fromisoformat(request['session_start_utc']),
            end=datetime.fromisoformat(request['session_end_utc']))
        candidate = create_test_candidate(label='V7 full session / stocks under $20 / first Early Squeeze',
            canvas_revision=payload['canvas']['revision'], canvas_profile=payload['canvas']['profile'],
            configuration=payload, run_plan_id=PLAN, strategy_profile_id=PROFILE)
        snapshot = backtest_configuration_snapshot(PLAN, candidate_id=candidate['candidate_id'])
        save(root / 'effective-configuration.json', snapshot)
        state.update(stage='candidate_ready', candidate_id=candidate['candidate_id'], candidate_revision=candidate['candidate_revision'],
            admitted_tickers=len(loaded['occurrences']), signal_manifest_sha256=digest(manifest))
        save(root / 'state.json', state)
        print(f"Candidate {state['candidate_revision']} | {state['admitted_tickers']:,} admitted stocks", flush=True)
        return
    api = 'http://127.0.0.1:8000/api/trading/backtest/runs'
    if args.new_run:
        if args.action != 'run': raise ValueError('--new-run is only valid for run')
        if state.get('run_id'):
            prior = requests.get(f"{api}/{state['run_id']}?compact=true", timeout=30)
            prior.raise_for_status()
            if prior.json()['status'] not in {'completed', 'failed', 'stopped', 'cancelled'}:
                raise ValueError('Previous run is still active; stop it explicitly before starting a successor')
            state.setdefault('prior_runs', []).append(state['run_id'])
            state.update(run_id=None, stage='candidate_ready')
            save(root / 'state.json', state)
    if args.action == 'run' and not state['run_id']:
        if state.get('launching'): raise RuntimeError('Prior launch outcome uncertain; reconcile API runs before retry')
        if not state.get('candidate_id'): raise ValueError('Prepare and validate the signal artifact and candidate first')
        request = dict(anchor_date=str(args.date + timedelta(days=1)), session_count=1, initial_cash=state['initial_cash'],
            configuration_revision_id=state['candidate_id'], run_plan_id=PLAN, tickers=[], start_time='04:00:00', end_time='20:00:00',
            experimental_structure_book='level-book-v7', simulation_profile='baseline', new_order_activation_delay_ms=0)
        save(root / 'launch-request.json', request)
        state['launching'] = True
        save(root / 'state.json', state)
        response = requests.post(api, json=request, timeout=180)
        if response.status_code >= 400:
            save(root / 'launch-failure.json', dict(status=response.status_code, body=response.text))
            state.pop('launching')
            save(root / 'state.json', state)
            response.raise_for_status()
        run = response.json()
        state.update(run_id=run['run_id'], stage=run['status'])
        state.pop('launching')
        save(root / 'state.json', state)
        print(f"Started backtest {state['run_id']}", flush=True)
    if not state.get('run_id'):
        print(f"Stage: {state['stage']} | signals: {root / 'signals/status.json'}", flush=True)
        return
    while True:
        response = requests.get(f"{api}/{state['run_id']}?compact=true", timeout=30)
        response.raise_for_status()
        run = response.json()
        save(root / 'latest-run.json', run)
        state.update(stage=run['status'], current_time=run.get('current_time'), progress=run.get('progress'), error=run.get('error'))
        save(root / 'state.json', state)
        preparation = run.get('preparation_progress') or {}
        print(f"{run['status']} | {run.get('current_time') or 'preparing'} | replay {100 * (run.get('progress') or 0):.1f}% | {run.get('preparation_stage') or ''} {preparation.get('completed', 0)}/{preparation.get('total', 0)}", flush=True)
        if run['status'] in {'completed', 'failed', 'stopped', 'cancelled'} or args.action == 'status':
            if run.get('error'): print(run['error'], flush=True)
            return
        time.sleep(15)


if __name__ == '__main__':
    main()
