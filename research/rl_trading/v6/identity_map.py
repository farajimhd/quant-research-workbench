"""Tiny hash-bound listing-to-ticker sidecar for packed V6 candle banks."""
from __future__ import annotations

from hashlib import sha256
import json
from pathlib import Path

import polars as pl

from research.rl_trading.v6.session_data import PackedSession


VERSION = 'rl-trading-v6-listing-ticker-map-v1'


def _hash(path: Path) -> str:
    digest = sha256()
    with path.open('rb') as stream:
        for chunk in iter(lambda: stream.read(8 * 1024 * 1024), b''):
            digest.update(chunk)
    return digest.hexdigest()


def certify_identity_map(session: PackedSession, population: list[dict],
                         population_snapshot_hash: str, output: Path, *,
                         runtime_root: Path) -> dict:
    """Persist only listing ID and ticker from the pinned source population."""
    runtime, output = Path(runtime_root).resolve(), Path(output).resolve()
    if (not runtime.is_dir() or not output.is_relative_to(runtime) or
            output == session.root or output.is_relative_to(session.root)):
        raise ValueError('Identity sidecar must have a separate runtime root')
    plan = json.loads((session.root / 'plan.json').read_text())
    if plan['population_snapshot_hash'] != population_snapshot_hash:
        raise ValueError('Pinned population snapshot differs from V6 bank')
    if (any(not row.get('listing_id') or not row.get('ticker') for row in
            population) or len({row['listing_id'] for row in population}) !=
            len(population) or len({row['ticker'] for row in population}) !=
            len(population)):
        raise ValueError('Population ticker and listing mapping is ambiguous')
    mapped = {row['listing_id']: row['ticker'] for row in population}
    if set(mapped) != set(session.listings):
        raise ValueError('Population does not equal the packed listing census')
    frame = pl.DataFrame({'listing_id': list(session.listings),
                          'ticker': [mapped[key] for key in session.listings]})
    certificate = output / 'complete.json'
    if certificate.exists():
        previous = json.loads(certificate.read_text())
        if (previous.get('bank_certificate_sha256') !=
            session.source_certificate_sha256 or
            previous.get('population_snapshot_hash') !=
            population_snapshot_hash or
            _hash(output / 'listing_ticker.parquet') !=
            previous.get('mapping_sha256')):
            raise ValueError('Existing identity sidecar differs from bank')
        return previous
    if output.exists() and any(output.iterdir()):
        raise ValueError('Uncertified identity sidecar exists')
    output.mkdir(parents=True, exist_ok=True)
    path = output / 'listing_ticker.parquet'
    frame.write_parquet(path)
    result = {'version': VERSION, 'day': str(session.day),
              'status': 'complete',
              'bank_certificate_sha256': session.source_certificate_sha256,
              'population_snapshot_hash': population_snapshot_hash,
              'listings': len(session.listings),
              'mapping_sha256': _hash(path)}
    temporary = output / 'complete.json.tmp'
    temporary.write_text(json.dumps(result, sort_keys=True), encoding='utf-8')
    temporary.replace(certificate)
    return result


def open_identity_map(root: Path, session: PackedSession, *,
                      runtime_root: Path) -> tuple[str, ...]:
    """Return ticker axis aligned exactly to the certified listing axis."""
    runtime, root = Path(runtime_root).resolve(), Path(root).resolve()
    if not runtime.is_dir() or not root.is_relative_to(runtime):
        raise ValueError('Identity map must be under the runtime root')
    cert = json.loads((root / 'complete.json').read_text())
    plan = json.loads((session.root / 'plan.json').read_text())
    path = root / 'listing_ticker.parquet'
    if (cert.get('status') != 'complete' or cert.get('version') != VERSION or
            cert.get('day') != str(session.day) or
            cert.get('bank_certificate_sha256') !=
            session.source_certificate_sha256 or
            cert.get('population_snapshot_hash') !=
            plan['population_snapshot_hash'] or
            _hash(path) != cert.get('mapping_sha256')):
        raise ValueError('Identity map authority or file hash changed')
    frame = pl.read_parquet(path)
    if (frame.columns != ['listing_id', 'ticker'] or
            frame['listing_id'].to_list() != list(session.listings) or
            frame['ticker'].n_unique() != frame.height or
            frame.height != cert['listings']):
        raise ValueError('Identity map order or uniqueness changed')
    return tuple(frame['ticker'].to_list())
