"""Global market-data launcher. Step 1: Trade Reporting Repair, workstation only.

Backfill repairs reporting bits from retained trade CSV evidence. Forwardfill is
reserved but fails closed until its approved ingestion stage is implemented.
No bars, levels, downloads, or original-tape expansion run in this step.
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
    p.add_argument('--step',choices=('trade-reporting-repair',),default='trade-reporting-repair')
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
    p.add_argument('--global-memory-gib',type=int,default=32)
    p.add_argument('--prefetch-gib',type=int,default=32)
    p.add_argument('--reserve-gib',type=int,default=256)
    p.add_argument('--reserve-fraction',type=float,default=.10)
    p.add_argument('--query-threads',type=int,choices=range(1,9),default=4)
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
    if a.mode=='forwardfill':p.error('Forwardfill ingestion is not implemented in this step; no action taken')
    if a.mode=='backfill' and (a.start_date is None or (a.end_date and a.end_date<a.start_date)):
        p.error('Backfill requires --start-date and an ordered inclusive range')
    if not 1<=a.prefetch_gib<=256 or not 0<=a.reserve_gib or not 0<=a.reserve_fraction<1:
        p.error('Invalid SSD staging budget')
    if not 1<=a.query_memory_gib<=32 or not 1<=a.query_timeout<=3600:p.error('Invalid query budget')
    if (a.sql_workers+1)*a.query_memory_gib+1>a.global_memory_gib:
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
        with Live(console=console,refresh_per_second=1,vertical_overflow='crop') as live:
            state=None
            while True:
                state=snapshot(a.run_dir,state);live.update(render(state,console.width,console.height))
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


def main(argv=None):
    a=arguments(argv)
    if a.mode=='monitor':return monitor(a)
    runtime=Path('D:/TradingML/runtimes').resolve()
    if not runtime.is_dir() or not a.run_dir.resolve().is_relative_to(runtime):
        raise ValueError('Run directory must be under the available D:/TradingML/runtimes')
    if not a.plan_only and os.environ.get('COMPUTERNAME','').upper()!='DESKTOP-SAAI85T':
        raise ValueError('Execute this writer on DESKTOP-SAAI85T')
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
