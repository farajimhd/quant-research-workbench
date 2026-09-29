"""Restart-safe bar-only geometry campaign over the forward train/dev split.

The controller waits for each certified feature bank, then reads sparse
one-second extrema once. It never opens the sealed test day and never marks
geometry as completed teacher supervision.
"""
from __future__ import annotations

import argparse
from datetime import date
import json
import os
from pathlib import Path
import time

from research.rl_trading.v6.build_price_action_geometry import main as build_day
from research.rl_trading.v6.split import TRAIN, DEVELOPMENT


DATES = TRAIN + DEVELOPMENT
VERSION = 'rl-trading-v6-price-action-geometry-campaign-1'


def run_available(source_manifest: Path, output: Path, *, early: Path,
                  late: Path, ledger: Path,
                  done: tuple[str, ...] = ()) -> dict:
    """Process only source-certified days in chronological order."""
    state = json.loads(source_manifest.read_text())
    if state.get('version') != 'rl-trading-v6-forward-candle-day-roots':
        raise ValueError('Unknown feature-bank campaign manifest')
    roots = state['day_roots']
    if done != tuple(str(day) for day in DATES[:len(done)]):
        raise ValueError('Completed dates are not a forward prefix')
    complete = list(done)
    for day in DATES[len(done):]:
        root = roots.get(str(day))
        if root is None:
            break
        source = Path(root)
        if not (source / 'complete.json').is_file():
            raise ValueError(f'{day}: feature day listed without certificate')
        build_day(['--source-root', str(source), '--manifest',
                   str(early if day <= date(2026, 8, 17) else late),
                   '--ledger', str(ledger), '--date', str(day), '--output',
                   str(output / str(day))])
        complete.append(str(day))
    return {'version': VERSION, 'completed': complete,
            'queued': [str(day) for day in DATES[len(complete):]],
            'failed': []}


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source-manifest', type=Path, required=True)
    parser.add_argument('--output-root', type=Path, required=True)
    parser.add_argument('--early-manifest', type=Path, required=True)
    parser.add_argument('--late-manifest', type=Path, required=True)
    parser.add_argument('--ledger', type=Path, required=True)
    parser.add_argument('--poll-seconds', type=int, default=60)
    parser.add_argument('--once', action='store_true')
    args = parser.parse_args(argv)
    runtime = Path(os.environ.get('QW_RUNTIME_ROOT', '')).resolve()
    output = args.output_root.resolve()
    if (not runtime.is_dir() or not output.is_relative_to(runtime) or
            not args.source_manifest.resolve().is_relative_to(runtime) or
            not 5 <= args.poll_seconds <= 3600):
        raise ValueError('Geometry campaign requires bounded runtime roots')
    output.mkdir(parents=True, exist_ok=True)
    done: tuple[str, ...] = ()
    while True:
        state = run_available(args.source_manifest, output,
                              early=args.early_manifest,
                              late=args.late_manifest, ledger=args.ledger,
                              done=done)
        done = tuple(state['completed'])
        temporary = output / 'campaign-state.json.tmp'
        temporary.write_text(json.dumps(state, sort_keys=True),
                             encoding='utf-8')
        temporary.replace(output / 'campaign-state.json')
        print(json.dumps(state, sort_keys=True), flush=True)
        if args.once or not state['queued']:
            return 0
        time.sleep(args.poll_seconds)


if __name__ == '__main__':
    os.environ['PYTHONDONTWRITEBYTECODE'] = '1'
    raise SystemExit(main())
