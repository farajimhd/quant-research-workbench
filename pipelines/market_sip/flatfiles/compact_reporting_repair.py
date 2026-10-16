"""Approved ingestion-owned flag-only migration; no clock persistence or coverage writes.

Existing import receipts remain authoritative. Complete exception resolution is
required before timely can be the default. Only the upper two event_meta bits
are updated. This migration's receipts do not claim full original-clock evidence.
"""
from concurrent.futures import ThreadPoolExecutor,wait,FIRST_COMPLETED
from contextlib import closing
from datetime import date,datetime,timedelta,timezone
import json
from pathlib import Path
from threading import Event,Thread,Lock,current_thread

from pipelines.certified_research import source,schema
from pipelines.certified_research.contracts import digest,literal,identifier
from pipelines.certified_research.publication import insert_rows
from pipelines.market_sip.events import reporting_evidence as ns
from . import retained_reporting as retained,download_update_events as updater,archive_staging
from .download_massive_sip_flatfiles import DownloadJob
from .repair_progress import Prefetch

VERSION='compact-reporting-flags-v2'
CANDIDATES='reporting_flag_candidates_v2'
EXCEPTIONS='reporting_flag_exceptions_v2'
RECEIPTS='reporting_flag_receipts_v2'
KEYS=('ticker','sip_timestamp_us','price_primary_int','size_primary','exchange_primary','encoding',
      'condition_token_1','condition_token_2','condition_token_3','condition_token_4','condition_token_5')


def tables(database='q_live'):
    identifier(database)
    common="ENGINE=MergeTree PARTITION BY source_date ORDER BY (source_date,ticker) SETTINGS storage_policy='live_market_ssd'"
    fields='ticker String,sip_timestamp_us UInt64,price_primary_int UInt32,size_primary Float32,exchange_primary UInt8,encoding UInt8,'+','.join(f'condition_token_{i} UInt8' for i in range(1,6))
    return [f'CREATE TABLE IF NOT EXISTS {database}.{CANDIDATES} (source_date Date,{fields},expected_bits UInt8,source_rows UInt64) {common}',
        f'CREATE TABLE IF NOT EXISTS {database}.{EXCEPTIONS} (source_date Date,ticker String,ordinal UInt64,expected_bits UInt8,matched_rows UInt64,source_rows UInt64) {common}',
        f"CREATE TABLE IF NOT EXISTS {database}.{RECEIPTS} (source_date Date,source_digest FixedString(64),stage String,exception_digest FixedString(64),exceptions UInt64,total_trades UInt64,input_identity String,created_at DateTime64(6,'UTC')) ENGINE=MergeTree ORDER BY (source_date,stage) SETTINGS storage_policy='live_market_ssd'"]


def classify_sql(raw,day,database='q_live'):
    """One CSV scan; timely records collapse to one count per ticker."""
    expressions=[]
    for key in KEYS:
        value='bitAnd(event_meta,7)' if key=='encoding' else key
        expressions.append(value+' AS '+key if key=='ticker' else f'if(bitAnd(event_meta,192)=64,0,{value}) AS {key}')
    return (f'INSERT INTO {database}.{CANDIDATES} SELECT toDate({literal(day)}),'+','.join(KEYS)+',expected_bits,count() '
        'FROM (SELECT '+','.join(expressions)+f',bitAnd(event_meta,192) expected_bits FROM ({raw})) '
        'GROUP BY '+','.join((*KEYS,'expected_bits')))


def match_sql(target,day,bounds,database='q_live'):
    """Join only exception groups, retaining multiplicity proof for duplicates."""
    if not bounds:raise ValueError('No certified ordinal bounds')
    predicate=' OR '.join(f'(ticker={literal(t)} AND ordinal>={int(a)} AND ordinal<{int(b)})' for t,a,b in bounds)
    tickers=','.join(literal(t) for t,_,_ in bounds)
    keys=','.join(KEYS)
    return f"""INSERT INTO {database}.{EXCEPTIONS}
      SELECT toDate({literal(day)}),ticker,ordinal,expected_bits,
        count() OVER (PARTITION BY {keys}),source_rows
      FROM (SELECT *,bitAnd(event_meta,7) encoding FROM {target}
        PREWHERE ({predicate}) WHERE bitAnd(event_meta,1)=1) e
      INNER JOIN (SELECT * FROM {database}.{CANDIDATES}
        WHERE source_date=toDate({literal(day)}) AND expected_bits!=64 AND ticker IN ({tickers})) c
      USING ({keys}) SETTINGS join_algorithm='full_sorting_merge',max_bytes_before_external_sort=268435456"""


def desired_sql(days,database='q_live'):
    if not days:raise ValueError('Empty classification scope')
    scope=','.join('toDate('+literal(d)+')' for d in days)
    where=f'source_date IN ({scope})'
    key='(ticker,ordinal)'
    delayed=f'{key} IN (SELECT ticker,ordinal FROM {database}.{EXCEPTIONS} WHERE {where} AND expected_bits=192)'
    unknown=f'{key} IN (SELECT ticker,ordinal FROM {database}.{EXCEPTIONS} WHERE {where} AND expected_bits=0)'
    return f'toUInt8(multiIf({delayed},192,{unknown},0,64))'


def scope_sql(rows,partition):
    """Partition pruning plus exact imported SIP bounds, including UTC spill."""
    bounds=' OR '.join(f"(sip_timestamp_us BETWEEN {int(r['first_sip_timestamp_us'])} AND {int(r['last_sip_timestamp_us'])})" for r in rows)
    return f'toYYYYMM(event_date)={int(partition)} AND bitAnd(event_meta,1)=1 AND ({bounds})'


def partition_plan(inventory,days):
    result={}
    epoch=datetime(1970,1,1,tzinfo=timezone.utc)
    for day in sorted(days):
        row=inventory['rows'][day]
        first=epoch+timedelta(microseconds=int(row['first_sip_timestamp_us']))
        last=epoch+timedelta(microseconds=int(row['last_sip_timestamp_us']))
        year,month=first.year,first.month
        while (year,month)<=(last.year,last.month):
            result.setdefault((day.year,year*100+month),[]).append(day)
            year,month=(year+1,1) if month==12 else (year,month+1)
    return dict(sorted(result.items()))


def fingerprint(query,day,database='q_live'):
    sums=','.join(f'sum(reinterpretAsUInt64(substring(h,{offset},8))) h{i}' for i,offset in enumerate((1,9,17,25)))
    row=query(f"SELECT count() n,{sums} FROM (SELECT SHA256(toString(tuple(ticker,ordinal,expected_bits))) h FROM {database}.{EXCEPTIONS} WHERE source_date=toDate({literal(day)}))")[0]
    return digest({k:int(v) for k,v in row.items()}),int(row['n'])


def write_journal(path,value):
    temporary=path.with_suffix('.tmp')
    temporary.write_text(json.dumps(value,indent=2),encoding='utf-8')
    temporary.replace(path)


def receipt(query,day,stage,source_hash,database='q_live'):
    rows=query(f'SELECT * FROM {database}.{RECEIPTS} WHERE source_date=toDate({literal(day)}) AND stage={literal(stage)}')
    if len(rows)>1 or any(r['source_digest']!=source_hash for r in rows):raise ValueError('Repair receipt source is ambiguous or changed')
    return rows[0] if rows else None


def record(query,day,stage,inventory,frozen,n,input_identity):
    source_hash=inventory['source_hashes'][str(day)]
    if receipt(query,day,stage,source_hash):return
    insert_rows(query,'q_live',RECEIPTS,[dict(source_date=str(day),source_digest=source_hash,stage=stage,
        exception_digest=frozen,exceptions=n,total_trades=int(inventory['rows'][day]['trade_event_rows']),
        input_identity=input_identity,created_at=datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S.%f'))])
    if not receipt(query,day,stage,source_hash):raise ValueError('Repair receipt publication uncertain')


def prepare(query,day,inventory,paths,prefetch,files,claim,stop,progress,reserve_gib,reserve_fraction):
    worker=current_thread().name
    def stage(name,detail='',done=None,total=None):
        progress.worker(worker,name,day=day,detail=detail,done=done,total=total,unit='batches')
        progress.emit(f'{worker} | Compact reporting {day} | {name} | {detail}')
    def guard():
        claim.check()
        if stop.is_set():raise InterruptedError('Compact preparation stopped')
    guard();source_hash=inventory['source_hashes'][str(day)]
    prior=receipt(query,day,'prepared',source_hash)
    if prior:
        stage('Prepared','Reused compact exception receipt');return
    stage('Reuse evidence','Checking existing capture receipt')
    captured=query(f'SELECT evidence_hash FROM q_live.{retained.RECEIPTS} WHERE source_date=toDate({literal(day)}) AND source_digest={literal(source_hash)}')
    if len(captured)>1:raise ValueError('Ambiguous legacy capture')
    # Only our new, date-partitioned working tables are cleared on retry.
    for table in (CANDIDATES,EXCEPTIONS):
        query(f'ALTER TABLE q_live.{table} DROP PARTITION {literal(day)}',read=False)
    owned=None
    if captured:
        stage('Extract exceptions','Reusing captured classification; no CSV or clock writes')
        expected='multiIf(bitAnd(reporting_reason,7)!=0,192,bitAnd(reporting_reason,8)!=0,0,64)'
        query(f'INSERT INTO q_live.{EXCEPTIONS} SELECT source_date,ticker,ordinal,toUInt8({expected}),toUInt64(1),toUInt64(1) '
            f'FROM q_live.{ns.TABLE} WHERE source_date=toDate({literal(day)}) AND {expected}!=64',read=False)
        identity='captured:'+captured[0]['evidence_hash']
    else:
        info=files[day]
        if info['origin']==1:
            stage('Waiting for SSD');copy=prefetch.get(day)
            if copy['status']!='staged':raise ValueError('Unverified source copy')
            owned=paths.cache/'_retained_reporting'/info['key'];path=owned;identity=copy['sha256']
        else:
            path=info['source'];identity=retained._file_hash(path,stop)
        before=path.stat()
        archive_staging.space_budget(paths.cache,reserve_bytes=reserve_gib*1024**3,reserve_fraction=reserve_fraction)
        args=updater.parse_args(['--start-date',str(day),'--end-date',str(day),'--skip-bars'])
        args.flatfiles_root_win=paths.cache;args.flatfiles_root_ch=paths.clickhouse_cache
        contract=inventory['rows'][day]['source_filter_key']
        args.drop_trade_correction_codes=contract.split('drop_trade_correction_codes=')[1].split('|')[0]
        if updater.source_filter_key(args)!=contract:raise ValueError('Imported filter contract is incompatible')
        job=DownloadJob('trades',str(day),str(info['key']),str(path),int(info['bytes']))
        raw=updater.raw_event_union_sql(args,updater.DayFiles(str(day),job,job),trade_only=True,include_reporting_reason=True)
        stage('Classify CSV','One scan; participant clocks are not persisted')
        query(classify_sql(raw,day),read=False)
        after=path.stat()
        if (before.st_size,before.st_mtime_ns)!=(after.st_size,after.st_mtime_ns):raise ValueError('Source changed during classification')
        counts=query(f'SELECT sum(source_rows) n,sumIf(source_rows,expected_bits!=64) exceptions FROM q_live.{CANDIDATES} WHERE source_date=toDate({literal(day)})')[0]
        if int(counts['n'])!=int(inventory['rows'][day]['trade_event_rows']):raise ValueError('CSV filtered count differs from existing ingestion receipt')
        candidates=query(f'SELECT DISTINCT ticker FROM q_live.{CANDIDATES} WHERE source_date=toDate({literal(day)}) AND expected_bits!=64')
        wanted={r['ticker'] for r in candidates}
        bounds=query(f'SELECT ticker,first_ordinal,next_ordinal FROM market_sip_compact.events_ticker_day_index FINAL WHERE source_date=toDate({literal(day)}) ORDER BY ticker')
        seen=set();batches=[];batch=[];size=0
        for row in bounds:
            ticker=row['ticker']
            if ticker not in wanted:continue
            if ticker in seen:raise ValueError('Duplicate ticker/day ordinal bounds')
            seen.add(ticker);a,b=int(row['first_ordinal']),int(row['next_ordinal'])
            if b<=a:raise ValueError('Invalid imported ordinal range')
            if batch and (size+b-a>5_000_000 or len(batch)>=128):batches.append(batch);batch=[];size=0
            batch.append((ticker,a,b));size+=b-a
        if batch:batches.append(batch)
        if seen!=wanted:raise ValueError('Candidate ticker lacks imported ordinal bounds')
        for i,batch in enumerate(batches):
            guard();stage('Match exceptions',f'{len(wanted)} candidate tickers',i,len(batches))
            query(match_sql(f'market_sip_compact.events_{day.year}',day,batch),read=False)
        audit=query(f'SELECT count() n,uniqExact(ticker,ordinal) unique_keys,countIf(matched_rows!=source_rows) ambiguous FROM q_live.{EXCEPTIONS} WHERE source_date=toDate({literal(day)})')[0]
        if int(audit['n'])!=int(counts['exceptions']) or int(audit['n'])!=int(audit['unique_keys']) or int(audit['ambiguous']):
            stage('Unresolved',f"expected={counts['exceptions']} matched={audit['n']} ambiguous={audit['ambiguous']}")
            raise ValueError('Exception mapping is incomplete or ambiguous; timely default is forbidden')
    guard();frozen,n=fingerprint(query,day)
    record(query,day,'prepared',inventory,frozen,n,identity)
    query(f'ALTER TABLE q_live.{CANDIDATES} DROP PARTITION {literal(day)}',read=False)
    if owned is not None:
        if not owned.resolve().is_relative_to((paths.cache/'_retained_reporting').resolve()):raise ValueError('Cleanup outside owned cache')
        owned.unlink();prefetch.release(day)
    stage('Prepared',f'{n:,} exception keys; awaiting flag update')


class Workers:
    def __init__(self,days,action,client_factory,stop,progress,workers):
        self.days=list(days);self.action=action;self.client_factory=client_factory;self.stop=stop;self.progress=progress;self.workers=workers
        self.clients={};self.lock=Lock();self.ready={d:Event() for d in days};self.error=None
        self.thread=Thread(target=self.run,name='compact-preparation')
    def one(self,day):
        with closing(self.client_factory()) as client:
            name=current_thread().name
            with self.lock:self.clients[name]=client
            try:self.action(client.query,day)
            finally:
                with self.lock:self.clients.pop(name,None)
        self.ready[day].set()
        with self.progress.lock:self.progress.update(prepared=sum(e.is_set() for e in self.ready.values()))
    def run(self):
        try:
            with ThreadPoolExecutor(max_workers=self.workers,thread_name_prefix='SQL') as pool:
                todo=list(self.days);pending=set()
                while todo or pending:
                    if self.stop.is_set():raise InterruptedError('Compact preparation stopped')
                    while todo and len(pending)<self.workers:pending.add(pool.submit(self.one,todo.pop(0)))
                    done,_=wait(pending,timeout=1,return_when=FIRST_COMPLETED)
                    for f in done:f.result();pending.remove(f)
        except BaseException as error:self.error=error;self.stop.set()
        finally:
            for event in self.ready.values():event.set()
    def await_day(self,day):
        while not self.ready[day].wait(1):
            if self.stop.is_set():raise InterruptedError('Compact preparation stopped')
        if self.error:raise self.error
        if self.stop.is_set():raise InterruptedError('Compact preparation stopped')


def run(query,inventory,legacy_verified,paths,claim,stop,progress,client_factory,args,active_preparation):
    for ddl in tables():query(ddl,read=False)
    schema.verify_auxiliary_storage(query,'q_live',(CANDIDATES,EXCEPTIONS,RECEIPTS))
    completed=set(legacy_verified)
    for day in inventory['expected']:
        if receipt(query,day,'complete',inventory['source_hashes'][str(day)]):completed.add(day)
    pending=[d for d in inventory['expected'] if d not in completed]
    progress.update(coverage_label='Flags verified',reused=len(completed),completed=0,queued=len(pending))
    plan_path=paths.runtime/'compact-plan.json'
    if plan_path.exists():
        original=json.loads(plan_path.read_text())
        if original['source_hash']!=inventory['content_hash']:raise ValueError('Frozen repair plan source changed')
        pending=[date.fromisoformat(d) for d in original['days']]
    else:write_journal(plan_path,dict(source_hash=inventory['content_hash'],days=list(map(str,pending))))
    captured=query(f'SELECT source_date,source_digest FROM q_live.{retained.RECEIPTS}')
    captures={date.fromisoformat(str(r['source_date'])) for r in captured if r['source_digest']==inventory['source_hashes'].get(str(r['source_date']))}
    needed=[d for d in pending if d not in captures and not receipt(query,d,'prepared',inventory['source_hashes'][str(d)])]
    plan=retained.file_plan(paths,needed,reserve_bytes=args.reserve_gib*1024**3,reserve_fraction=args.reserve_fraction,maximum_chunk_bytes=args.prefetch_gib*1024**3)
    if plan['missing']:raise ValueError('Missing retained source files: '+str(plan['missing']))
    copies=[(d,r) for d,r in sorted(plan['files'].items()) if r['origin']==1]
    with Prefetch(copies,paths,stop,progress,workers=args.copy_workers,max_bytes=args.prefetch_gib*1024**3,
                  reserve_bytes=args.reserve_gib*1024**3,reserve_fraction=args.reserve_fraction) as prefetch:
        action=lambda q,d:prepare(q,d,inventory,paths,prefetch,plan['files'],claim,stop,progress,args.reserve_gib,args.reserve_fraction)
        workers=Workers(pending,action,client_factory,stop,progress,args.sql_workers)
        active_preparation.append(workers);workers.thread.start()
        try:
            plans=partition_plan(inventory,pending)
            remaining={d:sum(d in group for group in plans.values()) for d in pending}
            for (year,partition),days in plans.items():
                for day in days:workers.await_day(day)
                claim.check()
                current=source.inventory(query,min(days),max(days),sessions=tuple(days))
                if current['missing'] or current['incomplete'] or any(current['source_hashes'][str(d)]!=inventory['source_hashes'][str(d)] for d in days):raise ValueError('Imported source changed')
                proofs=[receipt(query,d,'prepared',inventory['source_hashes'][str(d)]) for d in days]
                for day,proof in zip(days,proofs):
                    if not proof or fingerprint(query,day)!=(proof['exception_digest'],int(proof['exceptions'])):raise ValueError('Frozen exceptions changed')
                marker=digest(dict(version=VERSION,year=year,partition=partition,proofs=proofs))
                target=f'market_sip_compact.events_{year}';scope=scope_sql([inventory['rows'][d] for d in days],partition);desired=desired_sql(days)
                check=f'SELECT count() n,countIf(bitAnd(event_meta,192)!={desired}) bad FROM {target} WHERE {scope}'
                journal=paths.runtime/f'partition-{year}-{partition}.json'
                prior=json.loads(journal.read_text()) if journal.exists() else None
                if prior and prior['marker']!=marker:raise ValueError('Partition plan differs on resume')
                submitted=bool(prior and prior.get('submitted'))
                before_counts=prior.get('before') if prior else None
                while True:
                    claim.check()
                    if workers.error:raise workers.error
                    if stop.is_set():raise InterruptedError('Compact repair stopped; mutation remains resumable')
                    if prior and prior.get('complete'):
                        counts=prior['verification'];break
                    history=query(f"SELECT mutation_id,command,is_done,parts_to_do,latest_fail_reason FROM system.mutations WHERE database='market_sip_compact' AND table='events_{year}' AND (NOT is_done OR position(command,{literal(marker)})>0)")
                    if any(marker not in r['command'] for r in history):raise ValueError('Unrelated canonical mutation must be reconciled before compact repair')
                    if history:submitted=True
                    mutations=[r for r in history if not int(r['is_done'])]
                    if mutations:
                        if any(r['latest_fail_reason'] for r in mutations):raise ValueError('Compact mutation failed: '+str(mutations))
                        progress.worker('MUTATE','Waiting on mutation',day=str(partition),kind='mutation',detail=f"{sum(int(r['parts_to_do']) for r in mutations)} parts remaining; consolidated exception sets")
                        stop.wait(10);continue
                    progress.worker('MUTATE','Verify flags',day=str(partition),kind='mutation',detail='One reporting-only check over the classified scope')
                    counts=query(check)[0]
                    if before_counts and int(before_counts['n'])!=int(counts['n']):raise ValueError('Canonical trade count changed during mutation')
                    if not int(counts['bad']):break
                    if submitted:raise ValueError('Completed compact mutation did not produce expected reporting flags')
                    command=f'ALTER TABLE {target} UPDATE event_meta=toUInt8(bitOr(bitAnd(event_meta,63),{desired})) IN PARTITION {partition} WHERE ({scope}) AND bitAnd(event_meta,192)!={desired} AND {literal(marker)}={literal(marker)} SETTINGS mutations_sync=0'
                    before_counts=counts
                    write_journal(journal,dict(marker=marker,days=list(map(str,days)),submitted=False,before=counts,command=command))
                    query(command,read=False);submitted=True
                    write_journal(journal,dict(marker=marker,days=list(map(str,days)),submitted=True,before=counts,command=command))
                    progress.emit(f'Compact reporting {partition} | submitted | {counts["bad"]} incorrect flags | two exception sets')
                write_journal(journal,dict(marker=marker,days=list(map(str,days)),submitted=submitted,complete=True,verification=counts))
                for day,proof in zip(days,proofs):
                    remaining[day]-=1
                    if remaining[day]==0:
                        record(query,day,'complete',inventory,proof['exception_digest'],int(proof['exceptions']),proof['input_identity'])
                        completed.add(day)
                progress.update(completed=len(completed)-progress.state['reused'],queued=len(inventory['expected'])-len(completed))
            (paths.runtime/'completion.json').write_text(json.dumps(dict(version=VERSION,source_hash=inventory['content_hash'],days=len(completed)),indent=2))
        finally:
            stop.set();workers.thread.join()
