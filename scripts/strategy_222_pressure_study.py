"""Collect causal pressure trajectories from canonical events around research entries.

Uses the existing 1s/3s pressure contract unchanged. Timestamp-tied events are
excluded from each sample, conservatively. This is research, not an order replay.
"""
import os
os.environ['PYTHONDONTWRITEBYTECODE'] = '1'
import sys
sys.dont_write_bytecode = True
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import argparse
import asyncio
from datetime import datetime, timedelta, timezone
import hashlib
import json
from types import SimpleNamespace

from src.backend.qmd_gateway_client import qmd_history_base_url
from src.market_engine.historical_source import QmdHistoricalEventSource, event_from_qmd_payload
from src.trading_runtime.market_pressure import PressureTracker, DEFAULT_POLICY, evaluate


class PressureTimeline:
    """Stream once; emit snapshots before ingesting events tied to their time."""
    def __init__(self, start, end, step_ms=200):
        if start.tzinfo is None or end.tzinfo is None or end < start:
            raise ValueError('Ordered timezone-aware sample boundaries required')
        if type(step_ms) is not int or not 1 <= step_ms <= 250:
            raise ValueError('Sampling must preserve the pressure policy confirmation clock')
        self.next_us = round(start.timestamp() * 1e6)
        self.end_us = round(end.timestamp() * 1e6)
        self.step_us = step_ms * 1000
        self.tracker = PressureTracker()
        self.state = {}
        self.rows = []
        self.previous = None

    def emit_until(self, boundary_us):
        while self.next_us <= min(boundary_us, self.end_us):
            at = datetime.fromtimestamp(self.next_us / 1e6, timezone.utc)
            snapshot = self.tracker.snapshot(at)
            value = evaluate(DEFAULT_POLICY, self.state,
                             SimpleNamespace(observed_at=at, market_pressure=snapshot))
            self.rows.append(value)
            self.next_us += self.step_us

    def observe(self, event):
        key = (event.ts, event.sequence)
        if self.previous is not None and key < self.previous:
            raise ValueError('Canonical events are not ordered')
        self.emit_until(round(event.ts.timestamp() * 1e6))
        self.tracker.observe(event)
        self.previous = key

    def finish(self):
        self.emit_until(self.end_us)
        return self.rows


def atomic_json(path, value):
    temporary = path.with_suffix(path.suffix + '.tmp')
    temporary.write_text(json.dumps(value, indent=2), encoding='utf-8')
    temporary.replace(path)


async def collect(inputs, output, before_seconds=15, after_seconds=30):
    if before_seconds < 0 or after_seconds < 0 or before_seconds + after_seconds > 600:
        raise ValueError('Research windows must be nonnegative and at most 600 seconds')
    output.resolve().relative_to(Path('D:/TradingML/runtimes').resolve())
    output.mkdir(parents=True, exist_ok=True)
    identity = dict(inputs={str(p.resolve()): hashlib.sha256(p.read_bytes()).hexdigest() for p in inputs},
                    before_seconds=before_seconds, after_seconds=after_seconds, step_ms=200,
                    source_script_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
                    pressure_contract_sha256=hashlib.sha256(Path('src/trading_runtime/market_pressure.py').read_bytes()).hexdigest())
    path = output / 'manifest.json'
    if path.exists():
        manifest = json.loads(path.read_text())
        if manifest['identity'] != identity:
            raise ValueError('Research identity changed; preserve this output and choose a successor')
    else:
        manifest = dict(identity=identity, completed=[], status='running', failed=None)
    requests = []
    for p in inputs:
        payload = json.loads(p.read_text())
        requests.extend(dict(run_id=payload['run_id'], **row) for row in payload['entries'])
    manifest['total'] = len(requests)
    atomic_json(path, manifest)
    for number, row in enumerate(requests):
        key = f"{row['run_id']}:{row['symbol']}:{row['entry']}"
        destination = output / f'{number:03d}.json'
        prior = next((v for v in manifest['completed'] if v['key'] == key), None)
        if prior:
            if not destination.exists() or hashlib.sha256(destination.read_bytes()).hexdigest() != prior['sha256']:
                raise ValueError('Completed pressure artifact missing or modified')
            continue
        print(f"Active {number+1}/{len(requests)}: {row['symbol']}; completed={len(manifest['completed'])}, queued={len(requests)-number-1}", flush=True)
        at = datetime.fromtimestamp(row['decision_at'], timezone.utc)
        start, end = at - timedelta(seconds=before_seconds), at + timedelta(seconds=after_seconds)
        timeline = PressureTimeline(start, end)
        source = QmdHistoricalEventSource(qmd_history_base_url(), start=start-timedelta(seconds=4),
                                         end=end, tickers=[row['symbol']], batch_size=10000)
        event_hash, count = hashlib.sha256(), 0
        try:
            async for batch in source.stream_rows():
                for raw in batch:
                    event = event_from_qmd_payload(raw)
                    timeline.observe(event)
                    event_hash.update(json.dumps(raw, sort_keys=True, separators=(',', ':')).encode())
                    count += 1
            samples = timeline.finish()
            artifact = dict(request=row, start=start.isoformat(), end=end.isoformat(),
                            events=count, source_revision=source.source_revision,
                            event_payload_sha256=event_hash.hexdigest(), samples=samples,
                            method='Strictly before each sample timestamp; existing 1s/3s tracker and policy. '
                            '200ms grid. Future outcomes remain labels, not pressure inputs. '
                            'Post-entry horizon is explicit and may precede the actual exit.')
            atomic_json(destination, artifact)
            manifest['completed'].append(dict(key=key, file=destination.name,
                sha256=hashlib.sha256(destination.read_bytes()).hexdigest(), samples=len(samples), events=count))
            manifest.update(status='running', failed=None)
            atomic_json(path, manifest)
        except Exception as exc:
            manifest.update(status='failed', failed=dict(key=key, error=str(exc)))
            atomic_json(path, manifest)
            raise
    manifest.update(status='completed', failed=None)
    atomic_json(path, manifest)
    print(f"Completed {len(requests)} trajectories. Output: {output}", flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--inputs', type=Path, nargs='+', required=True,
                        help='Sequence-aware MACD entry reports provide the pre-fill decision clock')
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--before-seconds', type=int, default=15)
    parser.add_argument('--after-seconds', type=int, default=30)
    args = parser.parse_args()
    asyncio.run(collect(args.inputs, args.output, args.before_seconds, args.after_seconds))


if __name__ == '__main__':
    main()
