"""Audit fresh local pivots merged into anchored levels in a native recording.

This reproduces the existing swing subsystem; it does not change entry policy.
"""
import os
os.environ['PYTHONDONTWRITEBYTECODE'] = '1'
import sys
sys.dont_write_bytecode = True
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import argparse
from collections import Counter
import gzip
import json

from src.market_engine.swing_structure import SwingSettings, SwingStructure
from src.market_engine.structural_detector import DetectorSettings
from src.market_engine import swing_pivot_witness as witnesses
from src.runtime_paths import runtime_root
from strategy_222_recorded_sequences import recording
from strategy_222_supervised_research import digest, save


class PivotAudit(SwingStructure):
    """Observe the actual level selected by _found, without copying its policy."""
    def __init__(self, settings):
        super().__init__(settings)
        self.clock = {}
        self.events = []
        self.pending = None
        self.selected = None

    def _level_updated(self, level):
        super()._level_updated(level)
        if self.pending is not None:
            self.selected = dict(level)

    def _found(self, detector, extreme, side, t, reason='reversal_confirmed'):
        before = self.sequence
        self.pending, self.selected = True, None
        try:
            super()._found(detector, extreme, side, t, reason)
            if detector['scale'] == 'local' and side == 'support':
                level = self.selected
                if level is None:
                    raise ValueError('Local pivot has no selected anchored level')
                witness = witnesses.capture(level, extreme, t, reason)
                self.events.append(dict(at=self.clock[t], pivot_at=self.clock[extreme[1]],
                    pivot_price=extreme[0], reversal_distance=extreme[2],
                    merged=self.sequence == before, level_id=level['level_id'],
                    level_price=level['price'], level_lower=level['lower'], level_upper=level['upper'],
                    level_pivot_at=self.clock[level['pivot_at']],
                    level_confirmed_at=self.clock[level['confirmed_at']],
                    fresh_pivot_witness=witnesses.event_times(witness, self.clock) if witness else None))
        finally:
            self.pending = None


def run(manifest, run_id, configuration, summary, output):
    output = output.resolve()
    output.relative_to(runtime_root().resolve())
    if output.exists():
        raise ValueError('Use a new output file')
    path, receipt = recording(manifest, run_id)
    state = json.loads(manifest.read_text())
    pins = state['identity']['sequence_recorder_sources']
    repository = Path(__file__).resolve().parents[1]
    for name in ('swing_structure.py', 'structural_detector.py'):
        source = repository/'src/market_engine'/name
        if digest(source) != pins[str(source.relative_to(repository))]:
            raise ValueError('Swing source differs from recorded native implementation')
    config, run_summary = json.loads(configuration.read_text()), json.loads(summary.read_text())
    if (run_summary['run_id'] != run_id or run_summary['status'] != 'completed'
            or run_summary['configuration_content_hash'] != config['content_hash']
            or run_summary['configuration_revision_id'] != config['revision_id']):
        raise ValueError('Configuration authority differs from completed replay')
    settings = DetectorSettings(**(config['payload']['strategy']['parameters'].get('structural_detector_settings') or {}))
    swing_settings = SwingSettings(reversal_bps=settings.reversal_bps,
        volatility_multiple=settings.volatility_multiple)
    streams, counts, events = {}, Counter(), []
    support_tags = {'local:swing_low_confirmed', 'local:higher_low_confirmed', 'local:equal_low_confirmed'}
    print('Pivot audit: active=1 queued=0 completed=0 failed=0', flush=True)
    with gzip.open(path, 'rt', encoding='utf-8') as source:
        for line in source:
            row = json.loads(line)
            if row['run_id'] != run_id:
                raise ValueError('Recording run mismatch')
            symbol, seq, at = row['symbol'], row['detector_sequence'], row['at']
            if type(seq) is not int or seq < 1:
                raise ValueError('Invalid native detector sequence')
            previous = streams.get(symbol)
            if previous and at <= previous['at']:
                raise ValueError('Unordered native candles')
            if not previous or previous['session'] != row['session'] or seq != previous['sequence']+1:
                if seq != 1:
                    raise ValueError('Missing native reset prefix')
                engine = PivotAudit(swing_settings)
            else:
                engine = previous['engine']
            bar = row['candle']
            if bar['end'] != at or at-bar['time'] != 1:
                raise ValueError('Invalid native candle clock')
            engine.clock[seq] = at
            engine.observe(seq, bar['high'], bar['low'], bar['close'])
            actual = any(l['scale'] == 'local' and l['side'] == 'support'
                and l['confirmed_at'] == seq for l in engine.active.values())
            if actual != bool(support_tags.intersection(row['labels']['structural_progression'])):
                raise ValueError(f'Native support-label parity failed for {symbol} at {at}')
            events.extend(dict(symbol=symbol, **e) for e in engine.events)
            engine.events.clear()
            engine.segments.clear()
            referenced = {seq}
            for level in engine.active.values():
                level.pop('segment', None)
                referenced.update((level['pivot_at'], level['confirmed_at']))
            for detector in engine.detectors:
                referenced.update(detector[k][1] for k in ('high', 'low') if detector.get(k))
            engine.clock = {k:v for k,v in engine.clock.items() if k in referenced}
            streams[symbol] = dict(engine=engine, sequence=seq, at=at, session=row['session'])
            counts[symbol] += 1
    if dict(counts) != receipt['by_symbol'] or sum(counts.values()) != receipt['rows']:
        raise ValueError('Native recording coverage mismatch')
    merged = [e for e in events if e['merged']]
    save(output, dict(status='completed', run_id=run_id,
        inputs={str(p.resolve()):digest(p) for p in (manifest, path, configuration, summary,
            Path(__file__), Path(witnesses.__file__))},
        source_pins=pins, candles=dict(counts), support_label_parity_rows=sum(counts.values()),
        support_pivots=len(events), merged_support_pivots=len(merged),
        merged_by_symbol=dict(Counter(e['symbol'] for e in merged)), events=events,
        limitations='Local swing subsystem reconstruction only; every native support-confirmation label matched. Merged pivot witnesses are diagnostic observations, not entry permission or profitable trades. Level identity and original clocks remain unchanged. No global-book, recovery, liquidity, sizing or execution policy is bypassed.'))
    print(f'Pivot audit: active=0 queued=0 completed=1 failed=0 candles={sum(counts.values())} merged_supports={len(merged)}', flush=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('manifest', 'configuration', 'summary', 'output'):
        parser.add_argument('--'+name, required=True, type=Path)
    parser.add_argument('--run-id', required=True)
    args = parser.parse_args()
    run(args.manifest, args.run_id, args.configuration, args.summary, args.output)
