"""Compare causal decision features around frozen move and execution anchors.

Hindsight anchors and realized outcomes are labels, never feature inputs. This
is descriptive research on a selected development population, not a predictor.
"""
import os
os.environ['PYTHONDONTWRITEBYTECODE'] = '1'
import sys
sys.dont_write_bytecode = True
import argparse
from bisect import bisect_right
from collections import Counter, defaultdict
from datetime import datetime
import json
import math
from pathlib import Path
import sqlite3
from statistics import median

from strategy_222_supervised_research import digest, save
from strategy_222_market_state_features import MarketStates

ROOT = Path('D:/TradingML/runtimes/analysis/strategy-222-refinement')
OFFSETS = (-10, -5, 0, 5, 10)


def epoch(value):
    value = datetime.fromisoformat(value)
    if value.tzinfo is None:
        raise ValueError('Naive timestamp')
    return value.timestamp()


def features(metadata, at):
    """Explicit normalized allowlist; no ticker, absolute time, or outcomes."""
    out = {}
    def put(name, value):
        if isinstance(value, (int, float)) and math.isfinite(value):
            out[name] = float(value)
    def timed(value, key='observed_at'):
        stamp = value.get(key)
        if isinstance(stamp, (int, float)):
            if stamp > at:
                raise ValueError('Future feature timestamp')
            return True
        return False
    def relative(name, value, base):
        if isinstance(value, (int, float)) and isinstance(base, (int, float)) and base > 0:
            put(name, (value / base - 1) * 100)
    price = metadata.get('reference_price')
    liquidity = metadata.get('liquidity_admission') or {}
    facts = liquidity.get('facts') or {}
    for name in ('spread_bps', 'trade_rate_10s', 'trade_rate_60s',
                 'session_dollar_volume', 'session_share_volume'):
        put(name, facts.get(name))
    for name, value in (liquidity.get('checks') or {}).items():
        if isinstance(value, bool):
            put('liquidity_check.' + name, value)
    r10, r60 = facts.get('trade_rate_10s'), facts.get('trade_rate_60s')
    if isinstance(r10, (int, float)) and isinstance(r60, (int, float)) and r60 > 0:
        put('trade_rate_acceleration', r10 / r60)
    macd = metadata.get('macd') or {}
    if timed(macd):
        put('macd_age_s', at - macd['observed_at'])
        if isinstance(macd.get('episode'), (int, float)):
            if macd['episode'] > at:
                raise ValueError('Future MACD episode')
            put('macd_episode_age_s', at - macd['episode'])
        if price and all(isinstance(macd.get(k), (int, float)) for k in ('line', 'signal')):
            put('macd_histogram_bps', (macd['line'] - macd['signal']) / price * 10000)
        put('macd_is_forming', macd.get('kind') == 'forming')
    body = metadata.get('entry_body') or {}
    if timed(body):
        relative('body_pct', body.get('close'), body.get('open'))
    base = metadata.get('early_base_assessment') or {}
    if not base:
        research=metadata.get('research_base_assessment') or {}
        if research.get('status')=='measured':base=research
    if timed(base):
        for key in ('range_pct', 'risk_pct'):
            put('base_' + key, base.get(key))
        for key, value in (base.get('checks') or {}).items():
            if isinstance(value, bool):
                put('base_check.' + key, value)
        swing = base.get('swing') or {}
        if timed(swing, 'confirmed_at'):
            put('support_age_s', at - swing['confirmed_at'])
        prior = base.get('range') or {}
        if timed(prior, 'end'):
            low, high = prior.get('low'), prior.get('high')
            if price and isinstance(low, (int, float)) and isinstance(high, (int, float)) and high > low:
                put('base_price_location', (price - low) / (high - low))
            put('base_candle_count', prior.get('count'))
    wide = metadata.get('entry_range') or {}
    if timed(wide):
        put('lookback_range_pct', wide.get('range_pct'))
        put('lookback_observed_bars', wide.get('observed_bars'))
    clearance = metadata.get('entry_quote_clearance') or {}
    if timed(clearance) and clearance.get('spread', 0) > 0:
        if isinstance(clearance.get('clearance'), (int, float)):
            put('stop_clearance_spreads', clearance['clearance'] / clearance['spread'])
    reference = metadata.get('historical_hod_reference') or {}
    if timed(reference, 'at'):
        relative('price_to_hod_pct', price, reference.get('hod'))
        relative('price_to_resistance_pct', price, reference.get('resistance_center'))
    previous = (metadata.get('setup_recovery') or {}).get('last_exit') or {}
    if timed(previous, 'at'):
        put('since_previous_exit_s', at - previous['at'])
        put('previous_stop_protected', previous.get('stop_above_initial_fill'))
        relative('price_to_previous_stop_pct', price, previous.get('stop'))
        relative('price_to_previous_body_high_pct', price, previous.get('body_high'))
        relative('price_to_previous_initial_fill_pct', price, previous.get('initial_fill_price'))
        prior_setup = previous.get('setup') or {}
        reclaim_values = [previous.get('body_high'), prior_setup.get('breakout_threshold')]
        if all(isinstance(v, (int, float)) and math.isfinite(v) for v in reclaim_values):
            relative('price_to_previous_reclaim_pct', price, max(reclaim_values))
        if timed(base):
            base_range = base.get('range') or {}
            if timed(base_range, 'end') and isinstance(base_range.get('start'), (int, float)):
                put('base_entirely_after_previous_exit', base_range['start'] > previous['at'])
            support = base.get('swing') or {}
            if timed(support, 'confirmed_at'):
                relative('support_to_previous_stop_pct', support.get('lower'), previous.get('stop'))
    setup = metadata.get('setup_management') or {}
    if setup.get('phase'):
        put('phase_post_breakout', setup['phase'] == 'post_breakout')
    relative('price_to_active_stop_pct', price, metadata.get('active_stop'))
    relative('price_to_setup_body_high_pct', price, setup.get('body_high'))
    gain = metadata.get('setup_trail_current_gain') or {}
    if timed(gain):
        relative('price_gain_from_initial_fill_pct', gain.get('current_price'), gain.get('initial_fill'))
    encounters = metadata.get('level_encounters') or {}
    if encounters:
        # v7_encounters.summary exports blocked encounters only. Absence here
        # does not mean there are no other nearby levels or successful breaks.
        states = ('warning','failed')
        for state in states:
            put('level_state_count.'+state, sum(v.get('status')==state for v in encounters.values()))
        levels = [v for v in encounters.values() if isinstance(v.get('center'),(int,float))]
        if price and levels:
            above = [v for v in levels if v['center']>price]
            below = [v for v in levels if v['center']<price]
            if above: relative('nearest_encounter_above_pct',min(v['center'] for v in above),price)
            if below: relative('nearest_encounter_below_pct',max(v['center'] for v in below),price)
    return out


def anchors(cases, trial):
    result = []; seen = set()
    for case in cases:
        key = case.get('opportunity_id')
        if not key or key in seen or case['symbol'] not in trial['tickers']:
            continue
        seen.add(key)
        label = 'major' if case['major_move'] else 'small'
        for kind, at in [('move_onset', float(key.rsplit(':', 1)[1])),
                         ('move_peak', epoch(case['peak_time']))]:
            result.append(dict(group=key, symbol=case['symbol'], kind=kind, at=at,
                               label=label, sequence_limit=None))
    for index, episode in enumerate(trial['episodes']):
        label = ('open' if 'net' not in episode else
                 'winner' if episode['net'] > 0 else 'nonwinner')
        for kind, stamp, fill in [('actual_entry', episode['opened_at'], episode['fills'][0]),
                                 ('actual_exit', episode.get('closed_at'), episode['fills'][-1])]:
            if stamp:
                result.append(dict(group=f"{trial['run_id']}:{index}", symbol=episode['symbol'],
                    kind=kind, at=epoch(stamp), label=label, sequence_limit=fill['sequence']))
    return result


def sample(anchor, offset, decisions, maximum_age=2., market_states=None, sequence_states=None):
    target = anchor['at'] + offset
    # For execution anchors at offset zero, reject decisions after the fill,
    # even when their event timestamps are identical.
    limit = anchor['sequence_limit'] if offset == 0 else None
    index = bisect_right(decisions, (target, float('inf')), key=lambda d: (d[0], d[1])) - 1
    while index >= 0 and limit is not None and decisions[index][1] >= limit:
        index -= 1
    if index < 0 or target - decisions[index][0] > maximum_age:
        return dict(status='missing_fresh_completed_decision')
    at, sequence, decision = decisions[index]
    result = dict(status='sampled', decision_at=at, decision_sequence=sequence,
        decision_age_s=target-at, action=decision['action'], reason=decision['reason'],
        features=features(decision.get('metadata') or {}, at))
    if market_states is not None:
        extra, evidence = market_states.at(anchor['symbol'],decision,at)
        result['features'].update(extra)
        result['market_state_authority'] = evidence
    if sequence_states is not None:
        extra, evidence = sequence_states.at(anchor['symbol'], at)
        result['features'].update(extra)
        result['sequence_authority'] = evidence
    return result


def auc(positive, negative):
    """Tie-aware probability a positive group's value exceeds a negative's."""
    return sum((a > b) + .5 * (a == b) for a in positive for b in negative) / (len(positive) * len(negative))


def contrasts(rows):
    output = []
    for kind in ('move_onset', 'move_peak', 'actual_entry', 'actual_exit'):
        positive, negative = ('major', 'small') if kind.startswith('move_') else ('winner', 'nonwinner')
        for offset in OFFSETS:
            selected = [r for r in rows if r['kind'] == kind and r['offset_s'] == offset
                        and r['label'] in (positive, negative)]
            names = sorted({k for r in selected for k in r.get('features', {})})
            for name in names:
                groups = {label: {} for label in (positive, negative)}
                for row in selected:
                    value = row.get('features', {}).get(name)
                    if value is not None:
                        groups[row['label']].setdefault(row['group'], []).append((row['symbol'], value))
                values = {label: [(items[0][0], median(v for _, v in items)) for items in group.values()]
                          for label, group in groups.items()}
                a, b = ([v for _, v in values[label]] for label in (positive, negative))
                if not a or not b:
                    continue
                full = auc(a, b)
                leave_out = []
                for symbol in sorted({s for group in values.values() for s, _ in group}):
                    x, y = ([v for s, v in values[label] if s != symbol] for label in (positive, negative))
                    if x and y:
                        leave_out.append(auc(x, y))
                output.append(dict(kind=kind, offset_s=offset, feature=name,
                    positive=positive, negative=negative, positive_n=len(a), negative_n=len(b),
                    positive_total=len({r['group'] for r in selected if r['label']==positive}),
                    negative_total=len({r['group'] for r in selected if r['label']==negative}),
                    positive_median=median(a), negative_median=median(b), auc=full,
                    leave_one_ticker_out_min_auc=min(leave_out) if leave_out else None,
                    leave_one_ticker_out_max_auc=max(leave_out) if leave_out else None,
                    direction_survives_every_ticker_removal=bool(leave_out) and
                        all((v-.5)*(full-.5)>0 for v in leave_out)))
    return output


def run(source, output, variant, cache=None, sequence_manifest=None):
    output = output.resolve(); output.relative_to(Path('D:/TradingML/runtimes').resolve())
    output.mkdir(parents=True, exist_ok=True)
    inputs = [source/'comparison.json', source/'position-comparison.json', Path(__file__),
              Path(__file__).with_name('strategy_222_supervised_research.py')]
    inputs.extend(Path(__file__).with_name(n) for n in ('strategy_222_market_state_features.py','strategy_222_macd_episodes.py'))
    identity = {str(p.resolve()): digest(p) for p in inputs}
    trials = [t for t in json.loads(inputs[0].read_text()) if t['name'] == variant]
    if len(trials) != 1 or trials[0]['symbol'] != 'PORTFOLIO':
        raise ValueError('Select exactly one completed shared-capital trial')
    trial = trials[0]
    if trial['status'] != 'completed':
        raise ValueError('Incomplete trial')
    sequence_source = None
    if sequence_manifest is not None:
        from strategy_222_recorded_sequences import recording
        sequence_source = recording(sequence_manifest, trial['run_id'])
        for path in (sequence_manifest, sequence_source[0], Path(__file__).with_name('strategy_222_recorded_sequences.py')):
            identity[str(path.resolve())] = digest(path)
    journal = Path('D:/TradingML/runtimes/trading/backtest')/trial['run_id']/'journal.sqlite3'
    wal = Path(str(journal)+'-wal')
    if wal.exists() and wal.stat().st_size:
        raise ValueError('Journal still open')
    identity[str(journal)] = digest(journal)
    summary_path = journal.parent/'run-summary.json'
    if cache is not None:
        identity[str(cache.resolve())] = digest(cache)
        identity[str(summary_path)] = digest(summary_path)
    provenance = source/'provenance.json'
    if provenance.exists():
        identity[str(provenance.resolve())] = digest(provenance)
        pinned = [r for r in json.loads(provenance.read_text()) if r['run_id']==trial['run_id']]
        if len(pinned)!=1 or pinned[0]['journal_sha256']!=identity[str(journal)]:
            raise ValueError('Journal differs from the source comparison authority')
    identity['variant'] = variant
    manifest = output/'manifest.json'
    if manifest.exists():
        old = json.loads(manifest.read_text())
        if old['identity'] != identity:
            raise ValueError('Inputs changed; use a successor directory')
        if old['status'] == 'completed':
            for name, sha in old['outputs'].items():
                if digest(output/name) != sha:
                    raise ValueError('Completed output changed')
            print('Completed result verified; no recomputation.', flush=True)
            return
    state = dict(identity=identity, status='running', active=1, queued=0, completed=0,
                 skipped=0, retried=int(manifest.exists()), failed=0)
    save(manifest, state)
    try:
        market_states = MarketStates(cache,json.loads(summary_path.read_text())) if cache is not None else None
        points = anchors(json.loads(inputs[1].read_text())['cases'], trial)
        bounds = defaultdict(list)
        for point in points:
            bounds[point['symbol']].append((point['at']+min(OFFSETS)-2, point['at']+max(OFFSETS)))
        sequence_states = None
        if sequence_source is not None:
            from strategy_222_recorded_sequences import RecordedSequences
            sequence_states = RecordedSequences(*sequence_source, trial['run_id'],
                {symbol:[(start-2,end) for start,end in windows] for symbol,windows in bounds.items()})
        decisions = defaultdict(list)
        print(f"Active=1 queued=0 completed=0 skipped=0 failed=0; extracting {len(points)} anchors from one completed journal.", flush=True)
        connection = sqlite3.connect(journal.as_uri()+'?mode=ro&immutable=1', uri=True)
        try:
            for sequence, stamp, raw in connection.execute("select sequence,event_time,payload_json from journal where category='strategy_decision' order by sequence"):
                at = epoch(stamp); decision = json.loads(raw); symbol = decision.get('ticker')
                if not any(start <= at <= end for start, end in bounds.get(symbol, ())):
                    continue
                if not any(':1s:' in str(s) for s in decision.get('source_signal_ids', [])):
                    continue
                decisions[symbol].append((at, sequence, decision))
        finally:
            connection.close()
        for stream in decisions.values():
            stream.sort(key=lambda d: (d[0], d[1]))
        rows = []
        for point in points:
            for offset in OFFSETS:
                rows.append(dict(point, offset_s=offset, **sample(point, offset, decisions[point['symbol']],market_states=market_states,sequence_states=sequence_states)))
        if sequence_source is not None:
            from strategy_222_recorded_sequences import complete_sparse_labels
            complete_sparse_labels(rows)
        save(output/'samples.json', rows)
        summary = dict(method='Descriptive, outcome-selected development samples. One value per anchor group and offset. AUC is a rank association, not classifier accuracy. Leave-one-ticker-out values measure sensitivity only; this is not held-out prediction. Repeated moves within a ticker/session remain dependent; many features were screened without multiple-testing correction. Missing branch-dependent features are not imputed. Positive offsets are after the anchor and cannot justify decisions before it. Hindsight peak bids are labels, not executable exits. Price gain measured near actual exit is mechanically related to realized profit and is not evidence of an early-exit predictor. Quote/trade pressure and native 2s MACD were not delivered to this strategy and are not silently joined as available features.',
            anchors=len(points), samples=len(rows), coverage=dict(Counter(r['status'] for r in rows)),
            contrasts=contrasts(rows))
        summary['feature_count'] = len({name for row in rows for name in row.get('features',{})})
        summary['market_state_scope'] = ('Completed 1s/5s candle patterns, ATR, execution VWAP and MACD episode state are derived from certified frames using recorded delivery watermarks. Episode ages and peaks are observed-to-date only. Level encounter summaries expose warning/failed encounters, not the complete level book; their distances must not be interpreted as nearest support/resistance overall. Additional candlestick pattern definitions are descriptive geometry, not reversal predictions.' if cache is not None else 'Journal-only features; canonical market-state extension not requested.')
        if sequence_source is not None:
            summary['method'] += ' Native sequence label absence is encoded as zero only for a complete recorded window; missing or interrupted windows remain unavailable.'
            summary['sequence_coverage'] = dict(Counter(
                (r.get('sequence_authority') or {}).get('status', 'no_sample') for r in rows))
            summary['sequence_complete_windows'] = {str(n):sum(
                n in (r.get('sequence_authority') or {}).get('complete_windows', []) for r in rows)
                for n in (3,5,10)}
        save(output/'comparison.json', summary)
        lines = ['# Strategy 222 feature comparison', '', summary['method'], '', summary['market_state_scope'], '',
                 f"Features: {summary['feature_count']}.", '',
                 f"Anchors: {len(points)}; samples: {len(rows)}; coverage: {summary['coverage']}.", '',
                 '## Associations at the anchor', '',
                 'Exploratory ranking only. Each side needs at least five observed groups; all missing counts remain in the JSON report.', '',
                 '| Anchor | Feature | Positive / negative observed | Medians | Rank AUC | Direction survives ticker removal |',
                 '|---|---|---:|---|---:|---|']
        for kind in ('move_onset', 'move_peak', 'actual_entry', 'actual_exit'):
            ranked = sorted((r for r in summary['contrasts'] if r['kind']==kind and r['offset_s']==0 and min(r['positive_n'],r['negative_n'])>=5), key=lambda r:abs(r['auc']-.5), reverse=True)[:8]
            for r in ranked:
                lines.append(f"| {kind} | {r['feature']} | {r['positive_n']}/{r['positive_total']} / {r['negative_n']}/{r['negative_total']} | {r['positive_median']:.3g} / {r['negative_median']:.3g} | {r['auc']:.3f} | {r['direction_survives_every_ticker_removal']} |")
        if cache is not None:
            lines.extend(['','## Volatility check','','Compare raw and ATR-normalized associations before inferring a distinctive setup. An AUC near 0.5 indicates little rank separation in this sample. These are associations, not trading accuracy.','',
                '| Anchor | Feature | Raw AUC | ATR-normalized AUC | Normalized direction survives ticker removal |',
                '|---|---|---:|---:|---|'])
            index={(r['kind'],r['offset_s'],r['feature']):r for r in summary['contrasts']}
            for kind in ('move_onset','actual_entry'):
                for tf in ('1s','5s'):
                    for name in ('histogram_bps','histogram_slope_bps_per_second'):
                        key=f'completed_{tf}.{name}'
                        raw=index.get((kind,0,key)); normalized=index.get((kind,0,key+'_per_atr_bps'))
                        if raw and normalized:
                            lines.append(f"| {kind} | {key} | {raw['auc']:.3f} | {normalized['auc']:.3f} | {normalized['direction_survives_every_ticker_removal']} |")
        (output/'report.md').write_text('\n'.join(lines)+'\n', encoding='utf-8')
        state.update(status='completed', active=0, completed=1,
            outputs={name:digest(output/name) for name in ('samples.json','comparison.json','report.md')})
        save(manifest, state)
        print(f"Completed: {len(points)} anchors, {len(rows)} samples; {summary['coverage']}. Report: {output/'report.md'}", flush=True)
    except BaseException as exc:
        state.update(status='interrupted' if isinstance(exc,KeyboardInterrupt) else 'failed', active=0, failed=int(not isinstance(exc,KeyboardInterrupt)), error=str(exc))
        save(manifest, state)
        raise


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', type=Path, default=ROOT/'shared15-v31-focused-audit')
    parser.add_argument('--output', type=Path, default=ROOT/'v31-market-state-comparison-v3')
    parser.add_argument('--cache',type=Path,required=True,help='Certified canonical prepared-frame SQLite cache matching the replay authority')
    parser.add_argument('--variant', default='regular-origin-v31')
    parser.add_argument('--sequence-manifest',type=Path,help='Optional completed native sequence recording from this same replay')
    args = parser.parse_args()
    run(args.source, args.output, args.variant,args.cache,args.sequence_manifest)
