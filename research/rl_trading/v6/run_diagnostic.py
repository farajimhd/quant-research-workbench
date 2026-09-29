"""Hash-bound sparse quote entry/exit diagnostic; not training supervision."""
from __future__ import annotations

import argparse
from datetime import date
from hashlib import sha256
import json
import os
from pathlib import Path

import polars as pl

from research.rl_trading.v6.diagnostic_teacher import diagnose
from research.rl_trading.v6.split import role


def _hash(path: Path) -> str:
    digest = sha256()
    with path.open('rb') as stream:
        for block in iter(lambda: stream.read(8*1024*1024), b''):
            digest.update(block)
    return digest.hexdigest()


def _bound(root: Path, filename: str,
           fields: tuple[str, ...]) -> tuple[pl.DataFrame, str]:
    certificate = root / 'complete.json'
    data = root / filename
    saved = json.loads(certificate.read_text())
    expected = next((saved[name] for name in fields if name in saved), None)
    if expected is None or _hash(data) != expected:
        raise ValueError(f'Invalid certified sparse diagnostic input: {root}')
    return pl.read_parquet(data), _hash(certificate)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--date', type=date.fromisoformat, required=True)
    parser.add_argument('--allocation-root', type=Path, required=True)
    parser.add_argument('--entry-root', type=Path, required=True)
    parser.add_argument('--exit-root', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args(argv)
    if role(args.date) not in ('train', 'development'):
        raise ValueError('Diagnostic excludes context-only and sealed test')
    runtime = Path(os.environ.get('QW_RUNTIME_ROOT', '')).resolve()
    roots = [path.resolve() for path in (args.allocation_root, args.entry_root,
                                         args.exit_root, args.output)]
    if (not runtime.is_dir() or len(set(roots)) != 4 or
            any(not path.is_relative_to(runtime) for path in roots)):
        raise ValueError('Diagnostic inputs and output need distinct runtime roots')
    allocation, allocation_hash = _bound(
        roots[0], 'intended_allocations.parquet',
        ('intended_allocations_sha256',))
    entry, entry_hash = _bound(roots[1], 'entry_quotes.parquet',
                               ('quotes_sha256', 'entry_quotes_sha256'))
    exits, exit_hash = _bound(roots[2], 'exit_quotes.parquet',
                              ('quotes_sha256',))
    if (json.loads((roots[1] / 'complete.json').read_text())['day'] != str(args.date) or
            json.loads((roots[2] / 'complete.json').read_text())['day'] != str(args.date)):
        raise ValueError('Quote evidence belongs to another session')
    root = roots[3]
    sources = {'allocation': allocation_hash, 'entry': entry_hash,
               'exit': exit_hash}
    if root.exists() and any(root.iterdir()):
        cert = root / 'complete.json'
        if cert.exists():
            previous = json.loads(cert.read_text())
            if previous['source_certificates_sha256'] == sources and all(
                _hash(root / name) == value for name, value in
                previous['outputs_sha256'].items()):
                print(json.dumps(previous, sort_keys=True), flush=True)
                return 0
        raise ValueError('Uncertified diagnostic output exists')
    orders, positions, report = diagnose(allocation, entry, exits)
    root.mkdir(parents=True, exist_ok=True)
    files = {}
    for label, frame in (('orders', orders), ('closed_positions', positions)):
        if frame.height:
            path = root / f'{label}.parquet'
            frame.write_parquet(path)
            files[path.name] = _hash(path)
    cert = {'version': report['version'], 'day': str(args.date),
            'source_certificates_sha256': sources,
            'outputs_sha256': files, 'report': report,
            'status': 'diagnostic_only_not_teacher_certification'}
    temporary = root / 'complete.json.tmp'
    temporary.write_text(json.dumps(cert, sort_keys=True), encoding='utf-8')
    temporary.replace(root / 'complete.json')
    print(json.dumps(cert, sort_keys=True), flush=True)
    return 0


if __name__ == '__main__':
    os.environ['PYTHONDONTWRITEBYTECODE'] = '1'
    raise SystemExit(main())
