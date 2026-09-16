"""Screen completed native candle bounces against frozen quote windows.

This is a descriptive experiment, not an entry rule. Overlapping observations
are not independent trades. Hindsight enters labels only.
"""
import os
os.environ['PYTHONDONTWRITEBYTECODE'] = '1'
import sys
sys.dont_write_bytecode = True
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import argparse
from collections import Counter, deque
from datetime import datetime
import gzip
import json
import math

import numpy as np
from src.runtime_paths import runtime_root
from strategy_222_recorded_sequences import recording
from strategy_222_supervised_research import digest, flat_entry_state, hindsight_label, save
from strategy_222_macd_episodes import closed_connection, timestamp


class BounceWindow:
    """One market stream; no symbols, outcomes, or absolute entry times as inputs."""
    def __init__(self):
        self.rows = deque(maxlen=3)
        self.last = None
        self.session = None

    def observe(self, row):
        bar, at, evidence = row['candle'], row['at'], row['evidence']
        values = [at, *[bar[k] for k in ('time', 'end', 'open', 'high', 'low', 'close')]]
        if not all(type(v) in (int, float) and math.isfinite(v) for v in values):
            raise ValueError('Invalid candle numbers')
        if (at != bar['end'] or at != evidence['through'] or at != evidence['observed_at']
                or evidence['candle_seconds'] != 1 or bar['end']-bar['time'] != 1
                or self.last is not None and at <= self.last
                or not 0 < bar['low'] <= min(bar['open'], bar['close'])
                <= max(bar['open'], bar['close']) <= bar['high']):
            raise ValueError('Invalid candle clock or geometry')
        if evidence['reset'] or row['session'] != self.session or bar['time'] != self.last:
            self.rows.clear()
        tags = set(row['labels']['interaction'])
        self.rows.append(dict(bar=dict(bar), rejected=bool(tags & {
            'local:support_rejection', 'global:support_rejection'})))
        self.last, self.session = at, row['session']
        if len(self.rows) != 3:
            return None
        previous, current = list(self.rows)[-2:]
        p, c = previous['bar'], current['bar']
        return dict(stop=min(r['bar']['low'] for r in self.rows),
            two_green=p['close'] > p['open'] and c['close'] > c['open'],
            rising_close=c['close'] > p['close'],
            support_rejection=previous['rejected'] or current['rejected'],
            candle_ends=[r['bar']['end'] for r in self.rows])


def attach_decisions(journal, run_id, samples):
    """Join exact completed-candle decisions; never backfill with a later decision."""
    wanted = {(s['symbol'], s['at']) for s in samples}
    found = {}
    connection = closed_connection(journal)
    try:
        for sequence, stamp, raw in connection.execute(
                "select sequence,event_time,payload_json from journal where run_id=? "
                "and category='strategy_decision' order by sequence", (run_id,)):
            at = timestamp(stamp)
            decision = json.loads(raw)
            symbol = decision['ticker']
            key = symbol, at
            if key not in wanted or not any(str(s).startswith(f'qmd-derived:{symbol}:1s:')
                    for s in decision.get('source_signal_ids', ())):
                continue
            if key in found:
                raise ValueError('Ambiguous exact completed-candle decision')
            metadata = decision.get('metadata') or {}
            checks = (metadata.get('liquidity_admission') or {}).get('checks') or {}
            found[key] = dict(status='matched', sequence=sequence, at=at,
                strategy_status=metadata.get('status'),
                flat_entry_state=flat_entry_state(metadata),
                action=decision.get('action'), first_blocker=decision.get('reason'),
                liquidity_failed=[k for k, v in checks.items() if v is False],
                liquidity_checks_available=bool(checks))
    finally:
        connection.close()
    counts = Counter()
    for sample in samples:
        context = found.get((sample['symbol'], sample['at']),
            dict(status='missing_exact_completed_decision'))
        sample['decision_context'] = context
        counts[context['status']] += 1
    return dict(counts)


def run(manifest, run_id, ledger_path, output, journal=None):
    output = output.resolve()
    output.relative_to(runtime_root().resolve())
    if output.exists():
        raise ValueError('Use a new output file to preserve previous evidence')
    path, receipt = recording(manifest, run_id)
    ledger = json.loads(ledger_path.read_text())
    windows = {}
    for key, value in ledger.items():
        w = value['window']
        start, end = datetime.fromisoformat(w['start']), datetime.fromisoformat(w['end'])
        if start.tzinfo is None or end.tzinfo is None:
            raise ValueError('Quote windows require aware timestamps')
        windows.setdefault(w['symbol'], []).append((start.timestamp(), end.timestamp(), key, value))
    streams, counts, reasons, samples, hashes = {}, Counter(), Counter(), [], {}
    cached_key, quotes = None, None
    print('Bounce screen: active=1 queued=0 completed=0 failed=0', flush=True)
    with gzip.open(path, 'rt', encoding='utf-8') as source:
        for line in source:
            row = json.loads(line)
            if row['run_id'] != run_id:
                raise ValueError('Recording run mismatch')
            symbol, at = row['symbol'], row['at']
            counts[symbol] += 1
            measured = streams.setdefault(symbol, BounceWindow()).observe(row)
            if measured is None:
                reasons['insufficient_contiguous_candles'] += 1
                continue
            matches = [w for w in windows.get(symbol, ()) if w[0] <= at <= w[1]]
            if not matches:
                reasons['outside_frozen_quote_windows'] += 1
                continue
            _, _, key, authority = max(matches, key=lambda w: (w[1], w[2]))
            if authority['status'] != 'completed' or not authority['source_revision']['complete_for_history']:
                raise ValueError('Incomplete quote authority')
            if cached_key != key:
                quote_path = ledger_path.parent / f'quotes-{key}.npz'
                sha = digest(quote_path)
                if sha != authority['sha256']:
                    raise ValueError('Quote source changed')
                hashes[key] = sha
                with np.load(quote_path) as archive:
                    quotes = archive['data']
                if not len(quotes) or not np.isfinite(quotes[:, 0]).all() or (np.diff(quotes[:, 0]) < 0).any():
                    raise ValueError('Empty or unordered quotes')
                cached_key = key
            label = hindsight_label(quotes, at, measured['stop'])
            candidate = all(measured[k] for k in ('two_green', 'rising_close', 'support_rejection'))
            reasons['valid_label' if label['valid'] else label['reason']] += 1
            samples.append(dict(symbol=symbol, at=at, window=key, candidate=candidate,
                features=measured, label=label))
    if dict(counts) != receipt['by_symbol'] or sum(counts.values()) != receipt['rows']:
        raise ValueError('Recording coverage mismatch')
    context_counts = attach_decisions(journal, run_id, samples) if journal else None
    cohorts = {}
    for candidate in (False, True):
        valid = [s for s in samples if s['candidate'] == candidate and s['label']['valid']]
        cohorts[str(candidate)] = dict(rows=len(valid),
            profitable=sum(s['label']['profitable'] for s in valid),
            major_good=sum(s['label']['major_good'] for s in valid),
            by_symbol=dict(Counter(s['symbol'] for s in valid)))
        if journal:
            flat = [s for s in valid if s['decision_context'].get('flat_entry_state') is True]
            cohorts[str(candidate)]['flat_state'] = dict(rows=len(flat),
                profitable=sum(s['label']['profitable'] for s in flat),
                major_good=sum(s['label']['major_good'] for s in flat))
    save(output, dict(status='completed', run_id=run_id,
        inputs={str(p.resolve()): digest(p) for p in (manifest, path, ledger_path, Path(__file__),
            Path(__file__).with_name('strategy_222_recorded_sequences.py'),
            Path(__file__).with_name('strategy_222_macd_episodes.py'),
            Path(__file__).with_name('strategy_222_supervised_research.py'))},
        journal=({'path': str(journal.resolve()), 'sha256': digest(journal),
            'context_counts': context_counts} if journal else None),
        quote_hashes=hashes, recording_counts=dict(counts), reasons=dict(reasons),
        cohorts=cohorts, samples=samples,
        policy='Two completed green candles, rising close, support rejection on either candle; raw minimum of three completed candle lows as research stop. 100 shares, 100ms delay, 5bps slippage per side, $1 fee per side, 2R target, 300s horizon.',
        limitations='Overlapping position-selected development observations are not independent trades. Optional exact decision joins describe the original strategy state, not counterfactual state under a changed policy. First blocker is not every failed gate; missing checks are not passing checks. No MACD, liquidity, level-room, portfolio or OMS admission is applied. Raw candle low has no strategy stop buffer. This screen does not establish executable strategy returns or generalization.'))
    print(f'Bounce screen: active=0 queued=0 completed=1 failed=0 samples={len(samples)}', flush=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('manifest', 'ledger', 'output'):
        parser.add_argument('--'+name, required=True, type=Path)
    parser.add_argument('--run-id', required=True)
    parser.add_argument('--journal', type=Path, help='Optional closed journal for exact strategy-state context')
    args = parser.parse_args()
    run(args.manifest, args.run_id, args.ledger, args.output, args.journal)
