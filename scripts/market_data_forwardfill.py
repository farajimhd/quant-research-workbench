"""Restart-safe ingestion -> tradable bars -> daily-session authority handoff.

Only the ingestion owner opens new flatfiles. All derived stages read canonical
events and use the bar producer's exact dated population and certified receipts.
"""
from argparse import Namespace
from contextlib import closing
from datetime import date, timedelta
import json
from pathlib import Path

from scripts import build_market_day as bars
from pipelines.market_sip.flatfiles import download_update_events as ingestion

DAILY_VERSION='tradable_timely_daily_sessions_v1'


def ingestion_arguments(a):
    values=['--env-file',str(a.env_file),'--clickhouse-url',a.endpoint,
        '--flatfiles-root-win',str(a.cache_root),'--flatfiles-root-ch',a.clickhouse_cache_root,
        '--archive-root-win',str(a.archive_root),'--output-root-win',str(a.run_dir/'ingestion'),
        '--download-workers',str(a.copy_workers),'--max-threads',str(a.query_threads),
        '--max-memory-usage',f'{a.query_memory_gib}G','--skip-bars',
        '--progress-layout',a.progress if a.progress=='text' else 'auto']
    if a.start_date:
        values+=['--start-date',str(a.start_date),'--end-date',str(a.end_date)]
    return values


def daily_product(a, day, child, *, plan_report=None):
    with bars.build_lock(bars.RUNTIME/'market-daily-arte.lock'):
        return _daily_product(a,day,child,plan_report=plan_report)


def _daily_product(a, day, child, *, plan_report=None):
    """Replace only this certified session, using exactly the funnel population."""
    from pipelines.market_sip.events import clickhouse_build_daily_session_bars as daily
    child.mkdir(parents=True,exist_ok=True)
    report=plan_report or json.loads((child/'build'/'latest.json').read_text())
    if not plan_report and report['status']!='core_complete':
        raise ValueError('Daily derivation requires a completed bar certification')
    selected=sorted({row['ticker'] for row in report['definition']['plan']['units'] if row['source_date']==day})
    if not selected:raise ValueError('No certified tradable population for daily derivation')
    options=daily.parse_args(['--start-date',day,'--end-date',(date.fromisoformat(day)+timedelta(days=1)).isoformat(),
        '--target-database','arte','--storage-policy','live_market_ssd','--tickers',','.join(selected),
        '--max-threads',str(a.query_threads),'--max-memory-usage',f'{a.bar_query_memory_gib}G',
        '--max-bytes-before-external-group-by','512M','--feature-version',DAILY_VERSION,
        '--verify-source-count'])
    options.tradable_timely=True
    options.population_hash=bars.digest(dict(tickers=selected,
        population=[row for row in report['definition']['plan'].get('population',[]) if row['session_date']==day],
        evidence=report['definition']['plan'].get('population_evidence')))
    # Reuse the configured credentials without recording them in the handoff.
    client=bars.Client(bars.parse_args(['--date',day,'--env-file',str(a.env_file),
        '--max-threads',str(a.query_threads),'--max-memory-gb',str(a.bar_query_memory_gib)]),persistent=False)
    with closing(client):
        http=client.http
        # Receipt and mutation metadata only; never reopen raw files or rescan
        # historical events to infer eligibility.
        current=bars.reporting_coverage(client,date.fromisoformat(day),date.fromisoformat(day))
        expected=report['definition'].get('reporting') or report['definition'].get('trade_eligibility',{}).get('verified_coverage')
        if expected and current!=[row for row in expected if row['source_date']==day]:
            raise ValueError('Daily source reporting evidence changed since the population plan')
        daily.validate_preflight(http,options)
        http.execute(daily.create_target_table_sql(options))
        http.execute(daily.create_manifest_table_sql(options))
        daily.validate_schema(http,options)
        tables=(options.target_table,options.manifest_table)
        for table in tables:
            policy=client.query(f"SELECT storage_policy FROM system.tables WHERE database='arte' AND name={bars.sql.literal(table)}")
            parts=client.query(f"SELECT DISTINCT disk_name FROM system.parts WHERE active AND database='arte' AND table={bars.sql.literal(table)}")
            disks=client.query("SELECT disks FROM system.storage_policies WHERE policy_name='live_market_ssd'")
            allowed={disk for row in disks for disk in row['disks']} - {'default','hdd'}
            if len(policy)!=1 or policy[0]['storage_policy']!='live_market_ssd' or not allowed or any(p['disk_name'] not in allowed for p in parts):
                raise ValueError('Daily authority requires verified live_market_ssd storage and part placement')
        left=date.fromisoformat(day);right=left+timedelta(days=1)
        if daily.unit_id(left,right) in daily.completed_units(http,options):return
        result=daily.build_chunk(http,options,left,right)
        placed=client.query(f"SELECT DISTINCT disk_name FROM system.parts WHERE active AND database='arte' AND table={bars.sql.literal(options.target_table)}")
        if any(row['disk_name'] not in allowed for row in placed):
            raise ValueError('Daily output part placement differs from live_market_ssd policy')
    bars.save(child/'daily-completion.json',dict(version=DAILY_VERSION,day=day,
        population_hash=options.population_hash,bar_build_id=report.get('build_id'),rows=result.rows,
        source_events=result.source_events,seconds=result.seconds))


def run(a, run_bars):
    a.run_dir.mkdir(parents=True,exist_ok=True)
    plan=dict(version='market-forwardfill-v1',identity_policy='dated',start=str(a.start_date) if a.start_date else None,
        end=str(a.end_date) if a.end_date else None,endpoint=a.endpoint,
        stages=['certified canonical ingestion','tradable bar funnel','tradable timely daily sessions'],
        daily_version=DAILY_VERSION,daily_target='arte.daily_session_bars_by_symbol_time_v1',
        daily_migration='Old market_sip_compact daily tables remain stale; rebuild only selected sessions in ARTE',
        ingestion_arguments=ingestion_arguments(a))
    if a.plan_only:
        bars.save(a.run_dir/'forwardfill-plan.json',plan)
        print(json.dumps(plan,indent=2));return 0
    # This lock covers discovery, ingestion and the durable derivation queue.
    with bars.build_lock(a.run_dir/'forwardfill.lock'):
        path=a.run_dir/'forwardfill-request.json'
        if path.exists():
            if not a.resume or json.loads(path.read_text())!=plan:
                raise ValueError('Forwardfill resume requires the same frozen request')
        elif a.resume:raise ValueError('No forwardfill request exists to resume')
        else:bars.save(path,plan)
        queue_path=a.run_dir/'forwardfill-queue.json'
        queue=json.loads(queue_path.read_text()) if queue_path.exists() else dict(selected=[],complete=[])
        def selected(days):
            queue['selected']=sorted(set(queue['selected']).union(days))
            bars.save(queue_path,queue)
        ingestion.main(ingestion_arguments(a),acquisition_approved=True,selected_days=selected)
        for day in queue['selected']:
            if day in queue['complete']:continue
            child=a.run_dir/'days'/day
            options=Namespace(**vars(a));options.mode='backfill';options.start_date=options.end_date=date.fromisoformat(day)
            options.run_dir=child;options.resume=(child/'bars-request.json').exists()
            result=run_bars(options)
            if result:return result
            daily_product(a,day,child)
            queue['complete'].append(day);bars.save(queue_path,queue)
        bars.save(a.run_dir/'forwardfill-completion.json',dict(days=queue['complete'],version=plan['version']))
    return 0


def derive_imported_days(args, days):
    """Default updater handoff; suppress its retired all-listing daily/macro writer."""
    from scripts.run_market_data_pipeline import arguments, run_bars
    for day in sorted(set(days)):
        child=Path(args.output_root_win)/'tradable-products'/day
        values=['backfill','--step','bars','--start-date',day,'--end-date',day,
            '--run-dir',str(child),'--endpoint',args.clickhouse_url,
            '--bar-workers','32','--query-threads','1','--global-memory-gib','80']
        if args.env_file:values+=['--env-file',str(args.env_file[-1])]
        if (child/'bars-request.json').exists():values+=['--resume']
        a=arguments(values)
        result=run_bars(a)
        if result:raise RuntimeError(f'Tradable bars incomplete for {day}: exit {result}')
        daily_product(a,day,child)


def migrate_daily(a):
    """Versioned canonical rebuild into ARTE; the former daily tables stay stale."""
    a.run_dir.mkdir(parents=True,exist_ok=True)
    values=['--start-date',str(a.start_date),'--end-date',str(a.end_date),
        '--env-file',str(a.env_file),'--identity-policy',a.identity_policy,
        '--max-plan-units',str(a.max_plan_units),'--max-threads',str(a.query_threads),
        '--max-memory-gb',str(a.bar_query_memory_gib)]
    if a.conid_review_hash:values+=['--conid-review-hash',a.conid_review_hash]
    if a.identity_anchor_date:values+=['--identity-anchor-date',str(a.identity_anchor_date)]
    options=bars.parse_args(values)
    import os
    os.environ['QMD_CLICKHOUSE_URL']=a.endpoint
    with closing(bars.Client(options,readonly=True)) as client:
        plan=bars.source_plan(client,options)
        reporting=bars.reporting_coverage(client,options.start,options.end)
    from pipelines.market_sip.events import clickhouse_build_daily_session_bars as daily
    request=dict(version=DAILY_VERSION,population=bars.digest(plan),reporting=reporting,
        calculation_source=bars.digest(Path(daily.__file__).read_text()),
        start=str(a.start_date),end=str(a.end_date),target='arte.daily_session_bars_by_symbol_time_v1',
        stale_source='market_sip_compact.daily_session_bars_by_symbol_time_v1')
    bars.save(a.run_dir/'daily-migration-plan.json',request)
    if a.plan_only:
        print(json.dumps(request,indent=2));return 0
    with bars.build_lock(a.run_dir/'daily-migration.lock'):
        path=a.run_dir/'daily-migration-request.json'
        if path.exists():
            if not a.resume or json.loads(path.read_text())!=request:
                raise ValueError('Daily migration resume requires unchanged population and reporting receipts')
        elif a.resume:raise ValueError('No daily migration request exists to resume')
        else:bars.save(path,request)
        from pipelines.market_sip.flatfiles.repair_progress import Progress
        progress=Progress(a.run_dir)
        progress.update(step='daily',title='Tradable ARTE daily migration',status='running',
            start=str(a.start_date),end=str(a.end_date),total=len(plan['requested']),
            coverage_label='Daily sessions verified',queued=len(plan['requested']))
        by_day={}
        for row in plan['units']:by_day.setdefault(row['source_date'],[]).append(row)
        try:
            for index,day in enumerate(plan['requested']):
                progress.worker('DAILY','Build / verify daily sessions',day=str(day),detail='tradable, timely-only canonical events')
                report=dict(definition=dict(plan=dict(units=by_day.get(str(day),[]),
                    population=plan['population'],population_evidence=plan.get('population_evidence')),reporting=reporting))
                daily_product(a,str(day),a.run_dir/'days'/str(day),plan_report=report)
                progress.update(completed=index+1,queued=len(plan['requested'])-index-1)
        except BaseException as error:
            progress.update(status='failed',failed=1,message=type(error).__name__+': '+str(error));raise
        bars.save(a.run_dir/'daily-migration-completion.json',dict(request=request,days=list(map(str,plan['requested']))))
        progress.update(status='complete')
    return 0
