"""Certify sparse bar-only stop/target extrema before teacher construction.

This stage deliberately does not infer tick size, fills, account P&L, or a
trainable action. It reads only pinned one-second bar extrema for first-
eligible long episodes and can be reused when the label price grid is fixed.
"""
from __future__ import annotations

import argparse
from datetime import date
from hashlib import sha256
import json
import os
from pathlib import Path

import polars as pl

from research.mlops.clickhouse import discover_clickhouse_env_files
from research.mlops.env import load_env_files
from research.rl_trading.v1 import arte_source
from research.rl_trading.v6.bracket_evidence import read_oracle_one_second_extrema
from research.rl_trading.v6.price_action_oracle import geometry
from research.rl_trading.v6.split import role


VERSION = 'rl-trading-price-action-geometry-v6-1'


def _hash(path: Path) -> str:
    digest = sha256()
    with path.open('rb') as stream:
        for chunk in iter(lambda: stream.read(8 * 1024 * 1024), b''):
            digest.update(chunk)
    return digest.hexdigest()


def source_positions(source: Path, day: date, *, allocation_root: Path | None = None
                     ) -> tuple[pl.DataFrame, dict, str]:
    """Hash-bind the compact first-eligible long-episode allocation table."""
    certificate = json.loads((source / 'complete.json').read_text())
    plan = json.loads((source / 'plan.json').read_text())
    if (certificate.get('status') != 'complete' or
            plan.get('day') != str(day) or
            plan.get('split_role') != role(day) or
            role(day) not in ('train', 'development')):
        raise ValueError('Geometry requires a certified train/development bank')
    if allocation_root is None:
        metadata = certificate['outputs'].get('intended_allocations')
        if metadata is None:
            raise ValueError('Certified day lacks embedded allocation sidecar')
        allocation = source / 'intended_allocations.parquet'
        allocation_hash = metadata['sha256']
    else:
        if allocation_root == source:
            raise ValueError('External allocation root must be distinct')
        sidecar = json.loads((allocation_root / 'complete.json').read_text())
        if (sidecar.get('version') != 'rl-trading-sparse-allocation-v6' or
                sidecar.get('status') != 'planning_only_not_fills' or
                sidecar.get('source_certificate_sha256') !=
                    _hash(source / 'complete.json') or
                sidecar.get('source_candidate_sha256') !=
                    certificate['outputs']['candidates']['sha256']):
            raise ValueError('Allocation sidecar is not bound to source day')
        allocation = allocation_root / 'intended_allocations.parquet'
        allocation_hash = sidecar['intended_allocations_sha256']
        metadata = {'rows': sidecar['rows'], 'sha256': allocation_hash}
    if (not allocation.is_file() or
            _hash(allocation) != allocation_hash):
        raise ValueError('Sparse allocation differs from source certificate')
    selected = pl.read_parquet(allocation).filter(pl.col('direction') == 1)
    if (selected.height > metadata['rows'] or
            selected['episode_uid'].n_unique() != selected.height or
            selected.select('ticker', 'time_us').n_unique() != selected.height):
        raise ValueError('Duplicate or malformed first-eligible long episode')
    positions = selected.select('ticker', 'episode_uid',
        pl.col('time_us').alias('entry_us'),
        pl.col('exit_hint_us').alias('exit_us'),
        pl.col('decision_close').alias('entry_price'))
    return positions, certificate, allocation_hash


def compile_geometry(positions: pl.DataFrame, bars: pl.DataFrame
                     ) -> pl.DataFrame:
    """Join observed bars to episodes without manufacturing missing seconds."""
    if positions.is_empty():
        raise ValueError('No long episode to certify')
    renamed = bars.rename({'boundary_us': 'time_us'})
    result = geometry(positions, renamed)
    if (result.height != positions.height or
            result['episode_uid'].n_unique() != positions.height or
            result.select('ticker', 'episode_uid').join(
                positions.select('ticker', 'episode_uid'),
                on=['ticker', 'episode_uid'], how='anti').height):
        raise ValueError('Oracle geometry lost or duplicated an episode')
    return result


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source-root', type=Path, required=True)
    parser.add_argument('--allocation-root', type=Path)
    parser.add_argument('--manifest', type=Path, required=True)
    parser.add_argument('--ledger', type=Path, required=True)
    parser.add_argument('--date', type=date.fromisoformat, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args(argv)
    runtime = Path(os.environ.get('QW_RUNTIME_ROOT', '')).resolve()
    source, output = args.source_root.resolve(), args.output.resolve()
    allocation_root = (args.allocation_root.resolve()
                       if args.allocation_root is not None else None)
    if (not runtime.is_dir() or source == output or
            any(not path.is_relative_to(runtime) for path in
                (source, output, *((allocation_root,) if allocation_root else ()))) or
            allocation_root == output):
        raise ValueError('Geometry artifacts need distinct runtime roots')
    positions, certificate, allocation_hash = source_positions(
        source, args.date, allocation_root=allocation_root)
    source_hash = _hash(source / 'complete.json')
    if output.exists() and any(output.iterdir()):
        complete = output / 'complete.json'
        if complete.is_file():
            saved = json.loads(complete.read_text())
            if (saved.get('version') == VERSION and
                    saved.get('source_certificate_sha256') == source_hash and
                    saved.get('allocation_sha256') == allocation_hash and
                    _hash(output / 'geometry.parquet') ==
                    saved.get('geometry_sha256')):
                print(json.dumps(saved, sort_keys=True), flush=True)
                return 0
        raise ValueError('Uncertified price-action geometry output exists')
    load_env_files(discover_clickhouse_env_files(), verbose=False)
    build = arte_source.load_build(args.manifest, args.ledger, [args.date],
                                   sorted(set(positions['ticker'])))
    reader = arte_source.reader(threads=2)
    try:
        bars = read_oracle_one_second_extrema(reader, build, args.date,
                                               positions)
    finally:
        reader.close()
    result = compile_geometry(positions, bars)
    output.mkdir(parents=True, exist_ok=True)
    path = output / 'geometry.parquet'
    result.write_parquet(path)
    report = {'version': VERSION, 'status': 'geometry_only_not_supervision',
              'day': str(args.date), 'source_certificate_sha256': source_hash,
              'source_build_id': build['build_id'],
              'allocation_sha256': allocation_hash,
              'geometry_sha256': _hash(path), 'episodes': positions.height,
              'observed_extrema_rows': bars.height,
              'held_clock_complete': int(result['clock_complete'].sum()),
              'unobserved_held_seconds': int(
                  result['unobserved_held_seconds'].sum()),
              'label_evidence': 'certified_1s_candles_only',
              'execution_evidence': 'none'}
    temporary = output / 'complete.json.tmp'
    temporary.write_text(json.dumps(report, sort_keys=True), encoding='utf-8')
    temporary.replace(output / 'complete.json')
    print(json.dumps(report, sort_keys=True), flush=True)
    return 0


if __name__ == '__main__':
    os.environ['PYTHONDONTWRITEBYTECODE'] = '1'
    raise SystemExit(main())
