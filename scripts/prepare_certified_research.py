#!/usr/bin/env python3
"""Inspect research contracts or prepare canonical source prerequisites.

This entry point is under implementation. Inspection and saved verification
do not start a campaign. Source preparation owns bounded acquisition/reporting
repair; derived campaign execution is not yet enabled.
"""
from __future__ import annotations

import argparse
from datetime import date
import json
import os
from pathlib import Path
import sys
from uuid import UUID

os.environ['PYTHONDONTWRITEBYTECODE']='1'
sys.dont_write_bytecode=True
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))

from pipelines.certified_research import contracts,schema,source,short_sql,campaign,acquisition,capacity,universe,installation


def parse_args(argv=None):
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('command',choices=('schema','install-schema','preflight','plan','status','stored-plan','prepare-sources','verify'))
    p.add_argument('--dataset-id',type=UUID)
    p.add_argument('--database',default='arte')
    p.add_argument('--start-date',type=date.fromisoformat,default=date(2026,1,1))
    p.add_argument('--end-date',type=date.fromisoformat)
    p.add_argument('--context-start-date',type=date.fromisoformat,help='Explicit earlier source prerequisite bound; target start is unchanged')
    p.add_argument('--universe-anchor-date',type=date.fromisoformat,default=date.today(),help='Last allowed 2026-onward conid observation date')
    p.add_argument('--env-file',type=Path,default=ROOT/'.env')
    p.add_argument('--workers',type=int,default=4)
    p.add_argument('--query-threads',type=int,default=2)
    p.add_argument('--query-memory-gib',type=int,default=2)
    p.add_argument('--global-memory-gib',type=int,default=16)
    p.add_argument('--format',choices=('text','json','sql'),default='text')
    p.add_argument('--acquire-missing',action='store_true',help='Opt in to the existing flatfile downloader after Massive resubscription; acquire missing appendable sources before plan freeze')
    p.add_argument('--stage-archive',action='store_true',help='Stage new-source G files on D inside the canonical updater')
    p.add_argument('--runtime-root',type=Path,default=acquisition.RUNTIME/'certified-research')
    p.add_argument('--cache-root',type=Path,default=acquisition.CACHE)
    p.add_argument('--archive-root',type=Path,default=acquisition.ARCHIVE)
    p.add_argument('--clickhouse-cache-root',default='/mnt/d/market-data/flatfiles/us_stocks_sip')
    p.add_argument('--hydrate-missing-trades',action='store_true',help='Deprecated: imported-day repair requires canonical sidecars')
    args=p.parse_args(argv)
    try:contracts.identifier(args.database)
    except ValueError as error:p.error(str(error))
    if args.command!='schema' and args.format=='sql':p.error('SQL format is for schema inspection only')
    if args.command in ('status','stored-plan','verify') and args.dataset_id is None:p.error(args.command+' requires --dataset-id')
    if args.command=='prepare-sources' and (args.end_date is None or args.format!='text'):
        p.error('prepare-sources requires --end-date and uses text progress')
    if args.hydrate_missing_trades:p.error('Imported-day reporting repair cannot hydrate flatfiles; G-to-D staging belongs to acquisition')
    if args.command=='verify' and (args.acquire_missing or args.stage_archive):p.error('verify is read-only; acquisition flags belong to prepare-sources')
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


def qualify_plan(result):
    """Inspection evidence cannot masquerade as campaign admission."""
    blocked=[];inputs=result['source_plan'];membership=result['universe_preflight']
    for field in ('missing_sources','incomplete_sources','missing_reporting'):
        if inputs[field]:blocked.append(f'{field}: {len(inputs[field])} sessions')
    if result['canonical_mutations']:blocked.append('canonical source mutations are active')
    if not membership['ready']:blocked.append(f"historical universe: {membership['rejected']} unresolved intervals")
    for family,summary in result['short_publication_preflight']['families'].items():
        if summary['repair_required']:blocked.append(f"{family}: {summary['unknown_public_time']} rows lack public-time evidence")
    if not result['corporate_evidence_preflight']['original_public_vintage_certified']:
        blocked.append('corporate actions: original public vintages are not certified')
    if not result['campaign_execution_enabled']:blocked.append('full campaign execution integration is unfinished')
    result['admission_blockers']=blocked;result['status']='blocked' if blocked else 'ready'
    result['reason']='; '.join(blocked) if blocked else 'All campaign admission requirements verified'
    return result


def main(argv=None):
    args=parse_args(argv)
    if args.command=='schema':
        catalog=installation.catalog(args.database)
        if args.format=='sql':print(';\n\n'.join(t.ddl(args.database) for _,t in catalog)+';')
        elif args.format=='json':
            print(json.dumps(installation.manifest(args.database),indent=2))
        else:
            print(f'Compact research schema | {len(catalog)} tables | live_market_ssd')
            for group,t in catalog:print(f'  {group}: {t.name} | {len(t.columns)} columns, explicit lossless codecs')
            print('Use --format sql for exact DDL or --format json for the column catalog.')
        return 0
    from scripts import build_market_day as builder
    args.max_threads=args.query_threads;args.max_memory_gb=args.query_memory_gib;args.query_timeout=45
    client=builder.Client(args,persistent=False)
    try:
        if args.command=='verify':
            from contextlib import closing
            from types import SimpleNamespace
            from pipelines.market_sip.flatfiles.download_update_events import load_env_files
            from src.trading_runtime.keeper_session import open_workstation_keeper_session
            from pipelines.certified_research.publication import KeeperClaim,DictionaryClaim
            from pipelines.certified_research import verification
            load_env_files([args.env_file],verbose=False)
            def factory(config):
                options=SimpleNamespace(env_file=args.env_file,max_threads=config.query_threads,
                    max_memory_gb=config.query_memory_bytes/1024**3,query_timeout=45)
                real=builder.Client(options,persistent=False)
                class Reader:
                    def query(self,sql):return real.query(sql+' SETTINGS readonly=1')
                    def close(self):real.close()
                return Reader()
            with closing(open_workstation_keeper_session()) as keeper:
                claim=KeeperClaim(keeper.client,str(args.dataset_id))
                try:
                    result=verification.verify(lambda sql:client.query(sql+' SETTINGS readonly=1'),args.database,
                        str(args.dataset_id),claim,factory,lambda:DictionaryClaim(keeper.client,args.database))
                finally:claim.close()
            if args.format=='json':print(json.dumps(result,indent=2))
            else:print(f"Verified | {result['units']} units | {result['root_hash']} | read-only")
            return 0
        if args.command=='install-schema':
            from contextlib import closing
            from pipelines.market_sip.flatfiles.download_update_events import load_env_files
            from src.trading_runtime.keeper_session import open_workstation_keeper_session
            from pipelines.certified_research.publication import DictionaryClaim
            load_env_files([args.env_file],verbose=False)
            with closing(open_workstation_keeper_session()) as keeper:
                claim=DictionaryClaim(keeper.client,args.database)
                try:result=installation.install(client.query,args.database,claim)
                finally:claim.close()
            if args.format=='json':print(json.dumps(result,indent=2))
            else:print(f"Schema verified | {result['tables']} tables | {result['created']} created | live_market_ssd")
            return 0
        if args.command=='prepare-sources':
            from contextlib import closing
            from threading import Event
            from pipelines.market_sip.flatfiles.download_update_events import load_env_files
            from src.trading_runtime.keeper_session import open_workstation_keeper_session
            from pipelines.certified_research.publication import SourceClaim
            load_env_files([args.env_file],verbose=False)
            config=contracts.CampaignConfig(args.start_date,args.end_date,database=args.database,workers=args.workers,
                query_threads=args.query_threads,query_memory_bytes=args.query_memory_gib*1024**3,
                global_memory_bytes=args.global_memory_gib*1024**3)
            paths=acquisition.IngestionPaths(args.env_file,args.runtime_root,args.cache_root,args.archive_root,args.clickhouse_cache_root)
            paths.validate()
            with closing(open_workstation_keeper_session()) as keeper:
                claim=SourceClaim(keeper.client)
                try:
                    result=acquisition.prepare_sources(lambda sql:client.query(sql+' SETTINGS readonly=1'),paths,config,claim,
                        Event(),acquire_missing=args.acquire_missing,stage_archive=args.stage_archive,context_start=args.context_start_date,
                        writer_query=client.query)
                finally:claim.close()
            print(f"Source ready | {len(result['source']['expected'])} sessions | {result['source_hash']}")
            print('Derived campaign execution remains under implementation.')
            return 0
        if args.command=='status':
            result=campaign.status(lambda sql:client.query(sql+' SETTINGS readonly=1'),args.database,str(args.dataset_id))
            print(json.dumps(result,indent=2,default=str))
            return 0
        if args.command=='stored-plan':
            from collections import Counter
            saved=campaign.load_plan(lambda sql:client.query(sql+' SETTINGS readonly=1'),args.database,str(args.dataset_id))
            result=dict(dataset_id=saved.dataset_id,plan_hash=saved.plan_hash,source_hash=saved.source_hash,
                config=saved.config.identity(),units=len(saved.units),products=dict(Counter(p.unit.product for p in saved.units)),
                dependencies=sum(len(p.unit.parents) for p in saved.units),inspection_only=True)
            if args.format=='json':print(json.dumps(result,indent=2,default=str))
            else:print(f"Stored plan verified | {result['units']} units | {result['dependencies']} dependencies | {saved.plan_hash}")
            return 0
        # Every query in the inspection path is read-only at the server as well.
        result=inspect_sources(lambda sql:client.query(sql+' SETTINGS readonly=1'),args)
        if args.command=='plan':
            result['storage_inventory']=capacity.inventory(lambda sql:client.query(sql+' SETTINGS readonly=1'),args.database)
            result['corporate_evidence_preflight']=source.corporate_evidence_inventory(
                lambda sql:client.query(sql+' SETTINGS readonly=1'))
            end=date.fromisoformat(result['config']['config']['end']) if isinstance(result['config']['config']['end'],str) else result['config']['config']['end']
            historical=universe.load(lambda sql:client.query(sql+' SETTINGS readonly=1'),
                args.context_start_date or args.start_date,end,args.universe_anchor_date)
            result['universe_preflight']=dict(ready=historical['ready'],intervals=len(historical['intervals']),
                rejected=len(historical['rejected']),rejection_counts=historical['rejection_counts'],
                outside_scope=historical['outside_scope'],source_hash=historical['source_hash'])
            result['universe_repair_requirements']=universe.repair_inventory(historical)
            result['short_publication_preflight']=short_sql.clock_inventory(
                lambda sql:client.query(sql+' SETTINGS readonly=1'),args.start_date,end)
            inv=source.inventory(lambda sql:client.query(sql+' SETTINGS readonly=1'),args.start_date,end)
            reporting=source.reporting_inventory(lambda sql:client.query(sql+' SETTINGS readonly=1'),inv)
            result['source_plan']=dict(missing_sources=[str(d) for d in inv['missing']],
                incomplete_sources=[str(d) for d in inv['incomplete']],
                missing_reporting=[str(d) for d in reporting['missing']],source_hash=inv['content_hash'],
                reporting_hash=reporting['content_hash'],acquire_missing=args.acquire_missing,
                hydrate_missing_trades=args.hydrate_missing_trades)
            result['reporting_clock_evidence']=source.clock_evidence_inventory(
                lambda sql:client.query(sql+' SETTINGS readonly=1'),args.start_date,end)
            qualify_plan(result)
        if args.format=='json':print(json.dumps(result,indent=2,default=str))
        else:
            summary=result['source']
            print(f"Source inspected | {summary['first_day']} to {summary['last_day']} | {summary['sessions']} sessions")
            print(f"Storage checked | {result['storage']['tables']} existing research tables | {result['storage']['parts']} part groups")
            print(f"Status: {result['status']} | {result['reason']}")
        return 2 if result['status']=='blocked' else 0
    except Exception as error:
        print(('Verification failed: ' if args.command=='verify' else 'Preflight failed: ')+client.clean_error(error),file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        print('Verification interrupted; rerun verify to recheck stored authority.' if args.command=='verify' else
            'Source preparation interrupted; verify canonical receipts before resume.',file=sys.stderr)
        return 130
    finally:client.close()


if __name__=='__main__':raise SystemExit(main())
