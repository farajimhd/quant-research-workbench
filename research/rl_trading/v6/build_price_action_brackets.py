"""Certify bar-precision V6 stop/target labels without reading quotes.

ARTE bars store high_int and low_int in ten-thousandths of a dollar. One
integer unit is the label-side offset and rounding grid; it is explicitly
not represented as a historical exchange tick or an executable OMS price.
No redundant per-ticker price-grid file is materialized.
"""
from __future__ import annotations

import argparse
from hashlib import sha256
import json
import os
from pathlib import Path

import polars as pl

from research.rl_trading.v6.build_price_action_geometry import VERSION as GEOMETRY_VERSION
from research.rl_trading.v6.price_action_oracle import (
    VERSION as ORACLE_VERSION, round_geometry)


VERSION = 'rl-trading-price-action-brackets-v6-2'
BAR_PRICE_UNIT = 0.0001


def _hash(path: Path) -> str:
    digest = sha256()
    with path.open('rb') as stream:
        for chunk in iter(lambda: stream.read(8 * 1024 * 1024), b''):
            digest.update(chunk)
    return digest.hexdigest()


def compile_brackets(geometry_root: Path) -> tuple[pl.DataFrame, dict]:
    """Bind immutable observed bars; label levels at their stored precision."""
    certificate = json.loads((geometry_root / 'complete.json').read_text())
    if (certificate.get('version') != GEOMETRY_VERSION or
            certificate.get('status') != 'geometry_only_not_supervision' or
            certificate.get('execution_evidence') != 'none' or
            certificate.get('label_evidence') != 'certified_1s_candles_only'):
        raise ValueError('Uncertified or quote-derived bracket input')
    path = geometry_root / 'geometry.parquet'
    if _hash(path) != certificate.get('geometry_sha256'):
        raise ValueError('Price-action geometry hash changed')
    geometry = pl.read_parquet(path)
    if geometry.height != certificate.get('episodes'):
        raise ValueError('Price-action geometry row count changed')
    grid = pl.DataFrame({'ticker': geometry['ticker'].unique(),
                         'tick_size': pl.Series([BAR_PRICE_UNIT] *
                                                geometry['ticker'].n_unique())})
    result = round_geometry(geometry, grid)
    if result.height != geometry.height:
        raise ValueError('Bracket rounding changed episode count')
    return result, certificate


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--geometry-root', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args(argv)
    runtime = Path(os.environ.get('QW_RUNTIME_ROOT', '')).resolve()
    geometry_root, output = args.geometry_root.resolve(), args.output.resolve()
    if (not runtime.is_dir() or geometry_root == output or
            any(not path.is_relative_to(runtime) for path in
                (geometry_root, output))):
        raise ValueError('Bracket input and output need distinct runtime roots')
    rounded, geometry_cert = compile_brackets(geometry_root)
    geometry_hash = _hash(geometry_root / 'complete.json')
    if output.exists() and any(output.iterdir()):
        complete = output / 'complete.json'
        if complete.is_file():
            saved = json.loads(complete.read_text())
            if (saved.get('version') == VERSION and
                    saved.get('geometry_certificate_sha256') == geometry_hash and
                    saved.get('brackets_sha256') ==
                    _hash(output / 'oracle_brackets.parquet')):
                print(json.dumps(saved, sort_keys=True), flush=True)
                return 0
        raise ValueError('Uncertified bracket output root exists')
    output.mkdir(parents=True, exist_ok=True)
    path = output / 'oracle_brackets.parquet'
    rounded.write_parquet(path)
    report = {'version': VERSION, 'oracle_version': ORACLE_VERSION,
              'status': 'labels_only_not_teacher',
              'day': geometry_cert['day'],
              'geometry_certificate_sha256': geometry_hash,
              'price_grid_source_type': 'canonical_bar_precision',
              'price_unit': BAR_PRICE_UNIT, 'offset_units': 1,
              'executable_market_tick_claim': False,
              'brackets_sha256': _hash(path), 'episodes': rounded.height,
              'label_available': int(rounded['label_available'].sum()),
              'held_clock_complete': int(rounded['clock_complete'].sum()),
              'execution_evidence': 'none'}
    temporary = output / 'complete.json.tmp'
    temporary.write_text(json.dumps(report, sort_keys=True), encoding='utf-8')
    temporary.replace(output / 'complete.json')
    print(json.dumps(report, sort_keys=True), flush=True)
    return 0


if __name__ == '__main__':
    os.environ['PYTHONDONTWRITEBYTECODE'] = '1'
    raise SystemExit(main())
