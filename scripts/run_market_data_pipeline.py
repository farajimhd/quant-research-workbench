"""Workstation market-data launcher: reporting repair or sequential bar funnel.

Backfill --step bars reuses the ticker worker pool, persists 100ms/1s liquidity,
and rolls trade bars through successive frames. --with-indicators adds the
existing indicator stage. Forwardfill ingests before deriving tradable products.
"""
import os
os.environ['PYTHONDONTWRITEBYTECODE']='1'
import sys
sys.dont_write_bytecode=True
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import argparse
from contextlib import closing
from datetime import date,datetime,timezone
import json
import signal
from threading import Event,Thread
from types import SimpleNamespace

from scripts.build_market_day import Client
from scripts.backfill_trade_reporting_flags import preflight
from pipelines.certified_research import acquisition,source
from pipelines.certified_research.contracts import digest,literal
from pipelines.certified_research.publication import SourceClaim
from pipelines.market_sip.flatfiles import retained_reporting
from pipelines.market_sip.flatfiles.repair_progress import Prefetch,Progress,render
from pipelines.market_sip.flatfiles.repair_terminal import snapshot
from pipelines.market_sip.flatfiles.repair_preparation import Preparation,repair_month
from pipelines.market_sip.flatfiles.download_update_events import load_env_files
from src.trading_runtime.keeper_session import open_workstation_keeper_session

GIB=1024**3


def arguments(argv=None):
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('mode',choices=('backfill','forwardfill','monitor'))
    p.add_argument('--step',choices=('trade-reporting-repair','bars','daily','structural'))
    p.add_argument('--bars-run-dir',type=Path,default=Path('D:/TradingML/runtimes/market-data-pipeline/bars-2026-funnel-32'))
    p.add_argument('--migrate-structural-schema',action='store_true')
    p.add_argument('--allow-acquisition',action='store_true',help='Explicitly authorize provider discovery/download for forwardfill')
    p.add_argument('--bar-workers',type=int,choices=range(1,33))
    p.add_argument('--bar-query-memory-gib',type=float,default=2.)
    p.add_argument('--tickers',default='')
    p.add_argument('--identity-policy',choices=('dated','current_conid_retrospective_v1'),default='dated')
    p.add_argument('--conid-review-hash')
    p.add_argument('--identity-anchor-date',type=date.fromisoformat)
    p.add_argument('--max-plan-units',type=int,default=250000)
    p.add_argument('--allow-carried-forward-universe',action='store_true')
    p.add_argument('--with-indicators',action='store_true')
    p.add_argument('--start-date',type=date.fromisoformat)
    p.add_argument('--end-date',type=date.fromisoformat,help='Inclusive; defaults to latest certified imported source day')
    p.add_argument('--run-dir',type=Path,required=True)
    p.add_argument('--env-file',type=Path,default=Path('D:/TradingML/secrets/.env'))
    p.add_argument('--endpoint',default='http://desktop-saai85t:18123')
    p.add_argument('--archive-root',type=Path,default=Path('G:/market-data/flatfiles/us_stocks_sip'))
    p.add_argument('--cache-root',type=Path,default=Path('D:/market-data/flatfiles/us_stocks_sip'))
    p.add_argument('--clickhouse-cache-root',default='/mnt/d/market-data/flatfiles/us_stocks_sip')
    p.add_argument('--copy-workers',type=int,choices=range(1,5),default=2)
    p.add_argument('--sql-workers',type=int,choices=range(1,5),default=2)
    p.add_argument('--global-memory-gib',type=int)
    p.add_argument('--prefetch-gib',type=int,default=32)
    p.add_argument('--reserve-gib',type=int,default=256)
    p.add_argument('--reserve-fraction',type=float,default=.10)
    p.add_argument('--query-threads',type=int,choices=range(1,9))
    p.add_argument('--query-memory-gib',type=int,default=8)
    p.add_argument('--query-timeout',type=int,default=1800)
    p.add_argument('--plan-only',action='store_true')
    p.add_argument('--benchmark-sql',action='store_true',help='Read-only serial/two-worker classification benchmark; exactly two already-staged days')
    p.add_argument('--resume',action='store_true')
    p.add_argument('--repair-engine',choices=('compact-v2','legacy-v1'),default='compact-v2',
                   help='compact-v2 classifies once, retains exception keys only, and consolidates partition updates')
    p.add_argument('--once',action='store_true',help='monitor: print one snapshot')
    p.add_argument('--progress',choices=('auto','text'),default='auto')
    a=p.parse_args(argv)
    a.bar_workers=a.bar_workers or (32 if a.mode=='forwardfill' else 8)
    if a.global_memory_gib is None:a.global_memory_gib=80 if a.mode=='forwardfill' else 32
    a.query_threads=a.query_threads or (1 if a.mode=='forwardfill' or a.step=='daily' else 4)
    a.step=a.step or ('bars' if a.mode=='forwardfill' else 'trade-reporting-repair')
    if a.mode=='forwardfill':
        if a.step!='bars' or a.identity_policy!='dated' or a.allow_carried_forward_universe:
            p.error('Forwardfill requires bars and exact dated tradable membership')
        if (a.start_date is None)!=(a.end_date is None) or (a.start_date and a.end_date<a.start_date):
            p.error('Forwardfill accepts both inclusive dates or neither for automatic append')
        if not a.plan_only and not a.allow_acquisition:
            p.error('Forwardfill acquisition requires explicit --allow-acquisition')
        if a.with_indicators or a.tickers:
            p.error('Forwardfill derives bars for the complete dated tradable population only')
    if a.mode=='backfill' and (a.start_date is None or (a.end_date and a.end_date<a.start_date)):
        p.error('Backfill requires --start-date and an ordered inclusive range')
    if not 1<=a.prefetch_gib<=256 or not 0<=a.reserve_gib or not 0<=a.reserve_fraction<1:
        p.error('Invalid SSD staging budget')
    if not 1<=a.query_memory_gib<=32 or not 1<=a.query_timeout<=3600:p.error('Invalid query budget')
    if a.step in ('bars','daily') and a.mode=='backfill' and a.end_date is None:p.error('Bars/daily requires --end-date')
    if a.step=='trade-reporting-repair' and (a.sql_workers+1)*a.query_memory_gib+1>a.global_memory_gib:
        p.error('SQL workers, coordinator and telemetry exceed the global query-memory budget')
    return a


def monitor(a):
    from time import sleep
    from rich.console import Console
    from rich.live import Live
    console=Console()
    if a.once or a.progress=='text' or not sys.stdout.isatty():
        console.print(render(snapshot(a.run_dir),console.width,console.height))
        return 0
    try:
        # Keep a stable screen instead of erasing the previous dashboard each tick.
        with Live(console=console,screen=True,auto_refresh=False,vertical_overflow='crop') as live:
            state=None
            while True:
                state=snapshot(a.run_dir,state);live.update(render(state,console.width,console.height),refresh=True)
                if not state.get('monitor_warning') and state['status'] in ('complete','failed','interrupted'):break
                sleep(1)
    except KeyboardInterrupt:pass
    return 0


def classify(query,start,end):
    inventory=source.inventory(query,start,end)
    if inventory['missing'] or inventory['incomplete']:
        raise ValueError('Reporting-only step cannot repair missing/incomplete canonical days: '+
                         str(list(map(str,(*inventory['missing'],*inventory['incomplete'])))))
    reporting=source.reporting_inventory(query,inventory)
    return inventory,reporting


def run_bars(a):
    from scripts import build_market_day as builder
    values=['--start-date',str(a.start_date),'--end-date',str(a.end_date),'--runtime',str(a.run_dir/'build'),
        '--env-file',str(a.env_file),'--workers',str(a.bar_workers),'--max-threads',str(a.query_threads),
        '--max-memory-gb',str(a.bar_query_memory_gib),'--global-memory-gb',str(a.global_memory_gib),
        '--query-timeout',str(a.query_timeout),'--tickers',a.tickers,'--progress',a.progress,
        '--products','bars-indicators' if a.with_indicators else 'bars']
    values.extend(['--identity-policy',a.identity_policy,'--max-plan-units',str(a.max_plan_units)])
    if a.conid_review_hash:values.extend(['--conid-review-hash',a.conid_review_hash])
    if a.identity_anchor_date:values.extend(['--identity-anchor-date',str(a.identity_anchor_date)])
    if a.allow_carried_forward_universe:values.append('--allow-carried-forward-universe')
    if a.plan_only:values.append('--plan-only')
    args=builder.parse_args(values)
    os.environ['QMD_CLICKHOUSE_URL']=a.endpoint
    a.run_dir.mkdir(parents=True,exist_ok=True)
    request=dict(step='bars',start=str(a.start_date),end=str(a.end_date),tickers=a.tickers,
        products=args.products,allow_carried_forward=a.allow_carried_forward_universe,endpoint=a.endpoint)
    if a.identity_policy!='dated':
        request.update(identity_policy=a.identity_policy,conid_review_hash=a.conid_review_hash,
            identity_anchor_date=str(a.identity_anchor_date))
    path=a.run_dir/'bars-request.json'
    if path.exists():
        if not a.resume and not a.plan_only:raise ValueError('Existing bars run requires --resume')
        if json.loads(path.read_text())!=request:raise ValueError('Bars resume scope differs from frozen request')
    elif a.resume:raise ValueError('No bars request exists to resume')
    elif not a.plan_only:path.write_text(json.dumps(request,indent=2),encoding='utf-8')
    if a.plan_only:return builder.run(args)
    progress=Progress(a.run_dir)
    progress.update(step='bars',title='Market bars and liquidity',status='running',start=str(a.start_date),end=str(a.end_date))
    progress.emit('Starting sequential bar funnel; liquidity 100ms -> 1s; trade bars '+str(builder.sql.FRAMES))
    finished=Event()
    def heartbeat():
        while not finished.wait(1):
            with progress.lock:progress.flush(force=True)
    thread=Thread(target=heartbeat,daemon=True);thread.start()
    try:
        result=builder.run(args,progress_sink=progress.update)
        progress.update(status='complete' if result==0 else 'interrupted' if result==130 else 'failed',failed=int(result not in (0,130)))
        report=a.run_dir/'build'/'latest.json'
        if result and report.exists():progress.emit(json.loads(report.read_text()).get('error','Build interrupted; published units remain resumable'))
        progress.emit('Builder finished with exit code '+str(result))
        return result
    except BaseException as error:
        progress.update(status='interrupted' if isinstance(error,KeyboardInterrupt) else 'failed',failed=int(not isinstance(error,KeyboardInterrupt)))
        progress.emit(type(error).__name__+': '+str(error));raise
    finally:
        finished.set();thread.join()


def main(argv=None):
    a=arguments(argv)
    if a.mode=='backfill' and a.step=='structural':
        from scripts.backfill_structural_sessions import arguments as structural_arguments, run
        values=['--start-date',str(a.start_date),'--run-dir',str(a.run_dir),
            '--bars-run-dir',str(a.bars_run_dir),'--env-file',str(a.env_file),'--endpoint',a.endpoint]
        if a.end_date:values+=['--end-date',str(a.end_date)]
        if a.plan_only:values.append('--plan-only')
        if a.resume:values.append('--resume')
        if a.migrate_structural_schema:values.append('--migrate-schema')
        return run(structural_arguments(values))
    if a.mode=='monitor':return monitor(a)
    runtime=Path('D:/TradingML/runtimes').resolve()
    if not runtime.is_dir() or not a.run_dir.resolve().is_relative_to(runtime):
        raise ValueError('Run directory must be under the available D:/TradingML/runtimes')
    if not a.plan_only and os.environ.get('COMPUTERNAME','').upper()!='DESKTOP-SAAI85T':
        raise ValueError('Execute this writer on DESKTOP-SAAI85T')
    if a.mode=='forwardfill':
        from scripts.market_data_forwardfill import run
        return run(a,run_bars)
    if a.step=='daily':
        from scripts.market_data_forwardfill import migrate_daily
        return migrate_daily(a)
    if a.step=='bars':return run_bars(a)
    paths=acquisition.IngestionPaths(a.env_file,a.run_dir,a.cache_root,a.archive_root,a.clickhouse_cache_root)
    load_env_files([a.env_file],verbose=False)
    os.environ['QMD_CLICKHOUSE_URL']=a.endpoint
    options=SimpleNamespace(env_file=a.env_file,max_threads=a.query_threads,
        max_memory_gb=a.query_memory_gib,query_timeout=a.query_timeout)
    with closing(Client(options,persistent=True,readonly=a.plan_only or a.benchmark_sql)) as client:
        if a.resume and a.end_date is None:
            a.end_date=date.fromisoformat(json.loads((a.run_dir/'request.json').read_text())['end'])
        end=a.end_date or date.fromisoformat(client.query(
            'SELECT toString(max(source_date)) day FROM market_sip_compact.events_source_day_stats FINAL')[0]['day'])
        inventory,reporting=classify(client.query,a.start_date,end)
        preflight(client,set(day.year for day in inventory['expected']))
        request=dict(version='trade-reporting-repair-v1',mode=a.mode,step=a.step,start=str(a.start_date),
            end=str(end),source_hash=inventory['content_hash'],endpoint=a.endpoint,
            archive=str(a.archive_root),cache=str(a.cache_root),clickhouse_cache=a.clickhouse_cache_root)
        if a.repair_engine=='compact-v2':request.update(version='trade-reporting-repair-v2',repair_engine=a.repair_engine)
        if a.benchmark_sql:request['benchmark_sql']=True
        pending=list(reporting['missing'])
        plan=dict(request,total=len(inventory['expected']),reused=len(reporting['verified']),
                  pending=list(map(str,pending)),active_mutation=reporting['mutating'],
                  unqualified_previous_receipts=reporting['unqualified'])
        if a.plan_only:
            print(json.dumps(plan,indent=2));return 0
        if a.resume:
            if json.loads((a.run_dir/'request.json').read_text())!=request:
                raise ValueError('Resume differs from frozen dates/source/paths')
        else:
            a.run_dir.mkdir(parents=True,exist_ok=False)
            (a.run_dir/'request.json').write_text(json.dumps(request,indent=2),encoding='utf-8')
        if (a.run_dir/'stop.request').exists():raise ValueError('Remove the owned stop.request before an intentional resume')
        paths.validate()
        progress=Progress(a.run_dir)
        progress.update(status='running',start=str(a.start_date),end=str(end),total=plan['total'],
            reused=plan['reused'],queued=len(pending),copy_workers=a.copy_workers,
            query_threads=a.query_threads,query_memory_gib=a.query_memory_gib,
            prefetch_gib=a.prefetch_gib,sql_workers=a.sql_workers,
            global_memory_gib=a.global_memory_gib,code_root=str(Path(__file__).resolve().parents[1]))
        stop=Event();finished=Event();active_preparation=[];prefetch=None
        def heartbeat():
            while not finished.wait(1):
                with progress.lock:progress.flush(force=True)
        thread=Thread(target=heartbeat,name='repair-heartbeat',daemon=True);thread.start()
        def telemetry():
            try:
                telemetry_options=SimpleNamespace(env_file=a.env_file,max_threads=1,max_memory_gb=1,query_timeout=15)
                with closing(Client(telemetry_options,persistent=True,readonly=True)) as observer:
                    while not finished.wait(5):
                        if not a.benchmark_sql:
                            parts=observer.query("SELECT table,result_part_name,progress,elapsed,memory_usage FROM system.merges "
                                "WHERE database='market_sip_compact' AND is_mutation ORDER BY result_part_name LIMIT 8")
                            progress.update(mutation_parts=parts)
                        active={'QUERY-1':client.active}
                        if active_preparation:
                            preparation=active_preparation[0]
                            with preparation.lock:
                                active.update({name:c.active for name,c in preparation.clients.items()})
                        active={name:q for name,q in active.items() if q}
                        if not active:
                            progress.worker('QUERY-1','Idle')
                            continue
                        rows=observer.query('SELECT query_id,elapsed,read_rows,read_bytes,memory_usage FROM system.processes WHERE query_id IN ('+
                                            ','.join(literal(q) for q in active.values())+')')
                        by_id={row['query_id']:row for row in rows}
                        for name,query_id in active.items():
                            row=by_id.get(query_id)
                            detail=(f'{int(row["read_rows"]):,} rows / {int(row["read_bytes"])/GIB:.2f} GiB / '+
                                    f'{float(row["elapsed"]):.0f}s / RAM {int(row["memory_usage"])/GIB:.2f} GiB') if row else ''
                            if name=='QUERY-1':progress.worker(name,'ClickHouse' if row else 'Idle',detail=detail)
                            else:
                                with progress.lock:
                                    if name in progress.state['workers']:
                                        progress.state['workers'][name]['query_detail']=detail
                                        progress.flush()
            except Exception as error:
                progress.emit('Query telemetry unavailable: '+type(error).__name__)
        observer_thread=Thread(target=telemetry,name='repair-query-progress',daemon=True);observer_thread.start()
        old_signal=signal.signal(signal.SIGINT,lambda *unused:stop.set())
        def emit(message):
            progress.worker('MUTATE','Waiting on mutation' if 'mutating' in message else 'Verify / update',
                            detail=message,kind='mutation')
            progress.emit(message)
        try:
            if a.benchmark_sql:
                from pipelines.market_sip.flatfiles.repair_benchmark import run
                with acquisition.stop_marker(stop,a.run_dir/'stop.request'):
                    # ClickHouse forbids file() even in SELECT under readonly=1.
                    # This bounded benchmark generates SELECT statements only.
                    report=run(tuple(inventory['expected']),paths,inventory,
                               lambda:Client(options,persistent=True),progress,a.sql_workers,stop)
                progress.emit(f'Benchmark complete | classification speedup {report["speedup"]:.2f}x | see benchmark.json')
                progress.update(status='complete')
                return 0
            with acquisition.stop_marker(stop,a.run_dir/'stop.request'),closing(open_workstation_keeper_session()) as keeper,closing(SourceClaim(keeper.client)) as claim:
                if a.repair_engine=='compact-v2':
                    from pipelines.market_sip.flatfiles.compact_reporting_repair import run
                    run(client.query,inventory,reporting['verified'],paths,claim,stop,progress,
                        lambda:Client(options,persistent=True),a,active_preparation)
                    progress.update(status='complete',queued=0)
                    return 0
                # Discover capture receipts before staging; completed days never open CSVs.
                has=client.query("SELECT count() n FROM system.tables WHERE database='q_live' AND name="+literal(retained_reporting.RECEIPTS))[0]['n']
                captured=client.query('SELECT source_date,source_digest FROM q_live.'+retained_reporting.RECEIPTS+
                    f' WHERE source_date BETWEEN toDate({literal(a.start_date)}) AND toDate({literal(end)})') if has else []
                captured_days=set()
                for row in captured:
                    day=date.fromisoformat(str(row['source_date']))
                    if day in captured_days or row['source_digest']!=inventory['source_hashes'].get(str(day)):
                        raise ValueError('Ambiguous or changed retained source capture')
                    captured_days.add(day)
                needed=[day for day in pending if day not in captured_days]
                file_plan=retained_reporting.file_plan(paths,needed,reserve_bytes=a.reserve_gib*GIB,
                    reserve_fraction=a.reserve_fraction,maximum_chunk_bytes=a.prefetch_gib*GIB)
                if file_plan['missing']:raise ValueError('Required retained CSV missing: '+str(file_plan['missing']))
                copies=[(day,info) for day,info in sorted(file_plan['files'].items()) if info['origin']==1]
                progress.emit(f'Trade Reporting Repair | {plan["total"]} days | {plan["reused"]} certified reused | {len(pending)} pending | {len(copies)} G files | no downloads')
                months={m:tuple(d for d in pending if (d.year,d.month)==m)
                        for m in sorted({(d.year,d.month) for d in pending})}
                with Prefetch(copies,paths,stop,progress,workers=a.copy_workers,max_bytes=a.prefetch_gib*GIB,
                              reserve_bytes=a.reserve_gib*GIB,reserve_fraction=a.reserve_fraction) as prefetch, \
                     Preparation(months,inventory,captured_days.intersection(pending),paths,claim,stop,progress,
                         lambda:Client(options,persistent=True),prefetch,workers=a.sql_workers,
                         query_threads=a.query_threads,query_memory_bytes=a.query_memory_gib*GIB,
                         reserve_bytes=a.reserve_gib*GIB,reserve_fraction=a.reserve_fraction,
                         prefetch_bytes=a.prefetch_gib*GIB) as preparation:
                    active_preparation.append(preparation)
                    acquisition.resume_native_repairs(client.query,client.query,inventory,claim,stop,emit=emit,defer_unsubmitted=True)
                    current,reporting=classify(client.query,a.start_date,end)
                    if current['content_hash']!=inventory['content_hash']:raise ValueError('Canonical sources changed during resume')
                    if reporting['mutating']:raise ValueError('Unresolved canonical mutation blocks new reporting repair')
                    remaining=list(reporting['missing'])
                    progress.update(completed=plan['total']-plan['reused']-len(remaining),queued=len(remaining))
                    for month in months:
                        preparation.wait(month)
                        if stop.is_set():raise InterruptedError('Trade Reporting Repair stopped')
                        days=tuple(d for d in remaining if (d.year,d.month)==month)
                        if days:repair_month(client.query,days,inventory,claim,stop,emit)
                        checked=source.reporting_inventory(client.query,inventory)
                        if any(d in checked['missing'] for d in days):raise ValueError('Monthly repair did not certify every selected day')
                        progress.update(completed=plan['total']-plan['reused']-len(checked['missing']),queued=len(checked['missing']))
                        preparation.release(month)
                    final=source.reporting_inventory(client.query,inventory)
                    if final['missing'] or final['mutating']:raise ValueError('Reporting repair remains incomplete')
                    source.require_ready(client.query,inventory,final)
                    (a.run_dir/'completion.json').write_text(json.dumps(dict(request_hash=digest(request),
                        reporting_hash=final['content_hash'],days=plan['total'],completed_at=datetime.now(timezone.utc).isoformat()),indent=2))
                progress.update(status='complete',queued=0,completed=plan['total']-plan['reused'])
                return 0
        except BaseException as error:
            if active_preparation and active_preparation[0].error:
                error=active_preparation[0].error
            if prefetch is not None and prefetch.error:
                error=prefetch.error
            interrupted=isinstance(error,(InterruptedError,KeyboardInterrupt))
            progress.update(status='interrupted' if interrupted else 'failed',failed=0 if interrupted else 1)
            progress.emit(type(error).__name__+': '+str(error))
            if interrupted:return 130
            raise error
        finally:
            stop.set();finished.set();thread.join();observer_thread.join();signal.signal(signal.SIGINT,old_signal)


if __name__=='__main__':raise SystemExit(main())
