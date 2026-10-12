#!/usr/bin/env python3
"""Inspect certified research schemas and source prerequisites.

This entry point is under implementation. No campaign or downstream export
can be started by the inspection commands. All inspection reads are bounded.
"""
from __future__ import annotations

import argparse
from datetime import date
import json
import os
from pathlib import Path
import sys

os.environ['PYTHONDONTWRITEBYTECODE']='1'
sys.dont_write_bytecode=True
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))

from pipelines.certified_research import contracts,schema,source,short_sql


def parse_args(argv=None):
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('command',choices=('schema','preflight','plan'))
    p.add_argument('--database',default='arte')
    p.add_argument('--start-date',type=date.fromisoformat,default=date(2026,1,1))
    p.add_argument('--end-date',type=date.fromisoformat)
    p.add_argument('--env-file',type=Path,default=ROOT/'.env')
    p.add_argument('--workers',type=int,default=4)
    p.add_argument('--query-threads',type=int,default=2)
    p.add_argument('--query-memory-gib',type=int,default=2)
    p.add_argument('--global-memory-gib',type=int,default=16)
    p.add_argument('--format',choices=('text','json','sql'),default='text')
    p.add_argument('--acquire-missing',action='store_true',help='Include missing-source acquisition in the frozen plan')
    p.add_argument('--hydrate-missing-trades',action='store_true',help='Include verified G-to-D reporting migration staging')
    args=p.parse_args(argv)
    try:contracts.identifier(args.database)
    except ValueError as error:p.error(str(error))
    if args.command!='schema' and args.format=='sql':p.error('SQL format is for schema inspection only')
    return args


def inspect_sources(query,args):
    status=schema.verify_storage(query,args.database)
    source_policy=query("SELECT disk_name FROM system.storage_policies ARRAY JOIN disks AS disk_name "
                        "WHERE policy_name='sip_raw_ssd'")
    if not source_policy or any(r['disk_name']!='sip_raw_ssd' for r in source_policy):
        raise ValueError('Canonical source policy is absent or incorrectly routed')
    source_parts=query("SELECT table,disk_name FROM system.parts WHERE active AND database='market_sip_compact' "
                       "AND match(table,'^events_[0-9]{4}$') GROUP BY table,disk_name")
    if not source_parts or any(r['disk_name']!='sip_raw_ssd' for r in source_parts):
        raise ValueError('Canonical source parts are missing or incorrectly placed')
    source=query("SELECT min(source_date) first_day,max(source_date) last_day,count() sessions,"
                 "sum(total_event_rows_after_filters) events,sum(trade_event_rows) trades "
                 "FROM market_sip_compact.events_source_day_stats FINAL "
                 f"WHERE source_date>={contracts.literal(args.start_date)}"+
                 (f" AND source_date<={contracts.literal(args.end_date)}" if args.end_date else ''))
    if len(source)!=1 or not source[0]['sessions']:
        raise ValueError('No certified source-day statistics; acquisition is required')
    end=args.end_date or date.fromisoformat(source[0]['last_day'])
    config=contracts.CampaignConfig(args.start_date,end,database=args.database,workers=args.workers,
        query_threads=args.query_threads,query_memory_bytes=args.query_memory_gib*1024**3,
        global_memory_bytes=args.global_memory_gib*1024**3)
    mutations=query("SELECT database,table,mutation_id FROM system.mutations "
                    "WHERE database='market_sip_compact' AND startsWith(table,'events_') AND NOT is_done LIMIT 20")
    return dict(status='blocked' if mutations else 'inspected',source=source[0],storage=status,
                config=config.identity(),plan_hash=config.content_hash,canonical_mutations=len(mutations),
                campaign_execution_enabled=False,
                reason='Full campaign handlers are still under implementation')


def main(argv=None):
    args=parse_args(argv)
    if args.command=='schema':
        if args.format=='sql':print(';\n\n'.join(schema.ddl(args.database))+';')
        elif args.format=='json':
            print(json.dumps(dict(schema_hash=schema.schema_hash(),tables=[
                dict(name=t.name,order=t.order,partition=t.partition,columns=[vars(c) for c in t.columns])
                for t in schema.TABLES]),indent=2))
        else:
            print(f'Compact research schema | {len(schema.TABLES)} tables | live_market_ssd')
            for t in schema.TABLES:print(f'  {t.name}: {len(t.columns)} columns, explicit lossless codecs')
            print('Use --format sql for exact DDL or --format json for the column catalog.')
        return 0
    import build_market_day as builder
    args.max_threads=args.query_threads;args.max_memory_gb=args.query_memory_gib;args.query_timeout=45
    client=builder.Client(args,persistent=False)
    try:
        # Every query in the inspection path is read-only at the server as well.
        result=inspect_sources(lambda sql:client.query(sql+' SETTINGS readonly=1'),args)
        if args.command=='plan':
            end=date.fromisoformat(result['config']['config']['end']) if isinstance(result['config']['config']['end'],str) else result['config']['config']['end']
            result['short_publication_preflight']=short_sql.clock_inventory(
                lambda sql:client.query(sql+' SETTINGS readonly=1'),args.start_date,end)
            inv=source.inventory(lambda sql:client.query(sql+' SETTINGS readonly=1'),args.start_date,end)
            reporting=source.reporting_inventory(lambda sql:client.query(sql+' SETTINGS readonly=1'),inv)
            result['source_plan']=dict(missing_sources=[str(d) for d in inv['missing']],
                incomplete_sources=[str(d) for d in inv['incomplete']],
                missing_reporting=[str(d) for d in reporting['missing']],source_hash=inv['content_hash'],
                reporting_hash=reporting['content_hash'],acquire_missing=args.acquire_missing,
                hydrate_missing_trades=args.hydrate_missing_trades)
        if args.format=='json':print(json.dumps(result,indent=2,default=str))
        else:
            summary=result['source']
            print(f"Source inspected | {summary['first_day']} to {summary['last_day']} | {summary['sessions']} sessions")
            print(f"Storage checked | {result['storage']['tables']} existing research tables | {result['storage']['parts']} part groups")
            print(f"Status: {result['status']} | {result['reason']}")
        return 2 if result['status']=='blocked' else 0
    except Exception as error:
        print('Preflight failed: '+client.clean_error(error),file=sys.stderr)
        return 2
    finally:client.close()


if __name__=='__main__':raise SystemExit(main())
