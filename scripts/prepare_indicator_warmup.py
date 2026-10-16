"""Prepare bounded December indicator inputs or report exact missing authorities.

This command does not publish indicators. It verifies certified bar and corporate
dependencies before preparing the finite warmup input for the shared producer.
"""
import os
os.environ['PYTHONDONTWRITEBYTECODE'] = '1'
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import argparse
from contextlib import closing
from datetime import date
import json
from types import SimpleNamespace
from uuid import UUID, uuid4

from scripts import build_market_day as builder
from pipelines.certified_research import indicator_warmup, contracts
from pipelines.certified_research.publication import KeeperClaim, Scope
from pipelines.certified_research.reference_publication import ReferencePublisher, ReferenceScope
from pipelines.market_sip.flatfiles.download_update_events import load_env_files
from src.trading_runtime.keeper_session import open_workstation_keeper_session


def prepare(query, database, dataset, listing, claim_factory):
    contracts.identifier(database)
    UUID(dataset)
    if type(listing) is not int or not 0 < listing < 2**64:
        raise ValueError('Listing key must be a positive UInt64')
    where = f'dataset_id=toUUID({contracts.literal(dataset)}) AND listing_key={listing}'
    members = query(f'SELECT DISTINCT security_key FROM {database}.research_membership_v1 '
                    f'WHERE {where} AND eligible=1 LIMIT 2')
    blocks = []
    if len(members) != 1:
        blocks.append('unambiguous_certified_listing_membership_missing')
    rows = query(f'SELECT * FROM {database}.research_unit_receipt_v1 WHERE {where} '
                 "AND product='bars' AND session_date BETWEEN toDate('2025-12-01') AND toDate('2025-12-31') "
                 'ORDER BY session_date DESC LIMIT 24')
    if not rows:
        blocks.append('certified_december_bar_receipts_missing')
    corporate = []
    if len(members) == 1:
        corporate = query(f'SELECT * FROM {database}.research_reference_receipt_v1 WHERE '
                          f'dataset_id=toUUID({contracts.literal(dataset)}) AND entity_kind=2 '
                          f'AND entity_key={int(members[0]["security_key"])} AND family=\'corporate_actions\' '
                          "AND range_start<=toDate('2025-12-31') AND range_end>=toDate('2026-02-27') LIMIT 2")
        if len(corporate) != 1:
            blocks.append('unambiguous_certified_corporate_adjustment_coverage_missing')
    result = dict(status='blocked' if blocks else 'checking', execution_started=False,
                  dataset_id=dataset, listing_key=listing, requirements=indicator_warmup.requirements(),
                  blocking_dependencies=blocks, december_bar_receipts=len(rows))
    if blocks:
        return result
    # No writer is disturbed: an already-owned dataset causes normal Keeper
    # acquisition failure. A successful preparation holds the ordinary claim.
    with closing(claim_factory()) as claim:
        reference = corporate[0]
        scope = ReferenceScope(dataset, 2, int(reference['entity_key']), 'corporate_actions',
                               date.fromisoformat(str(reference['range_start'])),
                               date.fromisoformat(str(reference['range_end'])),
                               reference['parent_hash'], reference['catalog_hash'])
        if ReferencePublisher(query, database, claim).receipt(scope) != reference:
            raise ValueError('Corporate adjustment coverage differs from certified authority')
        parents = [(Scope(dataset, date.fromisoformat(str(row['session_date'])), listing,
                          'bars', row['parent_hash']), row) for row in rows]
        result['warmup'] = indicator_warmup.load(query, database, claim, parents)
        earliest = min(date.fromisoformat(str(row['session_date']))
                       for rows_ in result['warmup']['selected'].values() for row in rows_)
        if scope.start > earliest:
            raise ValueError('Certified corporate coverage omits the selected December warmup')
        result['corporate_receipt'] = reference
    result['status'] = 'prepared'
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--env-file', type=Path, required=True)
    parser.add_argument('--runtime-root', type=Path, required=True)
    parser.add_argument('--database', default='arte')
    parser.add_argument('--dataset-id', required=True)
    parser.add_argument('--listing-key', type=int, required=True)
    args = parser.parse_args(argv)
    if not args.runtime_root.is_dir():
        raise ValueError('Required runtime root is unavailable')
    load_env_files([args.env_file], verbose=False)
    options = SimpleNamespace(env_file=args.env_file, max_threads=1, max_memory_gb=1, query_timeout=45)
    with closing(builder.Client(options, persistent=True)) as client:
        # Do not open Keeper at all when basic retained authorities are absent.
        sessions = []

        def claim_factory():
            session = open_workstation_keeper_session()
            sessions.append(session)
            return KeeperClaim(session.client, args.dataset_id)

        try:
            result = prepare(client.query, args.database, args.dataset_id, args.listing_key, claim_factory)
        finally:
            for session in sessions:
                session.close()
    path = args.runtime_root / ('indicator-warmup-' + uuid4().hex[:12] + '.json')
    path.write_text(json.dumps(result, indent=2, default=str), encoding='utf-8')
    print(f'Indicator warmup {result["status"]} | listing {args.listing_key} | '
          f'{len(result["blocking_dependencies"])} missing dependencies | {path}', flush=True)
    return 0 if result['status'] == 'prepared' else 2


if __name__ == '__main__':
    raise SystemExit(main())
