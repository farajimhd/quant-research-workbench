"""Receipt-preserving exact quote extension of a completed trade-bar campaign."""
import os
os.environ['PYTHONDONTWRITEBYTECODE']='1'
import sys
sys.dont_write_bytecode=True
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import json,sqlite3,time,uuid,threading,signal,gc,http.client
from contextlib import closing
from concurrent.futures import ThreadPoolExecutor,wait,FIRST_COMPLETED,as_completed
from datetime import date,datetime,timezone
from scripts import build_market_day as bars
from pipelines.market_sip.events import quote_bars as sql
from pipelines.market_sip.events import market_signals as signals
from pipelines.market_sip.events import quote_execution as execution
from pipelines.market_sip.flatfiles.repair_progress import Progress


class QuoteProgress(Progress):
    """Coalesce worker display writes; database commit receipts stay durable."""
    def flush(self,force=False):
        with self.lock:
            terminal=self.state.get('status') in ('complete','failed','interrupted')
            return super().flush(force=terminal)


class RetryUnit(Exception):
    """Previous query canceled; owned staging drained; recheck commit first."""


def retryable_transport(error):
    if any('Cancellation could not be confirmed' in n for n in getattr(error,'__notes__',())):
        return False
    return isinstance(error,(ConnectionError,TimeoutError,http.client.RemoteDisconnected))


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
    # The million-unit parent is immutable invocation context. Keep it out of
    # repeated cyclic-GC sweeps; fresh worker objects still use normal GC.
    gc.freeze()
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
        progress=QuoteProgress(root);progress.update(step='quotes',title='Exact bid/ask OHLC',status='running',
            total=len(units),completed=0,reused=0,queued=len(units),failed=0,coverage_label='Certified ticker-days',
            products='1s -> 5s -> 10s -> 30s',
            signals='Estimated LULD from 1s trade closes; observed halt transitions; unknown halt state stays explicit')
        import pandas_market_calendars as mcal
        schedule=mcal.get_calendar('XNYS').schedule(start_date=a.start_date,end_date=a.end_date)
        session_times={str(stamp.date()):(int(r.market_open.timestamp()*1e6),int(r.market_close.timestamp()*1e6)) for stamp,r in schedule.iterrows()}
        old_signal=signal.signal(signal.SIGINT,lambda *_:stop.set())
        counts=dict(completed=0,reused=0,failed=0);profiles={};active_clients=[];verification_seconds=0.
        worker_local=threading.local();worker_clients=[]
        profile_stream=(root/'profiles.jsonl').open('a',encoding='utf-8',buffering=1024*1024)
        batch_stream=(root/'batch-profiles.jsonl').open('a',encoding='utf-8',buffering=1024*1024)
        last_profile_flush=time.monotonic()
        def guard():
            if stop.is_set() or (root/'stop.request').exists():raise InterruptedError('Graceful quote stop requested')
        def work(row,prior,retry=0,shared_stage=None,prepared=None):
            day=row['source_date'];ticker=row['ticker'];key=(day,ticker);source_hash=certified[key][0]
            client=getattr(worker_local,'client',None)
            if client is None:
                client=bars.Client(options);worker_local.client=client
                with mutex:worker_clients.append(client)
            client.profiles=[];client.profile_totals={}
            stage=shared_stage or 'quote_stage_'+uuid.uuid4().hex;attempt=prepared['attempt'] if prepared else str(uuid.uuid4());began=time.monotonic()
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
                if shared_stage is None:client.query(sql.stage_sql('arte',stage,day,ticker),'quote_canonical_scan',False)
                source=prepared['source'] if prepared else bars.checked_source_evidence(row,client.query(sql.stage_evidence('arte',stage)+(f' WHERE ticker={sql.L(ticker)}' if shared_stage else ''),'quote_source_fingerprint')[0])
                if bars.digest([source,plan['rules'],definition['version']])!=source_hash:
                    raise ValueError('Canonical content differs from completed trade source')
                guard();progress.worker(name,'Verify batched quote funnel' if prepared else 'Persist 1s / roll up 5s, 10s, 30s',day=day,detail=ticker)
                if not prepared:client.query(sql.base_insert('arte',stage,build,day,ticker,attempt)+(f' AND ticker={sql.L(ticker)}' if shared_stage else ''),'quote_1s',False)
                for parent,target in zip(sql.FRAMES,sql.FRAMES[1:]):
                    guard()
                    if not prepared:client.query('INSERT INTO arte.quote_bars_v1 '+sql.rollup_select('arte',build,day,ticker,attempt,parent,target),'quote_rollup',False)
                    if client.query(sql.parity('arte',build,day,ticker,attempt,parent,target),'quote_rollup_parity')[0]['n']:
                        raise ValueError('Quote rollup differs from exact parent OHLC')
                e=client.query(sql.evidence('arte',build,day,ticker,attempt),'quote_final_readback')[0]
                if int(e['bad']) or e['n']!=e['unique_keys']:raise ValueError('Quote key/value integrity failure')
                expected=client.query(f'SELECT sum(quote_count) events,countIf(quote_count>0) buckets FROM arte.{stage}{f" WHERE ticker={sql.L(ticker)}" if shared_stage else ""}','quote_stage_totals')[0]
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
                    record=dict(day=day,ticker=ticker,seconds=time.monotonic()-began,
                        source_events=row['event_count'],quote_events=expected['events'],rows=e['n'],signals=signal_result,
                        stage_seconds={k:v['seconds'] for k,v in client.profile_totals.items()})
                    if profile_units or counts['completed']<=128 or counts['completed']%1000==0 or int(row['event_count'])>=4000000:
                        record.update(profiles=client.profiles,totals=client.profile_totals)
                    profile_stream.write(json.dumps(record)+'\n')
            except BaseException as error:
                if retryable_transport(error) and retry<2:
                    with mutex:
                        counts['retried']=counts.get('retried',0)+1
                        with (root/'transport-retries.jsonl').open('a',encoding='utf-8') as f:
                            f.write(json.dumps(dict(day=day,ticker=ticker,retry=retry+1,
                                error=client.clean_error(error),last_query=client.profiles[-1] if client.profiles else None))+'\n')
                    progress.emit(f'{day} {ticker}: transport interrupted; reconcile receipt before bounded retry {retry+1}/2')
                    client.close();worker_local.client=None
                    raise RetryUnit() from error
                with mutex:
                    if isinstance(error,(InterruptedError,KeyboardInterrupt)):
                        counts['interrupted']=counts.get('interrupted',0)+1
                    else:counts['failed']+=1
                stop.set();raise
            finally:
                try:
                    try:
                        if shared_stage is None and not prior:client.query(f'DROP TABLE IF EXISTS arte.{stage} SYNC','quote_stage_cleanup',False)
                    except BaseException as error:
                        if not retryable_transport(error):raise
                        with closing(bars.Client(options,persistent=False)) as clean:
                            clean.query(f'DROP TABLE IF EXISTS arte.{stage} SYNC','quote_stage_cleanup_reconnect',False)
                finally:
                    with mutex:
                        profiles[name]=client.profile_totals;active_clients.remove(client)
                        done=counts['completed']+counts['reused'];elapsed=time.monotonic()-started;productive=max(.001,elapsed-verification_seconds)
                        progress.update(**counts,queued=max(0,len(units)-done-counts['failed']),
                            elapsed=elapsed,ticker_days_per_hour=done*3600/elapsed,
                            new_ticker_days_per_hour=counts['completed']*3600/productive,
                            eta_seconds=(len(units)-done)*productive/counts['completed'] if counts['completed']>=max(3,a.bar_workers) else None)
                    progress.worker(name,'Idle')
        def resilient_work(row,prior,shared_stage=None,prepared=None):
            for retry in range(3):
                if retry:
                    guard()
                    with closing(bars.Client(options,persistent=False)) as c:
                        saved=c.query(f"SELECT * FROM arte.quote_bars_coverage_v1 FINAL WHERE {sql.scope(build,row['source_date'],row['ticker'])}",'quote_retry_commit_reconciliation')
                    if len(saved)>1:raise ValueError('Ambiguous quote retry receipt')
                    prior=saved[0] if saved else None
                try:return work(row,prior,retry,shared_stage,prepared)
                except RetryUnit:time.sleep(.25*(retry+1))
        def verify_prior(day,rows,saved):
            guard();name=threading.current_thread().name
            progress.worker(name,'Verify completed receipts',day=day,detail=f'{len(rows)} ticker-days')
            try:
                with closing(bars.Client(options)) as c:
                    return execution.verify_completed(c,'arte',build,parent_build,day,rows,saved,certified,token_rows,policy,guard=guard)
            finally:progress.worker(name,'Idle')
        def batch_work(rows,saved):
            day=rows[0]['source_date'];stage='quote_stage_'+uuid.uuid4().hex
            fresh=[r for r in rows if r['ticker'] not in saved]
            with closing(bars.Client(options)) as c:
                began=time.monotonic()
                try:
                    guard()
                    attempts={r['ticker']:str(uuid.uuid4()) for r in fresh};prepared_units={}
                    progress.worker(threading.current_thread().name,'Canonical scan / quote batch',day=day,detail=f'{len(fresh)} ticker-days')
                    if fresh:
                        c.query(execution.stage_sql('arte',stage,day,list(attempts)),'quote_batched_canonical_scan',False)
                        # Validate each source identity before any batched output.
                        for row in fresh:
                            t=row['ticker'];e=c.query(sql.stage_evidence('arte',stage)+f' WHERE ticker={sql.L(t)}','quote_batch_source_fingerprint')[0]
                            source=bars.checked_source_evidence(row,e)
                            prepared_units[t]=dict(attempt=attempts[t],source=source)
                            if bars.digest([source,plan['rules'],definition['version']])!=certified[(day,t)][0]:raise ValueError('Canonical content differs from completed trade source')
                        guard();execution.insert_batch(c,'arte',stage,build,day,attempts,guard=guard)
                    for row in rows:resilient_work(row,saved.get(row['ticker']),stage if fresh else None,prepared_units.get(row['ticker']))
                finally:
                    if fresh:c.query(f'DROP TABLE IF EXISTS arte.{stage} SYNC','quote_batch_cleanup',False)
                    with mutex:
                        batch_stream.write(json.dumps(dict(day=day,tickers=[r['ticker'] for r in rows],
                            seconds=time.monotonic()-began,source_events=sum(int(r['event_count']) for r in rows),
                            totals=c.profile_totals))+'\n')
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
                    saved={x['ticker']:x for x in prior}
                    # Full row/hash verification in bounded grouped reads, not one
                    # network conversation per already completed ticker-day.
                    verification_started=time.monotonic()
                    progress.update(phase='receipt_verification',message=f'{day}: verify receipts, then publish only missing units')
                    reused=set();checks=[r for r in rows if r['ticker'] in saved]
                    verifying=[pool.submit(verify_prior,day,checks[i:i+128],saved) for i in range(0,len(checks),128)]
                    for future in as_completed(verifying):
                        verified=future.result();reused.update(verified)
                        with mutex:counts['reused']+=len(verified)
                        progress.update(**counts,queued=max(0,len(units)-counts['reused']-counts['completed']))
                    verification_seconds+=time.monotonic()-verification_started
                    progress.update(phase='build',verification_seconds=verification_seconds)
                    remaining=[r for r in rows if r['ticker'] not in reused]
                    iterator=iter(execution.batches(remaining));pending=set()
                    while True:
                        while len(pending)<2*a.bar_workers and not stop.is_set():
                            r=next(iterator,None)
                            if r is None:break
                            pending.add(pool.submit(batch_work,r,saved))
                        if not pending:break
                        finished,pending=wait(pending,timeout=1,return_when=FIRST_COMPLETED)
                        for f in finished:f.result()
                        if time.monotonic()-last_profile_flush>=1:
                            # Diagnostic telemetry is buffered; ClickHouse commit
                            # receipts remain independently durable for every unit.
                            with mutex:profile_stream.flush();batch_stream.flush()
                            last_profile_flush=time.monotonic()
                        elapsed=time.monotonic()-started;productive=max(.001,elapsed-verification_seconds);done=counts['completed']+counts['reused']
                        progress.update(**counts,queued=max(0,len(units)-done-counts['failed']),elapsed=elapsed,
                            new_ticker_days_per_hour=counts['completed']*3600/productive,
                            eta_seconds=(len(units)-done)*productive/counts['completed'] if counts['completed']>=max(3,a.bar_workers) else None)
                    guard()
            bars.save(root/'quote-completion.json',dict(request=request,counts=counts,seconds=time.monotonic()-started,
                profiles=profiles,luld_ready=True,halt_ready=True,official_bands=False,halt_state='unknown until explicit source status'))
            progress.update(status='complete',queued=0);return 0
        except BaseException as error:
            interrupted=isinstance(error,(InterruptedError,KeyboardInterrupt))
            stop.set();progress.update(status='interrupted' if interrupted else 'failed',message=str(error))
            (root/'stop.request').write_text('Preserve quote receipts. Diagnose and drain before deliberate resume.\n',encoding='utf-8')
            if interrupted:return 130
            raise
        finally:
            profile_stream.close();batch_stream.close()
            for client in worker_clients:client.close()
            signal.signal(signal.SIGINT,old_signal)
