"""Bounded read-only comparison of the actual retained CSV classification SQL."""
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from datetime import datetime,timezone
import json
from pathlib import Path
from time import monotonic

from . import download_update_events as updater
from .download_massive_sip_flatfiles import DownloadJob


def run(days,paths,inventory,client_factory,progress,workers=2,stop=None):
    if len(days)!=2 or workers!=2:
        raise ValueError('The approved benchmark requires exactly two source days and two SQL workers')
    inputs=[]
    for day in days:
        key=Path('trades_v1')/str(day.year)/f'{day.month:02d}'/(str(day)+'.csv.gz')
        path=paths.cache/key
        if not path.is_file():path=paths.cache/'_retained_reporting'/key
        if not path.is_file():raise ValueError('Benchmark requires an already staged SSD CSV: '+str(day))
        stamp=path.stat()
        args=updater.parse_args(['--start-date',str(day),'--end-date',str(day),'--skip-bars'])
        args.flatfiles_root_win=paths.cache;args.flatfiles_root_ch=paths.clickhouse_cache
        job=DownloadJob('trades',str(day),key.as_posix(),str(path),stamp.st_size)
        raw=updater.raw_event_union_sql(args,updater.DayFiles(str(day),job,job),trade_only=True,
            include_reporting_reason=True,include_reporting_evidence=True,include_listing_tape=True)
        sql=('SELECT count() n,sum(length(ticker)+128+arraySum(arrayMap(x -> length(x)+9,reporting_condition_entries))) bytes,'
             'countIf(bitAnd(event_meta,192)=192) delayed,countIf(bitAnd(event_meta,192)=0) unknown '
             f'FROM ({raw})')
        inputs.append((day,path,(stamp.st_size,stamp.st_mtime_ns),sql))
    def execute(item):
        if stop is not None and stop.is_set():raise InterruptedError('Benchmark stopped')
        day,path,stamp,sql=item
        from threading import current_thread
        name=current_thread().name
        progress.worker(name,'Benchmark CSV',day=day,detail='Read-only condition and original-clock classification')
        start=monotonic()
        with closing(client_factory()) as client:
            rows=client.query(sql)
            query_id=client.profiles[-1]['query_id']
        if stop is not None and stop.is_set():raise InterruptedError('Benchmark stopped')
        after=path.stat()
        if (after.st_size,after.st_mtime_ns)!=stamp:raise ValueError('Benchmark input changed')
        if len(rows)!=1 or int(rows[0]['n'])!=int(inventory['rows'][day]['trade_event_rows']):
            raise ValueError('Benchmark count differs from certified canonical source')
        elapsed=monotonic()-start
        progress.worker(name,'Benchmark done',day=day,detail=f'{int(rows[0]["n"]):,} rows in {elapsed:.2f}s')
        return dict(day=str(day),seconds=elapsed,query_id=query_id,result=rows[0])
    measurements=[]
    for count in (1,workers):
        progress.emit(f'Benchmark | SQL workers={count} | same two SSD files | classification only')
        start=monotonic()
        with ThreadPoolExecutor(max_workers=count,thread_name_prefix='SQL') as pool:
            results=list(pool.map(execute,inputs))
        measurements.append(dict(workers=count,seconds=monotonic()-start,days=results))
    if [d['result'] for d in measurements[0]['days']]!=[d['result'] for d in measurements[1]['days']]:
        raise ValueError('Serial and concurrent classification results differ')
    report=dict(created_at=datetime.now(timezone.utc).isoformat(),measurements=measurements,
                speedup=measurements[0]['seconds']/measurements[1]['seconds'],
                limitation='Serial then concurrent; cache warming can favor the second run. Classification only, not end-to-end repair.')
    (paths.runtime/'benchmark.json').write_text(json.dumps(report,indent=2),encoding='utf-8')
    return report
