"""Prepare canonical metadata parents; no producer, grants or DB writes."""
import os
import sys
os.environ['PYTHONDONTWRITEBYTECODE']='1'
sys.dont_write_bytecode=True
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import argparse
import json
from contextlib import closing
from datetime import date


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--archive-root',type=Path,required=True)
    parser.add_argument('--development-scope',type=Path,required=True)
    parser.add_argument('--development-scope-sha256',required=True)
    parser.add_argument('--exclusions',type=Path,required=True)
    parser.add_argument('--publish-metadata',action='store_true',
                        help='Save immutable runtime metadata only; default performs SELECT planning')
    parser.add_argument('--source-reader-plan',action='store_true',
                        help='Derive exact private SELECT contract before canonical metadata reads')
    args=parser.parse_args(argv)
    try:
        if args.source_reader_plan and args.publish_metadata:
            raise ValueError('Source-reader plan cannot publish metadata')
        from src.runtime_paths import runtime_root, WORKSTATION_RUNTIME_ROOT
        repo=Path(__file__).resolve().parents[1]
        root=args.archive_root.resolve()
        if (root.is_relative_to(repo) or not any(p.is_dir() and root.is_relative_to(p.resolve())
                for p in (runtime_root(),WORKSTATION_RUNTIME_ROOT))):
            raise ValueError('Operational archive root required')
        from research.level_book.v7 import canonical_scoped_campaign as scoped
        from research.level_book.v7.canonical_metadata_parent import prepare_parent,publish_parent,source_interval,source_read_plan
        from research.level_book.v7.canonical_source_reader import SourceReadPlan,source_client,POLICY_KEY
        from src.backend.backtest_market_data import readonly_clickhouse_client
        scope=scoped.read_scope(args.development_scope,args.development_scope_sha256,args.exclusions)
        with closing(readonly_clickhouse_client(v3_read_principal=True)) as reader:
            scopes=scoped.certified_scopes(reader,scope)
        inventory=scoped.filtered.make_plan(root)
        start,end=source_interval(root,scopes,inventory)
        contract=source_read_plan(root,scopes,inventory,os.environ.get(POLICY_KEY,''))
        if args.source_reader_plan:
            print(json.dumps(contract.payload(),sort_keys=True))
            print('Requested grants only: actual private principal/storage readiness unverified. No canonical metadata read.')
            return 0
        with closing(source_client(contract)) as reader:
            parent=prepare_parent(root,scope,scopes,reader,inventory=inventory)
        print(f"Verified {len(parent['rows'])} missing parents | canonical prefix {start} through {end}")
        print('Metadata parent '+parent['plan_hash'])
        if args.publish_metadata:
            print('Saved '+str(publish_parent(root,parent)))
        else:
            print('Plan only: no runtime metadata saved.')
        print('No books generated, producer launched or financial readiness certified.')
        return 0
    except Exception as exc:
        print('Blocked: '+type(exc).__name__+'. Check scope, canonical source profile and exact prior coverage.',file=sys.stderr)
        return 1


if __name__=='__main__':
    raise SystemExit(main())
