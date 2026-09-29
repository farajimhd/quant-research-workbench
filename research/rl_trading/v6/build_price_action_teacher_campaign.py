"""Restart-safe forward V6 price-action teacher campaign.

Each certified train/development day is consumed once in order: sparse bar
geometry, bracket labels, then a cash-reconciled hypothetical trajectory.
The sealed test date is deliberately absent from this controller.
"""
from __future__ import annotations

import argparse
from datetime import date
import json
import os
from pathlib import Path
import time

from research.rl_trading.v6.build_price_action_geometry import main as geometry_day
from research.rl_trading.v6.build_price_action_brackets import main as brackets_day
from research.rl_trading.v6.build_price_action_teacher import main as teacher_day
from research.rl_trading.v6.split import CONTEXT_ONLY, TRAIN, DEVELOPMENT


DATES = TRAIN + DEVELOPMENT
VERSION = 'rl-trading-v6-price-action-teacher-campaign-1'


def run_available(source_manifest: Path, output: Path, *, early: Path,
                  late: Path, ledger: Path,
                  allocation_roots: dict[date, Path],
                  done: tuple[str, ...] = ()) -> dict:
    """Use certificates as restart state; no date is skipped on a bad input."""
    state = json.loads(source_manifest.read_text(encoding='utf-8'))
    if state.get('version') != 'rl-trading-v6-forward-candle-day-roots':
        raise ValueError('Unknown feature-bank campaign manifest')
    if done != tuple(str(day) for day in DATES[:len(done)]):
        raise ValueError('Completed dates are not a forward prefix')
    roots = state['day_roots']
    complete: list[str] = list(done)
    previous = DATES[len(done)-1] if done else CONTEXT_ONLY[0]
    for day in DATES[len(done):]:
        source_root = roots.get(str(day))
        previous_root = roots.get(str(previous))
        if source_root is None or previous_root is None:
            break
        source = Path(source_root)
        prior = Path(previous_root)
        if not (source / 'complete.json').is_file():
            raise ValueError(f'{day}: feature day listed without certificate')
        if not (prior / 'complete.json').is_file():
            raise ValueError(f'{day}: previous context day lacks certificate')
        geometry_root = output / 'geometry' / str(day)
        bracket_root = output / 'brackets' / str(day)
        teacher_root = output / 'teacher' / str(day)
        allocation = allocation_roots.get(day)
        geometry_args = ['--source-root', str(source), '--manifest',
            str(early if day <= date(2026, 8, 17) else late),
            '--ledger', str(ledger), '--date', str(day),
            '--output', str(geometry_root)]
        if allocation is not None:
            geometry_args.extend(['--allocation-root', str(allocation)])
        geometry_day(geometry_args)
        brackets_day(['--geometry-root', str(geometry_root),
                      '--output', str(bracket_root)])
        teacher_args = ['--source-root', str(source),
            '--previous-root', str(prior), '--geometry-root',
            str(geometry_root), '--brackets-root', str(bracket_root),
            '--date', str(day), '--output', str(teacher_root)]
        if allocation is not None:
            teacher_args.extend(['--allocation-root', str(allocation)])
        teacher_day(teacher_args)
        complete.append(str(day))
        previous = day
        progress = {'version': VERSION, 'completed': complete.copy(),
            'queued': [str(next_day) for next_day in DATES[len(complete):]],
            'active': [], 'failed': []}
        temporary = output / 'campaign-state.json.tmp'
        temporary.write_text(json.dumps(progress, sort_keys=True),
                             encoding='utf-8')
        temporary.replace(output / 'campaign-state.json')
        print(json.dumps(progress, sort_keys=True), flush=True)
    return {'version': VERSION, 'completed': complete,
        'queued': [str(day) for day in DATES[len(complete):]],
        'active': [], 'failed': []}


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source-manifest', type=Path, required=True)
    parser.add_argument('--output-root', type=Path, required=True)
    parser.add_argument('--early-manifest', type=Path, required=True)
    parser.add_argument('--late-manifest', type=Path, required=True)
    parser.add_argument('--ledger', type=Path, required=True)
    parser.add_argument('--allocation-root', action='append', default=[],
                        metavar='YYYY-MM-DD=ABSOLUTE_RUNTIME_ROOT')
    parser.add_argument('--poll-seconds', type=int, default=120)
    parser.add_argument('--once', action='store_true')
    args = parser.parse_args(argv)
    runtime = Path(os.environ.get('QW_RUNTIME_ROOT', '')).resolve()
    output = args.output_root.resolve()
    inputs = [args.source_manifest.resolve(), args.early_manifest.resolve(),
              args.late_manifest.resolve(), args.ledger.resolve()]
    if (not runtime.is_dir() or not output.is_relative_to(runtime) or
            any(not item.is_relative_to(runtime) for item in inputs) or
            not 5 <= args.poll_seconds <= 3600):
        raise ValueError('Teacher campaign requires bounded runtime roots')
    allocation_roots: dict[date, Path] = {}
    for item in args.allocation_root:
        label, separator, path = item.partition('=')
        if not separator or not path:
            raise ValueError('Expected --allocation-root DATE=ROOT')
        day, root = date.fromisoformat(label), Path(path).resolve()
        if (day not in DATES or day in allocation_roots or
                not root.is_relative_to(runtime)):
            raise ValueError('Invalid external allocation root')
        allocation_roots[day] = root
    output.mkdir(parents=True, exist_ok=True)
    done: tuple[str, ...] = ()
    while True:
        progress = run_available(args.source_manifest, output,
            early=args.early_manifest, late=args.late_manifest,
            ledger=args.ledger, allocation_roots=allocation_roots, done=done)
        done = tuple(progress['completed'])
        if args.once or not progress['queued']:
            return 0
        time.sleep(args.poll_seconds)


if __name__ == '__main__':
    os.environ['PYTHONDONTWRITEBYTECODE'] = '1'
    raise SystemExit(main())
