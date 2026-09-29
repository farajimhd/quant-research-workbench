"""Serial day controller for the certified V6 candle/opportunity compiler.

Runs one full-market day at a time. Each day independently resumes from its
own plan and per-listing progress. A missing source certificate stops the
controller; it never substitutes a stale day or silently skips a date.
"""
from __future__ import annotations

import argparse
from datetime import date
import json
import os
from pathlib import Path

from research.rl_trading.v6.build import main as build_day
from research.rl_trading.v6.split import CONTEXT_ONLY, TRAIN, DEVELOPMENT


DATES = CONTEXT_ONLY + TRAIN + DEVELOPMENT


def manifest_for(day: date, early: Path, late: Path) -> Path:
    return early if day <= date(2026, 8, 17) else late


def day_arguments(day: date, previous: date | None, *, early: Path,
                  late: Path, ledger: Path, output: Path,
                  workers: int) -> list[str]:
    args = ['--manifest', str(manifest_for(day, early, late)),
            '--ledger', str(ledger), '--date', str(day),
            '--workers', str(workers), '--output', str(output)]
    if previous is None:
        if day not in CONTEXT_ONLY:
            raise ValueError('Only context-only date may omit prior session')
        args.append('--context-only')
    else:
        args.extend(['--previous-manifest',
                     str(manifest_for(previous, early, late)),
                     '--previous-date', str(previous)])
    return args


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--early-manifest', type=Path, required=True)
    parser.add_argument('--late-manifest', type=Path, required=True)
    parser.add_argument('--ledger', type=Path, required=True)
    parser.add_argument('--output-root', type=Path, required=True)
    parser.add_argument('--workers', type=int, default=16)
    parser.add_argument('--through', type=date.fromisoformat,
                        default=DEVELOPMENT[-1])
    args = parser.parse_args(argv)
    runtime = Path(os.environ.get('QW_RUNTIME_ROOT', '')).resolve()
    root = args.output_root.resolve()
    if not runtime.is_dir() or not root.is_relative_to(runtime):
        raise ValueError('Campaign output must be under the required runtime root')
    if args.through not in DATES:
        raise ValueError('Campaign end must be a certified train/development day')
    for index, day in enumerate(DATES):
        if day > args.through:
            break
        previous = DATES[index-1] if index else None
        output = root / str(day)
        print(json.dumps({'day': str(day), 'status': 'starting',
                          'previous_day': str(previous) if previous else None}),
              flush=True)
        build_day(day_arguments(day, previous, early=args.early_manifest,
                                late=args.late_manifest, ledger=args.ledger,
                                output=output, workers=args.workers))
        if not (output / 'complete.json').is_file():
            raise ValueError(f'{day}: builder returned without a certificate')
        print(json.dumps({'day': str(day), 'status': 'certified',
                          'output': str(output)}), flush=True)
    return 0


if __name__ == '__main__':
    os.environ['PYTHONDWRITEBYTECODE'] = '1'
    raise SystemExit(main())
