"""Build one immutable opening-as-of split sidecar using SELECT-only authority.

No feature arrays, labels, or market products are written. Validation sessions
cannot be certified through this command until a frozen-winner receipt exists.
"""
import os
os.environ['PYTHONDONTWRITEBYTECODE'] = '1'
import argparse
from datetime import date
from pathlib import Path
import json
from .runtime import require_runtime, write_json, file_hash
from .splits import VERSION, price_factor


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--sessions', type=Path, required=True)
    parser.add_argument('--date', type=date.fromisoformat, required=True)
    parser.add_argument('--frozen-winner', type=Path)
    args = parser.parse_args(argv)
    spec = json.loads(args.sessions.read_text(encoding='utf-8'))
    matching = [(role, item) for role in ('training', 'validation') for item in spec[role] if item['day'] == str(args.date)]
    if len(matching) != 1:
        raise ValueError('Day absent or ambiguous in split plan')
    role, item = matching[0]
    if role == 'validation':
        if not args.frozen_winner or not args.frozen_winner.is_file():
            raise ValueError('Final validation remains sealed until winner freeze')
        freeze = json.loads(args.frozen_winner.read_text(encoding='utf-8'))
        if not freeze.get('winner'):
            raise ValueError('Missing frozen winner')
    output = require_runtime(Path(item['split_certificate']).parent) / Path(item['split_certificate']).name
    from research.mlops.clickhouse import discover_clickhouse_env_files
    from research.mlops.env import load_env_files
    from research.rl_trading.v1 import arte_source, reference_features
    from research.rl_trading.v1.common import digest, exclusive
    from research.rl_trading.v1.arte_sql import literal, query
    load_env_files(discover_clickhouse_env_files(), verbose=False)
    root = Path(item['feature_root'])
    plan = json.loads((root / 'plan.json').read_text(encoding='utf-8'))
    mapping = json.loads(Path(item['identity_map']).read_text(encoding='utf-8'))
    bank_hash = file_hash(root / 'complete.json')
    if plan['day'] != str(args.date) or mapping['bank_certificate_sha256'] != bank_hash:
        raise ValueError('Split producer bank/day identity mismatch')
    previous_root = Path(item['previous_feature_root']) if item.get('previous_feature_root') else None
    previous_plan = json.loads((previous_root / 'plan.json').read_text(encoding='utf-8')) if previous_root else None
    previous_hash = file_hash(previous_root / 'complete.json') if previous_root else None
    current = arte_source.load_build(Path(item['source_manifest']), Path(item['source_ledger']), [args.date])
    with exclusive(output.with_suffix('.producer')):
        if output.exists():
            # Do not rewrite an immutable certificate or query a newer snapshot.
            existing = json.loads(output.read_text(encoding='utf-8'))
            if existing['hash'] != digest({k: v for k, v in existing.items() if k != 'hash'}) or existing['bank_certificate_sha256'] != bank_hash or existing['previous_bank_certificate_sha256'] != previous_hash:
                raise ValueError('Existing split certificate identity changed')
            return 0
        client = arte_source.reader(threads=1)
        try:
            storage = reference_features.storage_check(client)
            population, proof = arte_source.population(client, current, args.date)
            listings = {row['listing_id']: row for row in population}
            cutoff = "toDateTime64(" + literal(reference_features.opening(args.date)) + ",9,'UTC')"
            # Reference actions are small metadata; one bounded-column query
            # avoids reloading every V7 seed just to certify split evidence.
            actions = query(client,
                'SELECT symbol_id,listing_id,security_id,execution_date,split_from,split_to,inserted_at,source_content_sha256 '
                'FROM q_live.market_stock_split_v1 FINAL '
                f'WHERE execution_date<=toDate({literal(args.date)}) AND inserted_at<={cutoff} '
                'ORDER BY listing_id,execution_date,inserted_at')
            by_listing = {}
            for action in actions:
                key = tuple(action[name] for name in ('symbol_id','listing_id','security_id'))
                by_listing.setdefault(key, []).append(action)
            records = {}
            for identity, ticker in sorted(mapping['listing_to_ticker'].items()):
                listing = listings.get(identity)
                if not listing or listing['ticker'] != ticker:
                    raise ValueError('Corporate-action listing identity mismatch')
                key = tuple(listing[name] for name in ('symbol_id','listing_id','security_id'))
                rows = by_listing.get(key, [])
                records[identity] = dict(ticker=ticker, splits=rows,
                    history_price_factor=price_factor(rows, previous_plan['day'], plan['day']) if previous_plan else 1.,
                    rvol_price_factor=price_factor(rows, plan['previous_day'], plan['day']) if plan.get('previous_day') else 1.)
        finally:
            client.close()
        value = dict(version=VERSION, status='complete', day=str(args.date),
            opening_asof_utc=reference_features.opening(args.date),
            authority='q_live.market_stock_split_v1; execution_date<=day AND inserted_at<=opening',
            bank_certificate_sha256=bank_hash, previous_bank_certificate_sha256=previous_hash,
            population_snapshot_hash=proof['snapshot_hash'], storage=storage, listings=records,
            source_manifest_sha256=file_hash(Path(item['source_manifest'])),
            source_ledger=str(item['source_ledger']), market_data_written=False)
        # Normalize DB date/time values before digest and persistence.
        value = json.loads(json.dumps(value, default=str))
        value['hash'] = digest(value)
        write_json(output, value)
        print(json.dumps(dict(status='complete', day=str(args.date), listings=len(records), output=str(output))))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
