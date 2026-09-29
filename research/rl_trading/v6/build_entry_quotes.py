"""Certify minimal ARTE quote evidence for sparse long entry/exit intentions."""
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
from research.rl_trading.v1 import arte_source, bracket_source
from research.rl_trading.v6.entry_source import (arrival_quotes,
                                                  with_decision_fallback)
from research.rl_trading.v6.split import role


def _hash(path: Path) -> str:
    digest = sha256()
    with path.open('rb') as stream:
        for chunk in iter(lambda: stream.read(8 * 1024 * 1024), b''):
            digest.update(chunk)
    return digest.hexdigest()


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source-root', type=Path, required=True)
    parser.add_argument('--allocation-root', type=Path, required=True)
    parser.add_argument('--manifest', type=Path, required=True)
    parser.add_argument('--ledger', type=Path, required=True)
    parser.add_argument('--date', type=date.fromisoformat, required=True)
    parser.add_argument('--clock', choices=('entry', 'exit'), default='entry')
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args(argv)
    if role(args.date) not in ('train', 'development'):
        raise ValueError('Entry quote labels exclude context-only and sealed test')
    runtime = Path(os.environ.get('QW_RUNTIME_ROOT', '')).resolve()
    source, allocation, output = (path.resolve() for path in
                                   (args.source_root, args.allocation_root,
                                    args.output))
    if (not runtime.is_dir() or any(not path.is_relative_to(runtime)
                                    for path in (source, allocation, output)) or
            len({source, allocation, output}) != 3):
        raise ValueError('Quote artifacts must use distinct required runtime roots')
    source_cert = source / 'complete.json'
    source_plan = json.loads((source / 'plan.json').read_text())
    allocation_cert = json.loads((allocation / 'complete.json').read_text())
    if (source_plan['day'] != str(args.date) or
            source_plan['split_role'] != role(args.date) or
            allocation_cert['source_certificate_sha256'] != _hash(source_cert)):
        raise ValueError('Entry quotes do not bind to source candidate day')
    allocation_file = allocation / 'intended_allocations.parquet'
    if _hash(allocation_file) != allocation_cert['intended_allocations_sha256']:
        raise ValueError('Sparse allocation hash changed')
    proposals = pl.read_parquet(allocation_file).filter(pl.col('direction') == 1)
    if proposals.is_empty():
        raise ValueError('No qualified long entry proposals to bind')
    if args.clock == 'exit':
        if 'exit_hint_us' not in proposals.columns:
            raise ValueError('Missing hindsight exit decision clock')
        proposals = proposals.with_columns(pl.col('exit_hint_us').alias('time_us'))
    filename = f'{args.clock}_quotes.parquet'
    if output.exists() and any(output.iterdir()):
        cert = output / 'complete.json'
        if cert.exists():
            saved = json.loads(cert.read_text())
            if (saved['source_certificate_sha256'] == _hash(source_cert) and
                    saved['allocation_certificate_sha256'] ==
                        _hash(allocation / 'complete.json') and
                    saved.get('clock') == args.clock and
                    _hash(output / filename) == saved['quotes_sha256']):
                print(json.dumps(saved, sort_keys=True), flush=True)
                return 0
        raise ValueError('Uncertified quote output root exists')
    load_env_files(discover_clickhouse_env_files(), verbose=False)
    source_build = arte_source.load_build(args.manifest, args.ledger,
                                          [args.date],
                                          sorted(set(proposals['ticker'])))
    reader = arte_source.reader(threads=2)
    try:
        evidence = arrival_quotes(reader, source_build, args.ledger,
                                  args.date, proposals)
        missing = (evidence.filter(~pl.col('quote_available'))
                   .select('ticker', pl.col('time_us').alias('entry_us'))
                   .unique())
        if missing.height:
            missing = missing.with_columns(
                (pl.col('ticker') + ':' +
                 pl.col('entry_us').cast(pl.String)).alias('episode_uid'))
            prior = bracket_source.entry_quotes(
                reader, source_build, args.ledger, args.date, missing)
            evidence = with_decision_fallback(evidence, prior)
    finally:
        reader.close()
    if evidence.height != proposals.height or (
            evidence.select('episode_uid').n_unique() != evidence.height):
        raise ValueError('Quote read dropped or duplicated long proposals')
    output.mkdir(parents=True, exist_ok=True)
    path = output / filename
    evidence.write_parquet(path)
    report = {'version': 'rl-trading-sparse-decision-quotes-v6',
              'clock': args.clock,
              'day': str(args.date), 'rows': evidence.height,
              'fresh_quotes': int(evidence['quote_available'].sum()),
              'arrival_bucket_quotes': int((evidence['quote_source'] ==
                                            'arrival_bucket').sum()),
              'carried_decision_quotes': int((evidence['quote_source'] ==
                                             'carried_decision_quote').sum()),
              'missing_or_stale_quotes': int((~evidence['quote_available']).sum()),
              'source_certificate_sha256': _hash(source_cert),
              'allocation_certificate_sha256': _hash(allocation / 'complete.json'),
              'quotes_sha256': _hash(path),
              'scope': 'optimistic_quote_bound_fill_input_not_broker_fills'}
    temporary = output / 'complete.json.tmp'
    temporary.write_text(json.dumps(report, sort_keys=True), encoding='utf-8')
    temporary.replace(output / 'complete.json')
    print(json.dumps(report, sort_keys=True), flush=True)
    return 0


if __name__ == '__main__':
    os.environ['PYTHONDWRITEBYTECODE'] = '1'
    raise SystemExit(main())
