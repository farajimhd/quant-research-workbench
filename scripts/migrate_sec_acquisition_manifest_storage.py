#!/usr/bin/env python3
"""Plan or execute an exact SEC manifest SSD migration during maintenance."""
import argparse
import hashlib
import os
from pathlib import Path
import re
import sys

os.environ['PYTHONDONTWRITEBYTECODE']='1';sys.dont_write_bytecode=True
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from scripts import build_market_day as builder
from scripts.migrate_structure_checkpoint_storage import migrate

TABLES=('sec_filing_entity_archive_manifest_v3','sec_filing_text_rendered_rebuild_manifest_v3',
        'sec_filing_text_rendered_rebuild_bundle_manifest_v3')


class Adapter:
    def __init__(self,client,guard=None):self.client=client;self.guard=guard
    def rows(self,sql):return self.client.query(sql)
    def execute(self,sql):
        if self.guard:self.guard()
        self.client.query(sql,read=False)
    def digest(self,sql):
        hashed=hashlib.sha256();count=0
        for row in self.client.http.iter_json_each_row(sql+' FORMAT JSONEachRow'):
            value=row['h']
            if not isinstance(value,str) or not re.fullmatch('[0-9A-F]{64}',value):
                raise RuntimeError('Invalid complete-row hash stream')
            hashed.update((value+'\n').encode());count+=1
        return dict(rows=count,sha256=hashed.hexdigest())


def parse_args(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--env-file',type=Path,default=ROOT/'.env')
    parser.add_argument('--runtime-dir',type=Path,default=Path('D:/TradingML/runtimes/sec-manifest-storage-migration-v1'))
    parser.add_argument('--reserve-gib',type=int,default=256)
    parser.add_argument('--execute',action='store_true')
    parser.add_argument('--writers-stopped',action='store_true',help='Declare the required SEC writer maintenance window; active queries/mutations are also checked')
    args=parser.parse_args(argv)
    if args.reserve_gib<0:parser.error('Reserve must be nonnegative')
    if args.execute and not args.writers_stopped:parser.error('Execution requires --writers-stopped maintenance declaration')
    return args


def main(argv=None):
    import json
    args=parse_args(argv)
    runtime_root=Path('D:/TradingML/runtimes').resolve()
    target=args.runtime_dir.resolve()
    if not runtime_root.is_dir() or not target.is_relative_to(runtime_root):
        raise ValueError('Required runtime root unavailable or migration journal outside it')
    options=builder.parse_args(['--date','2026-01-02','--env-file',str(args.env_file)])
    client=builder.Client(options,persistent=False)
    try:
        def run(adapter):
            return migrate(adapter,'q_live',target,[],args.execute,table_names=TABLES,
                retain_originals=True,preserve_all_versions=True,reserve_bytes=args.reserve_gib*1024**3)
        if args.execute:
            from contextlib import closing
            from pipelines.market_sip.flatfiles.download_update_events import load_env_files
            from pipelines.certified_research.publication import SourceClaim
            from src.trading_runtime.keeper_session import open_workstation_keeper_session
            load_env_files([args.env_file],verbose=False)
            with closing(open_workstation_keeper_session()) as keeper:
                claim=SourceClaim(keeper.client)
                try:result=run(Adapter(client,claim.check))
                finally:claim.close()
        else:result=run(Adapter(client))
        print(json.dumps(result,indent=2))
        return 0
    finally:client.close()


if __name__=='__main__':raise SystemExit(main())
