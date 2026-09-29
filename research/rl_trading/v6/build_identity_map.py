"""Project only listing ID and ticker from a certified source population."""
from __future__ import annotations

import argparse
from datetime import date
import json
import os
from pathlib import Path

from research.rl_trading.v1 import arte_source
from research.rl_trading.v6.identity_map import certify_identity_map
from research.rl_trading.v6.session_data import open_session
from research.mlops.clickhouse import discover_clickhouse_env_files
from research.mlops.env import load_env_files
from src.runtime_paths import runtime_root


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--manifest', type=Path, required=True)
    parser.add_argument('--ledger', type=Path, required=True)
    parser.add_argument('--date', type=date.fromisoformat, required=True)
    parser.add_argument('--session-root', type=Path, required=True)
    parser.add_argument('--previous-root', type=Path)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--ticker', action='append', default=[])
    args = parser.parse_args(argv)
    runtime = runtime_root().resolve()
    session = open_session(args.session_root, runtime_root=runtime,
                           previous_root=args.previous_root)
    if session.day != args.date:
        raise ValueError('Identity projection day differs from packed bank')
    load_env_files(discover_clickhouse_env_files(), verbose=False)
    source = arte_source.load_build(args.manifest, args.ledger, [args.date],
                                    sorted(set(args.ticker)) or None)
    plan = json.loads((session.root / 'plan.json').read_text())
    if plan['source_build_id'] != source['build_id']:
        raise ValueError('Identity projection uses another source build')
    reader = arte_source.reader(threads=1)
    try:
        arte_source.storage_check(reader)
        population, proof = arte_source.population(reader, source, args.date)
    finally:
        reader.close()
    certificate = certify_identity_map(session, population,
        proof['snapshot_hash'], args.output, runtime_root=runtime)
    print(json.dumps(certificate, sort_keys=True), flush=True)
    return 0


if __name__ == '__main__':
    os.environ['PYTHONDONTWRITEBYTECODE'] = '1'
    raise SystemExit(main())
