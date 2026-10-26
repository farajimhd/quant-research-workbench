"""Receipt-preserving exact quote extension of a completed trade-bar campaign."""
import os
os.environ['PYTHONDONTWRITEBYTECODE']='1'
import sys
sys.dont_write_bytecode=True
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import json,sqlite3,time,uuid,threading,signal
from contextlib import closing
from concurrent.futures import ThreadPoolExecutor,wait,FIRST_COMPLETED
from datetime import date,datetime,timezone
from scripts import build_market_day as bars
from pipelines.market_sip.events import quote_bars as sql
from pipelines.market_sip.events import market_signals as signals
from pipelines.market_sip.flatfiles.repair_progress import Progress


def run(a):
    root=a.run_dir.resolve();runtime=bars.RUNTIME.resolve()
    if not root.is_relative_to(runtime) or not runtime.is_dir():raise ValueError('Operational runtime required')
    os.environ['QMD_CLICKHOUSE_URL']=a.endpoint
    options=bars.parse_args(['--start-date',str(a.start_date),'--end-date',str(a.end_date),
        '--env-file',str(a.env_file),'--max-threads','1','--max-memory-gb',str(a.bar_query_memory_gib),
        '--global-memory-gb',str(a.global_memory_gib),'--workers',str(a.bar_workers),
        '--query-timeout',str(a.query_timeout)])
    parent=a.bars_run_dir/'build'/'latest.json'
    if not parent.is_file():raise ValueError('Completed parent trade campaign required')
    report=json.loads(parent.read_text(encoding='utf-8'))
    if report['status']!='core_complete':raise ValueError('Parent trade campaign is not complete')
    parent_build=report['build_id'];definition=report['definition'];plan=definition['plan']
    requested=set(x.strip() for x in a.tickers.split(',') if x.strip())
    units=[r for r in plan['units'] if str(a.start_date)<=r['source_date']<=str(a.end_date)
           and (not requested or r['ticker'] in requested)]
    profile_units=getattr(a,'profile_units',0)
    if profile_units:
        if not 4<=profile_units<=128:raise ValueError('Quote profile must use 4..128 units')
        days=sorted({r['source_date'] for r in units});selected=[]
        for day in sorted({days[0],days[-1]}):
            rows=sorted([r for r in units if r['source_date']==day],key=lambda r:int(r['event_count']))
            quota=min(len(rows),max(2,profile_units//2))
            selected.extend(rows[round(i*(len(rows)-1)/(quota-1))] for i in range(quota))
        units=selected
    if not units or len(units)>a.max_plan_units:raise ValueError('Empty or oversized quote plan')
    parent_db=sqlite3.connect((a.bars_run_dir/'build-ledger-v2.sqlite3').as_uri()+'?mode=ro',uri=True)
    try:
        restriction=(' AND ticker IN ('+','.join('?' for _ in requested)+')') if requested else ''
        certified={(d,t):(h,n,oh,attempt) for d,t,h,n,oh,attempt in parent_db.execute(
            "SELECT session_date,ticker,source_hash,output_rows,output_hash,attempt_id FROM units WHERE build_id=? AND stage='bars' AND status='complete' AND session_date BETWEEN ? AND ?"+restriction,
            (parent_build,str(a.start_date),str(a.end_date),*requested))}
    finally:parent_db.close()
    if any((r['source_date'],r['ticker']) not in certified for r in units):raise ValueError('Parent trade receipt missing')
    policy=sql.recipe(parent_build)
    build=policy
    request=dict(version=sql.VERSION,policy_hash=policy,parent_build=parent_build,
        start=str(a.start_date),end=str(a.end_date),tickers=sorted(requested),units=len(units),
        selected_scope_hash=bars.digest(units),frames=sql.FRAMES)
    request['signals_version']=signals.VERSION
    with closing(bars.Client(options,persistent=False)) as c:
        sql.verify_storage(c.query,'arte',['quote_bars_v1','quote_bars_coverage_v1'],required=False)
        reporting=bars.reporting_coverage(c,a.start_date,a.end_date)
        free=c.query("SELECT free_space FROM system.disks WHERE name='live_market_ssd'",'quote_storage_reserve')
        if len(free)!=1 or int(free[0]['free_space'])<int(a.reserve_gib*1024**3):raise ValueError('Quote SSD reserve is unavailable')
        token_rows=c.query('SELECT * FROM market_sip_compact.event_condition_token_reference ORDER BY token_id','signal_dictionary')
        signals.token_sets(token_rows)
        request['signal_dictionary_hash']=bars.digest(token_rows)
        # Immutable recipe and reporting evidence are part of this campaign.
        request['reporting_hash']=bars.digest(reporting)
        if a.plan_only:
            root.mkdir(parents=True,exist_ok=True);bars.save(root/'quote-plan.json',request)
            print(json.dumps(request));return 0
    if os.environ.get('COMPUTERNAME','').upper()!='DESKTOP-SAAI85T':raise ValueError('Workstation writer required')
    root.mkdir(parents=True,exist_ok=True)
    stop=threading.Event();mutex=threading.RLock();started=time.monotonic()
    with bars.build_lock(runtime/'market-quotes-arte.lock'),bars.build_lock(root/'controller.lock'):
        path=root/'quote-request.json'
        if path.exists():
            if not a.resume or json.loads(path.read_text(encoding='utf-8'))!=json.loads(json.dumps(request)):
                raise ValueError('Quote resume must preserve immutable request')
        elif a.resume:raise ValueError('No quote campaign to resume')
        else:bars.save(path,request)
        if (root/'stop.request').exists():raise ValueError('Stop marker retained; diagnose and drain before resume')
        with closing(bars.Client(options,persistent=False)) as c:
            for query in sql.ddl():c.query(query,'quote_schema',False)
            for query in signals.ddl():c.query(query,'signal_schema',False)
            sql.verify_storage(c.query,'arte',['quote_bars_v1','quote_bars_coverage_v1'])
            sql.verify_storage(c.query,'arte',list(signals.TABLES))
            columns=c.query("SELECT name,type FROM system.columns WHERE database='arte' AND table='quote_bars_v1' ORDER BY position")
            if [x['name'] for x in columns]!=list(sql.FIELDS):raise ValueError('Quote schema differs')
        progress=Progress(root);progress.update(step='quotes',title='Exact bid/ask OHLC',status='running',
            total=len(units),completed=0,reused=0,queued=len(units),failed=0,coverage_label='Certified ticker-days',
            products='1s -> 5s -> 10s -> 30s',
            signals='Estimated LULD from 1s trade closes; observed halt transitions; unknown halt state stays explicit')
        import pandas_market_calendars as mcal
        schedule=mcal.get_calendar('XNYS').schedule(start_date=a.start_date,end_date=a.end_date)
        session_times={str(stamp.date()):(int(r.market_open.timestamp()*1e6),int(r.market_close.timestamp()*1e6)) for stamp,r in schedule.iterrows()}
        old_signal=signal.signal(signal.SIGINT,lambda *_:stop.set())
        counts=dict(completed=0,reused=0,failed=0);profiles={};active_clients=[]
        def guard():
            if stop.is_set() or (root/'stop.request').exists():raise InterruptedError('Graceful quote stop requested')
        def work(row,prior):
            day=row['source_date'];ticker=row['ticker'];key=(day,ticker);source_hash=certified[key][0]
            client=bars.Client(options);stage='quote_stage_'+uuid.uuid4().hex;attempt=str(uuid.uuid4());began=time.monotonic()
            with mutex:active_clients.append(client)
            name=threading.current_thread().name
            try:
                guard();progress.worker(name,'Reuse receipt' if prior else 'Canonical events -> exact 1s',day=day,detail=ticker)
                if prior:
                    if prior['source_hash']!=source_hash or prior['policy_hash']!=policy:raise ValueError('Published quote source/recipe differs')
                    e=client.query(sql.evidence('arte',build,day,ticker,prior['attempt_id']),'quote_reuse_readback')[0]
                    if (int(e['n']),e['hash'],int(e['bad']),int(e['unique_keys']))!=(int(prior['row_count']),prior['content_hash'],0,int(prior['row_count'])):raise ValueError('Completed quote readback differs')
                    progress.worker(name,'Estimated bands / halt transitions',day=day,detail=ticker)
                    signals.publish(client,'arte',parent_build,day,ticker,certified[key][3],source_hash,token_rows,*session_times[day])
                    with mutex:counts['reused']+=1
                    return
                client.query(sql.stage_sql('arte',stage,day,ticker),'quote_canonical_scan',False)
                source=bars.checked_source_evidence(row,client.query(sql.stage_evidence('arte',stage),'quote_source_fingerprint')[0])
                if bars.digest([source,plan['rules'],definition['version']])!=source_hash:
                    raise ValueError('Canonical content differs from completed trade source')
                guard();progress.worker(name,'Persist 1s / roll up 5s, 10s, 30s',day=day,detail=ticker)
                client.query(sql.base_insert('arte',stage,build,day,ticker,attempt),'quote_1s',False)
                for parent,target in zip(sql.FRAMES,sql.FRAMES[1:]):
                    guard();client.query('INSERT INTO arte.quote_bars_v1 '+sql.rollup_select('arte',build,day,ticker,attempt,parent,target),'quote_rollup',False)
                    if client.query(sql.parity('arte',build,day,ticker,attempt,parent,target),'quote_rollup_parity')[0]['n']:
                        raise ValueError('Quote rollup differs from exact parent OHLC')
                e=client.query(sql.evidence('arte',build,day,ticker,attempt),'quote_final_readback')[0]
                if int(e['bad']) or e['n']!=e['unique_keys']:raise ValueError('Quote key/value integrity failure')
                expected=client.query(f'SELECT sum(quote_count) events,countIf(quote_count>0) buckets FROM arte.{stage}','quote_stage_totals')[0]
                actual=client.query(f"SELECT resolution_ms,side,sum(event_count) events,count() buckets FROM arte.quote_bars_v1 WHERE {sql.scope(build,day,ticker,attempt)} GROUP BY resolution_ms,side",'quote_conservation')
                if int(expected['events']):
                    if len(actual)!=8 or any(int(x['events'])!=int(expected['events']) for x in actual):raise ValueError('Quote event conservation differs')
                    if any(int(x['buckets'])!=int(expected['buckets']) for x in actual if x['resolution_ms']==1000):raise ValueError('Quote 1s bucket completeness differs')
                elif actual:raise ValueError('Unexpected bars in zero-quote unit')
                sql.verify_storage(client.query,'arte',['quote_bars_v1','quote_bars_coverage_v1',stage])
                guard();client.query(f"INSERT INTO arte.quote_bars_coverage_v1 VALUES ({sql.L(build)},toDate({sql.L(day)}),{sql.L(ticker)},toUUID({sql.L(attempt)}),{sql.L(source_hash)},{sql.L(policy)},{int(e['n'])},{sql.L(e['hash'])},now64(6))",'quote_commit',False)
                committed=client.query(f"SELECT attempt_id,row_count,content_hash FROM arte.quote_bars_coverage_v1 FINAL WHERE {sql.scope(build,day,ticker)}",'quote_commit_readback')
                if committed!=[dict(attempt_id=attempt,row_count=e['n'],content_hash=e['hash'])]:raise ValueError('Quote commit receipt readback differs')
                progress.worker(name,'Estimated bands / halt transitions',day=day,detail=ticker)
                signal_result=signals.publish(client,'arte',parent_build,day,ticker,certified[key][3],source_hash,token_rows,*session_times[day])
                with mutex:
                    counts['completed']+=1
                    with (root/'profiles.jsonl').open('a',encoding='utf-8') as f:
                        f.write(json.dumps(dict(day=day,ticker=ticker,seconds=time.monotonic()-began,source_events=row['event_count'],quote_events=expected['events'],rows=e['n'],signals=signal_result,profiles=client.profiles,totals=client.profile_totals))+'\n')
            except BaseException:
                with mutex:counts['failed']+=1
                stop.set();raise
            finally:
                try:client.query(f'DROP TABLE IF EXISTS arte.{stage} SYNC','quote_stage_cleanup',False)
                finally:
                    with mutex:
                        profiles[name]=client.profile_totals;active_clients.remove(client)
                        done=counts['completed']+counts['reused'];elapsed=time.monotonic()-started
                        progress.update(**counts,queued=max(0,len(units)-done-counts['failed']),
                            elapsed=elapsed,ticker_days_per_hour=done*3600/elapsed,
                            new_ticker_days_per_hour=counts['completed']*3600/elapsed,
                            eta_seconds=(len(units)-done)*elapsed/counts['completed'] if counts['completed']>=max(3,a.bar_workers) else None)
                    progress.worker(name,'Idle');client.close()
        try:
            by_day={}
            for r in units:by_day.setdefault(r['source_date'],[]).append(r)
            with ThreadPoolExecutor(max_workers=a.bar_workers,thread_name_prefix='QUOTE') as pool:
                for day,rows in sorted(by_day.items()):
                    guard()
                    with closing(bars.Client(options,persistent=False)) as c:
                        free=c.query("SELECT free_space FROM system.disks WHERE name='live_market_ssd'",'quote_storage_reserve')
                        if len(free)!=1 or int(free[0]['free_space'])<int(a.reserve_gib*1024**3):raise ValueError('Quote SSD reserve reached; preserve receipts before resume')
                        prior=c.query(f"SELECT * FROM arte.quote_bars_coverage_v1 FINAL WHERE build_id={sql.L(build)} AND session_date=toDate({sql.L(day)})",'quote_receipt_prefetch')
                    saved={x['ticker']:x for x in prior};iterator=iter(rows);pending=set()
                    while True:
                        while len(pending)<2*a.bar_workers and not stop.is_set():
                            r=next(iterator,None)
                            if r is None:break
                            pending.add(pool.submit(work,r,saved.get(r['ticker'])))
                        if not pending:break
                        finished,pending=wait(pending,timeout=1,return_when=FIRST_COMPLETED)
                        for f in finished:f.result()
                        with progress.lock:progress.flush(force=True)
                    guard()
            bars.save(root/'quote-completion.json',dict(request=request,counts=counts,seconds=time.monotonic()-started,
                profiles=profiles,luld_ready=True,halt_ready=True,official_bands=False,halt_state='unknown until explicit source status'))
            progress.update(status='complete',queued=0);return 0
        except BaseException as error:
            stop.set();progress.update(status='interrupted' if isinstance(error,(InterruptedError,KeyboardInterrupt)) else 'failed',message=str(error))
            (root/'stop.request').write_text('Preserve quote receipts. Diagnose and drain before deliberate resume.\n',encoding='utf-8')
            raise
        finally:signal.signal(signal.SIGINT,old_signal)
