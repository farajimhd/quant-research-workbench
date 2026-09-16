"""Offline local-swing geometry/clock comparison; no parent imports or services."""
import argparse
import hashlib
import json
import math
from pathlib import Path
import random
import subprocess
import sys
import types


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--rust-executable', required=True)
    args = parser.parse_args()
    root = Path(__file__).resolve().parent
    manifest = json.loads((root / 'origin.json').read_text())
    entry = next(e for e in manifest['files'] if e['source'] == 'src/market_engine/swing_structure.py')
    source = (root.parent.parent / entry['destination']).read_bytes()
    if hashlib.sha256(source).hexdigest() != entry['sha256']:
        raise RuntimeError('Frozen swing source hash differs')
    module = types.ModuleType('arte_frozen_swing_reference')
    sys.modules[module.__name__] = module
    exec(compile(source, entry['destination'], 'exec'), module.__dict__)
    config = dict(reversal_bps=50., volatility_multiple=2., volatility_cap_multiple=2., lifetime_bars=35, maximum_levels=1000)
    settings = module.SwingSettings(reversal_bps=config['reversal_bps'], volatility_multiple=config['volatility_multiple'], volatility_cap_multiple=config['volatility_cap_multiple'], local_lifetime_seconds=config['lifetime_bars'], max_active=10000, max_segments=100000)
    checked = 0
    for case in range(6):
        rng = random.Random(8200 + case)
        price = 0.5 if case % 2 else 10.
        bars = []
        second = 200
        for i in range(500):
            if i and i % 137 == 0:
                second += 2
            op = price
            price = max(0.05, price * (1 + rng.choice([-1, 1]) * rng.uniform(0, .025)))
            spread = price * rng.uniform(.0001, .003)
            bars.append(dict(start_ns=second*10**9, end_ns=(second+1)*10**9, open=op, high=max(op,price)+spread, low=min(op,price)-spread, close=price, volume=1., notional=price, trades=1))
            second += 1
        result = subprocess.run([args.rust_executable], input=json.dumps(dict(config=config, bars=bars)), text=True, capture_output=True, check=True, timeout=60)
        actual = json.loads(result.stdout)
        if len(actual) != len(bars):
            raise AssertionError('Snapshot count differs')
        engine = module.SwingStructure(settings)
        clocks = {}
        sequence = 0
        prior_end = None
        for index, (bar, got) in enumerate(zip(bars, actual)):
            gap = prior_end is not None and bar['start_ns'] > prior_end
            if gap:
                engine = module.SwingStructure(settings)
                clocks = {}
                sequence = 0
            sequence += 1
            clocks[sequence] = bar['end_ns']

            def compact(level):
                return dict(lower=level['lower'], price=level['price'], upper=level['upper'], pivot_at_ns=clocks[level['pivot_at']], confirmed_at_ns=clocks[level['confirmed_at']], support=level['side']=='support', active=level['state']=='active')

            expected = [compact(l) for l in engine.active.values() if l['scale']=='local' and l['state']=='active']
            engine.observe(sequence, bar['high'], bar['low'], bar['close'])
            expected += [compact(l) for l in engine.active.values() if l['scale']=='local' and l['confirmed_at']==sequence]
            engine.segments.clear()
            for level in engine.active.values():
                level.pop('segment', None)
            if got['at_ns'] != bar['end_ns'] or got['gap_reset'] != gap or len(got['swings']) != len(expected):
                raise AssertionError(f'Case {case} candle {index}: snapshot shape differs')
            for left, right in zip(got['swings'], expected):
                # ARTE deliberately namespaces IDs. Local insertion order remains
                # stable; major visual-level numbering is not strategy evidence.
                for key, value in right.items():
                    if key in ('lower', 'price', 'upper'):
                        equal = math.isclose(left[key], value, rel_tol=1e-12, abs_tol=1e-12)
                    else:
                        equal = left[key] == value
                    if not equal:
                        raise AssertionError(f'Case {case} candle {index}: {key}: {left[key]} != {value}')
            prior_end = bar['end_ns']
            checked += 1
    print(f'Passed frozen local-swing comparison: {checked} completed candles, six deterministic paths. Geometry/clocks/roles only; no profitability claim.')


if __name__ == '__main__':
    main()
