"""Collect native MACD evidence for the fixed study population, with resumable outputs."""
import os
os.environ['PYTHONDONTWRITEBYTECODE'] = '1'
import sys
sys.dont_write_bytecode = True
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import argparse
from bisect import bisect_left
import json

from src.backend.qmd_gateway_client import QmdProductRequest, qmd_product_request, qmd_materialize_indicator_warmup
from scripts.strategy_222_macd_episodes import analyze_frames, timestamp, digest
from scripts.strategy_222_pressure_study import atomic_json


def validate_native(payload, warmup, symbol, seconds, start, end):
    timeframe = f'{seconds}s'
    if warmup['status'] != 'ready' or len(warmup['bars']) < warmup['required_bars']:
        raise ValueError('Complete native indicator warmup required')
    if warmup['ticker'] != symbol or warmup['timeframe'] != timeframe or timestamp(warmup['session_start']) != start:
        raise ValueError('Warmup identity mismatch')
    if any(timestamp(b['bar_start']) + seconds > start for b in warmup['bars']):
        raise ValueError('Warmup includes future candles')
    if payload['ticker'] != symbol or payload['timeframe'] != timeframe or payload.get('has_more'):
        raise ValueError('Wrong identity or truncated native chart')
    revision = payload['cache']['source_revision']
    if not revision.get('complete_for_history') or not revision.get('request_complete') or not payload['indicator_provenance'].get('complete'):
        raise ValueError('Incomplete canonical source or indicators')
    bars = payload['bars']; indicators = payload['indicators']
    if len(bars) >= 50000 or len(bars) != len(indicators):
        raise ValueError('Incomplete bar/indicator correspondence')
    by_start = {r['bar_start']: r for r in indicators}
    if len(by_start) != len(indicators):
        raise ValueError('Duplicate indicator candles')
    for b in bars:
        if b['sym'] != symbol or b['timeframe'] != timeframe or not b['is_closed'] or not start <= timestamp(b['bar_start']) < timestamp(b['bar_end']) <= end:
            raise ValueError('Invalid completed native candle')
        if timestamp(b['bar_end']) - timestamp(b['bar_start']) != seconds or b['bar_start'] not in by_start:
            raise ValueError('Native candle interval or indicator mismatch')
    return analyze_frames(((timestamp(b['bar_end']), b, by_start[b['bar_start']]) for b in bars), seconds)


def run(inputs, output, start, end, seconds):
    first, last = timestamp(start), timestamp(end)
    if seconds not in (1, 2, 5) or not 0 < last-first < 50000*seconds:
        raise ValueError('Use a positive bounded interval below the native chart row limit')
    output.resolve().relative_to(Path('D:/TradingML/runtimes').resolve())
    reports = [json.loads(p.read_text()) for p in inputs]
    symbols = sorted({key.split(':')[0] for r in reports for key in r['authorities']})
    identity = dict(inputs={str(p.resolve()): digest(p) for p in inputs}, start=start, end=end,
                    seconds=seconds, symbols=symbols, script_sha256=digest(Path(__file__)),
                    analyzer_sha256=digest(Path(__file__).with_name('strategy_222_macd_episodes.py')))
    output.mkdir(parents=True, exist_ok=True); path = output/'manifest.json'
    manifest = json.loads(path.read_text()) if path.exists() else dict(identity=identity, completed=[], status='running')
    if manifest['identity'] != identity:
        raise ValueError('Study identity changed; use a successor output')
    for number, symbol in enumerate(symbols):
        destination = output/f'{number:03d}.json'
        prior = next((r for r in manifest['completed'] if r['symbol'] == symbol), None)
        if prior:
            if not destination.exists() or digest(destination) != prior['sha256']:
                raise ValueError('Completed native evidence missing or changed')
            continue
        print(f'Active {number+1}/{len(symbols)}: {symbol}; completed={len(manifest["completed"])}, queued={len(symbols)-number-1}', flush=True)
        manifest.update(status='running', active=symbol, error=None); atomic_json(path, manifest)
        try:
            warmup = qmd_materialize_indicator_warmup(ticker=symbol, timeframe=f'{seconds}s', session_start=start)
            payload = qmd_product_request(QmdProductRequest('chart', authority='history', mode='backtest', ticker=symbol,
                timeframe=f'{seconds}s', start=start, end=end, as_of=end, indicator_columns=('macd_line','macd_signal','macd_histogram'),
                include_market_signals=False, include_structure=False, stage='bars', limit=50000, timeout_seconds=180)).payload
            snapshots, labels = validate_native(payload, warmup, symbol, seconds, first, last)
            times = [s['as_of'] for s in snapshots]; entries = []
            for report in reports:
                for entry in report['entries']:
                    if entry['symbol'] != symbol:
                        continue
                    cutoff = entry['decision_at']
                    if not first <= cutoff <= last:
                        raise ValueError('Entry clock outside requested study interval')
                    index = bisect_left(times, cutoff)-1
                    entries.append(dict(run_id=report['run_id'], symbol=symbol, entry=entry['entry'],
                        decision_at=cutoff, outcome=entry['outcome'], net=entry['net'],
                        feature=snapshots[index] if index >= 0 else None,
                        source_age_seconds=cutoff-times[index] if index >= 0 else None))
            artifact = dict(symbol=symbol, warmup=warmup, payload=payload, snapshots=snapshots,
                episode_labels=labels, entries=entries,
                method='Native canonical candles; entry feature uses candle end strictly before the recorded decision. '
                'Hypothetical availability, not proof of runtime delivery. Outcomes and episode endings are labels only.')
            atomic_json(destination, artifact)
            manifest['completed'].append(dict(symbol=symbol, file=destination.name, sha256=digest(destination), candles=len(snapshots)))
            atomic_json(path, manifest)
        except BaseException as exc:
            manifest.update(status='interrupted' if isinstance(exc, KeyboardInterrupt) else 'failed', error=str(exc))
            atomic_json(path, manifest)
            raise
    manifest.update(status='completed', active=None, error=None); atomic_json(path, manifest)
    print(f'Completed {len(symbols)} native {seconds}s streams. Output: {output}', flush=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--inputs', type=Path, nargs='+', required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--start', required=True)
    parser.add_argument('--end', required=True)
    parser.add_argument('--seconds', type=int, choices=(1,2,5), default=2)
    args = parser.parse_args()
    run(args.inputs, args.output, args.start, args.end, args.seconds)
