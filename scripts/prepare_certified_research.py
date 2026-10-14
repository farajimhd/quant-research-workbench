#!/usr/bin/env python3
"""Inspect research contracts or prepare canonical source prerequisites.

This entry point is under implementation. Inspection and saved verification
do not start a campaign. Source preparation owns bounded acquisition/reporting
repair. Launch discovers/prepares a new graph; run/resume execute an installed
immutable plan. All routes require qualified capacity and complete authority.
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
    p.add_argument('command',choices=('schema','install-schema','preflight','plan','status','stored-plan','prepare-sources','verify','launch','backfill-v7','run','resume'))
    p.add_argument('--dataset-id',type=UUID)
    p.add_argument('--source-dataset-id',type=UUID,help='Qualified source plan/bar dataset for backfill-v7; never modified')
    p.add_argument('--backfill-start-date',type=date.fromisoformat,help='Optional narrower target range inside the source plan')
    p.add_argument('--backfill-end-date',type=date.fromisoformat,help='Optional narrower target range inside the source plan')
    p.add_argument('--database',default='arte')
    p.add_argument('--start-date',type=date.fromisoformat,default=date(2026,1,1))
    p.add_argument('--end-date',type=date.fromisoformat)
    p.add_argument('--context-start-date',type=date.fromisoformat,help='Explicit earlier source prerequisite bound; target start is unchanged')
    p.add_argument('--rebuild-origin-date',type=date.fromisoformat,help='Explicit versioned empty-state origin if compatible causal seeds are absent; every subsequent session is replayed')
    p.add_argument('--fundamental-start-date',type=date.fromisoformat,help='Fresh-launch SEC context coverage bound')
    p.add_argument('--universe-anchor-date',type=date.fromisoformat,default=date.today(),help='Last allowed 2026-onward conid observation date')
    p.add_argument('--env-file',type=Path,default=ROOT/'.env')
    p.add_argument('--capacity-profile',type=Path,help='Bounded metadata configuration containing externally qualified pilot rates; required for run/resume')
    p.add_argument('--workers',type=int,default=4)
    p.add_argument('--query-threads',type=int,default=2)
    p.add_argument('--query-memory-gib',type=int,default=2)
    p.add_argument('--global-memory-gib',type=int,default=16)
    def integer_list(value):
        try:values=tuple(int(v) for v in value.split(','))
        except ValueError:raise argparse.ArgumentTypeError('Use comma-separated positive integers')
        if not values or tuple(sorted(set(values)))!=values or any(v<=0 for v in values):
            raise argparse.ArgumentTypeError('Values must be sorted, unique and positive')
        return values
    p.add_argument('--volume-windows-seconds',type=integer_list,default=(10,60),help='Rolling market feature requirements for new planning')
    p.add_argument('--short-windows-sessions',type=integer_list,default=(20,),help='Short-pressure session lookbacks for new planning')
    p.add_argument('--resolutions-ms',type=integer_list,default=(100,1000,5000,10000,30000,60000,300000,3600000),help='New-plan bar resolutions; 1000 is required by causal V7')
    p.add_argument('--format',choices=('text','json','sql'),default='text')
    p.add_argument('--acquire-missing',action='store_true',help='Opt in to the existing flatfile downloader after Massive resubscription; acquire missing appendable sources before plan freeze')
    p.add_argument('--stage-archive',action='store_true',help='Stage new-source G files on D inside the canonical updater')
    p.add_argument('--recover-retained-reporting',action='store_true',help='Authorized ingestion-only original-clock recovery from retained G/D trades; no REST')
    p.add_argument('--staging-reserve-gib',type=int,default=256,help='Minimum D free-space reserve')
    p.add_argument('--staging-reserve-fraction',type=float,default=.10,help='D capacity fraction reserved; larger of both reserve bounds applies')
    p.add_argument('--staging-chunk-gib',type=int,default=256,help='Maximum retained-file chunk; 0 uses all available headroom')
    p.add_argument('--runtime-root',type=Path,default=acquisition.RUNTIME/'certified-research')
    p.add_argument('--cache-root',type=Path,default=acquisition.CACHE)
    p.add_argument('--archive-root',type=Path,default=acquisition.ARCHIVE)
    p.add_argument('--clickhouse-cache-root',default='/mnt/d/market-data/flatfiles/us_stocks_sip')
    p.add_argument('--hydrate-missing-trades',action='store_true',help='Deprecated: imported-day repair requires canonical sidecars')
    args=p.parse_args(argv)
    if args.staging_reserve_gib<0 or args.staging_chunk_gib<0 or not 0<=args.staging_reserve_fraction<1:
        p.error('Invalid SSD staging reserve or chunk bound')
    if args.recover_retained_reporting and args.command not in ('prepare-sources','launch'):
        p.error('--recover-retained-reporting belongs to prepare-sources')
    try:contracts.identifier(args.database)
    except ValueError as error:p.error(str(error))
    if args.command!='schema' and args.format=='sql':p.error('SQL format is for schema inspection only')
    if args.command in ('status','stored-plan','verify','launch','backfill-v7','run','resume') and args.dataset_id is None:p.error(args.command+' requires --dataset-id')
    if args.command in ('launch','backfill-v7','run','resume') and args.capacity_profile is None:p.error(args.command+' requires --capacity-profile')
    if args.command=='backfill-v7':
        if args.source_dataset_id is None or args.source_dataset_id==args.dataset_id:
            p.error('backfill-v7 requires distinct source and new target dataset UUIDs')
        if args.acquire_missing or args.stage_archive or args.recover_retained_reporting:
            p.error('backfill-v7 reads certified ClickHouse inputs only; prepare sources before backfill')
    elif any(v is not None for v in (args.source_dataset_id,args.backfill_start_date,args.backfill_end_date)):
        p.error('Source dataset and backfill bounds belong to backfill-v7')
    if args.command=='launch' and any(v is None for v in (args.context_start_date,args.fundamental_start_date)):
        p.error('launch requires --context-start-date and --fundamental-start-date')
    if args.rebuild_origin_date is not None and args.command!='launch':
        p.error('--rebuild-origin-date belongs to fresh launch; resume uses the frozen plan')
    if args.command=='prepare-sources' and (args.end_date is None or args.format!='text'):
        p.error('prepare-sources requires --end-date and uses text progress')
    if args.hydrate_missing_trades:p.error('Imported-day reporting repair cannot hydrate flatfiles; G-to-D staging belongs to acquisition')
    if args.command=='verify' and (args.acquire_missing or args.stage_archive):p.error('verify is read-only; acquisition flags belong to prepare-sources')
    if args.command in ('run','resume') and (args.acquire_missing or args.stage_archive):p.error('Frozen execution cannot acquire sources; use prepare-sources before freezing the plan')
    return args


def campaign_config(args,end):
    """One authority for launch selections and requirement-derived context."""
    return contracts.CampaignConfig(args.start_date,end,database=args.database,workers=args.workers,
        query_threads=args.query_threads,query_memory_bytes=args.query_memory_gib*1024**3,
        global_memory_bytes=args.global_memory_gib*1024**3,volume_windows_seconds=args.volume_windows_seconds,
        short_windows_sessions=args.short_windows_sessions,resolutions_ms=args.resolutions_ms)


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
    config=campaign_config(args,end)
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
    if 'sec_acquisition_preflight' in result and not result['sec_acquisition_preflight']['ready']:
        blocked.append('SEC acquisition: incomplete '+','.join(result['sec_acquisition_preflight']['gaps']))
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
        if args.command in ('launch','backfill-v7','run','resume'):
            from contextlib import closing
            from types import SimpleNamespace
            from threading import Event
            import signal
            import time
            from pipelines.market_sip.flatfiles.download_update_events import load_env_files
            from src.trading_runtime.keeper_session import open_workstation_keeper_session
            from pipelines.certified_research.publication import KeeperClaim,DictionaryClaim,SourceClaim
            from pipelines.certified_research import saved_execution,capacity_profile,fresh_execution,checkpoint_backfill
            profile=capacity_profile.load(args.capacity_profile)
            load_env_files([args.env_file],verbose=False)
            stop=Event();last=[0.];counts={}
            def progress(change):
                counts.clear();counts.update(change['counts'])
                now=time.monotonic()
                if args.format=='text' and now-last[0]>=1:
                    print(change.get('phase','execution')+' | '+' | '.join(f'{k}={v}' for k,v in sorted(counts.items())),flush=True)
                    last[0]=now
            def factory(config):
                options=SimpleNamespace(env_file=args.env_file,max_threads=config.query_threads,
                    max_memory_gb=config.query_memory_bytes/1024**3,query_timeout=45)
                return builder.Client(options,persistent=False)
            saved=None if args.command=='launch' else campaign.load_plan(lambda sql:client.query(sql+' SETTINGS readonly=1'),args.database,
                str(args.source_dataset_id if args.command=='backfill-v7' else args.dataset_id))
            if saved is None:
                end=args.end_date
                if end is None:
                    frontier=client.query('SELECT max(source_date) last_day FROM market_sip_compact.events_source_day_stats FINAL')[0]['last_day']
                    if not frontier or str(frontier)=='1970-01-01':raise ValueError('No canonical source frontier; select an explicit acquisition end date')
                    end=date.fromisoformat(str(frontier))
                config=campaign_config(args,end)
            else:config=saved.config
            inspection_client=client
            client=factory(config)
            inspection_client.close()
            original=signal.getsignal(signal.SIGINT)
            signal.signal(signal.SIGINT,lambda *unused:stop.set())
            try:
                with closing(open_workstation_keeper_session()) as keeper:
                    if args.command=='launch':
                        paths=acquisition.IngestionPaths(args.env_file,args.runtime_root,args.cache_root,args.archive_root,args.clickhouse_cache_root)
                        paths.validate()
                        dictionary=DictionaryClaim(keeper.client,args.database)
                        try:installation.install(client.query,args.database,dictionary)
                        finally:dictionary.close()
                        existing=client.query(f'SELECT count() n FROM {args.database}.research_dataset_v1 '
                            f"WHERE dataset_id=toUUID('{args.dataset_id}')")[0]['n']
                        if int(existing):raise ValueError('Dataset already has a frozen definition; use resume')
                        source_claim=SourceClaim(keeper.client)
                        try:
                            acquisition.prepare_sources(lambda sql:client.query(sql+' SETTINGS readonly=1'),paths,config,source_claim,
                                stop,acquire_missing=args.acquire_missing,stage_archive=args.stage_archive,context_start=args.context_start_date,
                                writer_query=client.query,recover_retained_reporting=args.recover_retained_reporting,
                                staging_reserve_bytes=args.staging_reserve_gib*1024**3,staging_reserve_fraction=args.staging_reserve_fraction,
                                staging_chunk_bytes=args.staging_chunk_gib*1024**3)
                        finally:source_claim.close()
                    claim=KeeperClaim(keeper.client,str(args.dataset_id))
                    try:
                        dictionary_factory=lambda:DictionaryClaim(keeper.client,args.database)
                        admission=capacity_profile.factory(client.query,profile)
                        if args.command=='launch':
                            result=fresh_execution.run(client.query,config,str(args.dataset_id),claim,factory,
                                dictionary_factory,admission,context_start=args.context_start_date,fundamental_start=args.fundamental_start_date,
                                anchor_date=args.universe_anchor_date,stop=stop,progress=progress,
                                rebuild_origin=args.rebuild_origin_date)
                        elif args.command=='backfill-v7':
                            result=checkpoint_backfill.run(client.query,args.database,str(args.source_dataset_id),str(args.dataset_id),
                                claim,factory,dictionary_factory,admission,stop=stop,progress=progress,
                                start=args.backfill_start_date,end=args.backfill_end_date)
                        else:result=saved_execution.run(client.query,args.database,str(args.dataset_id),claim,factory,
                            dictionary_factory,admission,stop=stop,progress=progress)
                    finally:claim.close()
            finally:signal.signal(signal.SIGINT,original)
            if not counts and result['status']=='complete' and result.get('seal'):
                counts=dict(queued=0,active=0,reused=int(result['seal']['units']),failed=0,blocked=0,interrupted=0)
            summary=dict(status=result['status'],counts=counts,seal=result.get('seal'),
                failures={k:client.clean_error(v)[:256] for k,v in list(result['errors'].items())[:20]},
                failed_units=len(result['errors']))
            if args.format=='json':print(json.dumps(summary,indent=2,default=str))
            else:
                print(f"Execution {summary['status']} | failed={summary['failed_units']} | "+
                    (str(summary['seal']['root_hash']) if summary['seal'] else 'unsealed'))
                for key,reason in summary['failures'].items():print(f'  {key}: {reason}')
            return 0 if result['status']=='complete' else 130 if result['status']=='interrupted' else 2
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
            import signal
            from pipelines.market_sip.flatfiles.download_update_events import load_env_files
            from src.trading_runtime.keeper_session import open_workstation_keeper_session
            from pipelines.certified_research.publication import SourceClaim
            load_env_files([args.env_file],verbose=False)
            config=campaign_config(args,args.end_date)
            paths=acquisition.IngestionPaths(args.env_file,args.runtime_root,args.cache_root,args.archive_root,args.clickhouse_cache_root)
            paths.validate()
            stop=Event();original=signal.getsignal(signal.SIGINT)
            signal.signal(signal.SIGINT,lambda *unused:stop.set())
            try:
                with closing(open_workstation_keeper_session()) as keeper:
                    claim=SourceClaim(keeper.client)
                    try:
                        result=acquisition.prepare_sources(lambda sql:client.query(sql+' SETTINGS readonly=1'),paths,config,claim,
                            stop,acquire_missing=args.acquire_missing,stage_archive=args.stage_archive,context_start=args.context_start_date,
                            writer_query=client.query,recover_retained_reporting=args.recover_retained_reporting,
                            staging_reserve_bytes=args.staging_reserve_gib*1024**3,
                            staging_reserve_fraction=args.staging_reserve_fraction,
                            staging_chunk_bytes=args.staging_chunk_gib*1024**3)
                    finally:claim.close()
            finally:signal.signal(signal.SIGINT,original)
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
            from pipelines.certified_research import sec_coverage,planning
            result['sec_acquisition_preflight']=sec_coverage.inventory(
                lambda sql:client.query(sql+' SETTINGS readonly=1'),args.context_start_date or args.start_date,
                planning.reference_coverage_end(end))
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
                missing_reporting=[str(d) for d in reporting['missing']],unqualified_reporting=reporting.get('unqualified',{}),source_hash=inv['content_hash'],
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
    except InterruptedError:
        print('Preparation interrupted; rerun launch before plan publication, or resume an already frozen dataset.',file=sys.stderr)
        return 130
    except Exception as error:
        label='Verification failed: ' if args.command=='verify' else 'Execution failed: ' if args.command in ('launch','backfill-v7','run','resume') else 'Preflight failed: '
        print(label+client.clean_error(error),file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        print('Verification interrupted; rerun verify to recheck stored authority.' if args.command=='verify' else
            'Execution interrupted; resume the same immutable dataset after checking status.' if args.command in ('launch','backfill-v7','run','resume') else
            'Source preparation interrupted; verify canonical receipts before resume.',file=sys.stderr)
        return 130
    finally:client.close()


if __name__=='__main__':raise SystemExit(main())
