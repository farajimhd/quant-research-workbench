"""Audit independent completed-candle MACD episodes with causal entry features.

Future episode endings live only in labels. No parameter selection or trading
configuration mutation occurs here. Inputs must be completed replay artifacts.
"""
import os
os.environ['PYTHONDONTWRITEBYTECODE'] = '1'
import sys
sys.dont_write_bytecode = True
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import argparse
from bisect import bisect_right
from datetime import datetime
import hashlib
import json
import math
import sqlite3

from scripts.summarize_strategy_222_refinement import episodes


def timestamp(value):
    value = datetime.fromisoformat(value.replace('Z', '+00:00'))
    if value.tzinfo is None:
        raise ValueError('Timezone-aware evidence required')
    return value.timestamp()


def analyze_frames(frames, seconds):
    """Consume ordered (time, bar, indicator) rows; never interpolate gaps."""
    if seconds not in (1, 2, 5):
        raise ValueError('Expected independent 1s, 2s or 5s candles')
    snapshots, labels = [], []
    previous = None
    active = None
    for at, bar, indicator in frames:
        values = [at, *[bar[k] for k in ('open', 'high', 'low', 'close')],
                  *[indicator[k] for k in ('macd_line', 'macd_signal', 'macd_histogram')]]
        if any(type(v) not in (int, float) or not math.isfinite(v) for v in values):
            raise ValueError('Nonfinite or missing candle evidence')
        if previous and at <= previous['at']:
            raise ValueError('Duplicate or unordered completed candles')
        if timestamp(bar['bar_end']) != at:
            raise ValueError('Candle end differs from observation time')
        if not 0 < bar['low'] <= min(bar['open'], bar['close']) <= max(bar['open'], bar['close']) <= bar['high']:
            raise ValueError('Invalid OHLC geometry')
        histogram = indicator['macd_line'] - indicator['macd_signal']
        if not math.isclose(histogram, indicator['macd_histogram'], abs_tol=1e-9, rel_tol=1e-9):
            raise ValueError('MACD histogram disagrees with line and signal')
        normalized = histogram / bar['close'] * 10000
        gap = max(0., at - previous['at'] - seconds) if previous else None
        snapshot = dict(as_of=at, positive=histogram > 0, histogram_bps=normalized,
                        gap_since_previous_seconds=gap,
                        histogram_slope_bps_per_second=(normalized - previous['histogram_bps']) /
                        (at - previous['at']) if previous else None)
        if histogram > 0:
            if active is None:
                active = dict(start=at, left_censored=previous is None, right_censored=True,
                              positive_candles=0, max_gap_seconds=0., end=None, duration_seconds=None)
                labels.append(active)
                peak = bar['high']
                peak_histogram = normalized
                first_close = bar['close']
            active['positive_candles'] += 1
            if active['positive_candles'] > 1:
                active['max_gap_seconds'] = max(active['max_gap_seconds'], gap or 0.)
            peak = max(peak, bar['high'])
            peak_histogram = max(peak_histogram, normalized)
            snapshot.update(episode_start=active['start'],
                            episode_age_seconds=at - active['start'],
                            observed_candles=active['positive_candles'],
                            left_censored=active['left_censored'],
                            max_gap_seconds=active['max_gap_seconds'],
                            histogram_fraction_of_peak=normalized / peak_histogram,
                            pullback_pct=(peak - bar['close']) / peak * 100,
                            progress_pct=(bar['close'] / first_close - 1) * 100)
        elif active is not None:
            active.update(end=at, duration_seconds=at - active['start'],
                          right_censored=False, closing_gap_seconds=gap)
            active = None
        snapshots.append(snapshot)
        previous = dict(at=at, histogram_bps=normalized)
    return snapshots, labels


def select_observed(snapshots, times, cutoff, decision_at):
    """The cutoff is a delivered-frame watermark, not just wall-clock time."""
    if cutoff is None:
        return dict(status='no_delivered_frame_watermark')
    if cutoff > decision_at:
        raise ValueError('Delivered frame is in the future')
    index = bisect_right(times, cutoff) - 1
    if index < 0:
        return dict(status='no_prior_completed_frame')
    # A watermark not present in the canonical source is an authority mismatch.
    if times[index] != cutoff:
        raise ValueError('Delivered frame watermark absent from canonical cache')
    return dict(snapshots[index], status='measured', source_age_seconds=decision_at - cutoff)


def digest(path):
    value = hashlib.sha256()
    with path.open('rb') as stream:
        for chunk in iter(lambda: stream.read(8 * 1024 * 1024), b''):
            value.update(chunk)
    return value.hexdigest()


def closed_connection(path):
    wal = Path(str(path) + '-wal')
    if wal.exists() and wal.stat().st_size:
        raise ValueError(f'Wait for the journal/cache writer to close: {path}')
    return sqlite3.connect(path.resolve().as_uri() + '?mode=ro&immutable=1', uri=True)


def audit(cache, run_dir):
    summary = json.loads((run_dir / 'run-summary.json').read_text())
    # The persisted summary can lag at finalization. Terminal manifest is authoritative.
    manifest = json.loads((run_dir / 'manifest.json').read_text())
    if manifest.get('run', {}).get('status') != 'completed':
        raise ValueError('A completed replay manifest is required')
    streams, labels, authorities = {}, {}, {}
    connection = closed_connection(cache)
    try:
        for symbol in summary['tickers']:
            for timeframe, seconds in (('1s', 1), ('5s', 5)):
                authority = json.loads(connection.execute(
                    'select authority_json from strategy_frame_streams where ticker=? and timeframe=?',
                    (symbol, timeframe)).fetchone()[0])
                if not authority.get('complete_for_history') or authority != summary['data_authority']['sources'][f'derived:{symbol}:{timeframe}']:
                    raise ValueError('Prepared-frame authority differs from replay')
                rows = connection.execute('select as_of_us,bar_json,indicator_json from strategy_frames '
                                          'where ticker=? and timeframe=? order by as_of_us', (symbol, timeframe))
                snapshots, episode_labels = analyze_frames(
                    ((us / 1e6, json.loads(bar), json.loads(indicator)) for us, bar, indicator in rows), seconds)
                streams[(symbol, timeframe)] = (snapshots, [s['as_of'] for s in snapshots])
                labels[f'{symbol}:{timeframe}'] = episode_labels
                authorities[f'{symbol}:{timeframe}'] = authority
    finally:
        connection.close()
    journal = run_dir / 'journal.sqlite3'
    actual = episodes(journal)['episodes']
    entries = {e['fills'][0]['sequence']: e for e in actual}
    latest, delivered_1s, output = {}, {}, []
    connection = closed_connection(journal)
    try:
        query = "select sequence,event_time,category,payload_json from journal where category='strategy_decision' or (category='execution' and entity_type='fill') order by sequence"
        for sequence, stamp, category, raw in connection.execute(query):
            if category == 'strategy_decision':
                decision = json.loads(raw)
                symbol = decision.get('ticker')
                metadata = decision.get('metadata', {})
                macd = metadata.get('macd', {})
                if not macd:
                    continue
                at = timestamp(stamp)
                if any(str(s).startswith(f'qmd-derived:{symbol}:1s:') for s in decision.get('source_signal_ids', [])):
                    observed = macd.get('observed_at')
                    if observed == at:
                        delivered_1s[symbol] = at
                latest[symbol] = (sequence, at, macd.get('completed_base_at'), delivered_1s.get(symbol))
            elif sequence in entries:
                entry = entries[sequence]
                symbol = entry['symbol']
                if symbol not in latest:
                    output.append(dict(symbol=symbol, entry=entry['opened_at'], status='missing_pre_fill_decision'))
                    continue
                prior_sequence, at, five, one = latest[symbol]
                if prior_sequence >= sequence or at > timestamp(entry['opened_at']):
                    raise ValueError('Pre-fill decision ordering violated')
                features = {tf: select_observed(*streams[(symbol, tf)], cutoff, at)
                            for tf, cutoff in (('1s', one), ('5s', five))}
                output.append(dict(symbol=symbol, entry=entry['opened_at'], first_fill_sequence=sequence,
                                   decision_sequence=prior_sequence, decision_at=at, features=features,
                                   outcome='open' if 'closed_at' not in entry else 'win' if entry['net'] > 0 else 'loss',
                                   net=entry.get('net')))
    finally:
        connection.close()
    return dict(method='Independent canonical completed MACD. Entry features use journal-sequence-before-fill '
                'and delivered-frame watermarks. Future endings are labels only; no gap interpolation.',
                run_id=summary['run_id'], cache=str(cache), cache_sha256=digest(cache),
                journal_sha256=digest(journal), authorities=authorities,
                entries=output, episode_labels=labels)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--cache', type=Path, required=True)
    parser.add_argument('--run-dir', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    runtime = Path('D:/TradingML/runtimes').resolve()
    args.output.resolve().relative_to(runtime)
    print('Active: validate canonical authority and measure delivered MACD history.', flush=True)
    result = audit(args.cache, args.run_dir)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2), encoding='utf-8')
    print(f"Completed: {len(result['entries'])} entries, {sum(map(len, result['episode_labels'].values()))} episodes. "
          f"Future labels remain separate. Report: {args.output}", flush=True)


if __name__ == '__main__':
    main()
