#!/usr/bin/env python3
"""Isolated, server-side structure feasibility study; never a production writer.

Only metadata, validation counts and profiles leave ClickHouse. SQL candidates
are NOT a replacement for the v18 state machine. Closing rows are extracted
from retained v18 checkpoints to measure storage independently of computation.
"""
from __future__ import annotations

import argparse
import concurrent.futures as futures
import datetime as dt
import hashlib
import json
import os
from pathlib import Path
import re
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid

sys.dont_write_bytecode = True
os.environ['PYTHONDONTWRITEBYTECODE'] = '1'

SET = 'canonical-tradable-20250101-20260904-prominence-v18-v1'
FRAMES = [('100ms',100),('1s',1000),('5s',5000),('10s',10000),
          ('30s',30000),('1m',60000),('5m',300000),('1h',3600000),
          ('1d',86400000),('1w',604800000)]


def literal(value):
    return "'" + str(value).replace('\\', '\\\\').replace("'", "\\'") + "'"


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


def save(path, value):
    temporary = path.with_suffix('.tmp')
    temporary.write_text(json.dumps(value, indent=2), encoding='utf-8')
    temporary.replace(path)


class Client:
    def __init__(self, env_file, threads=4):
        config = {}
        for line in env_file.read_text(encoding='utf-8-sig').splitlines():
            match = re.match(r'^\s*([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.*?)\s*$', line)
            if match:
                config[match[1]] = match[2].strip('"').strip("'")
        def pick(keys, default=''):
            return next((os.environ.get(k) or config.get(k) for k in keys if os.environ.get(k) or config.get(k)), default)
        self.url = pick(['QMD_CLICKHOUSE_URL','REAL_LIVE_CLICKHOUSE_WRITE_URL','CLICKHOUSE_URL','CLICKHOUSE_ENDPOINT'])
        self.user = pick(['QMD_CLICKHOUSE_USER','REAL_LIVE_CLICKHOUSE_WRITE_USER','CLICKHOUSE_WORKSTATION_USER','CLICKHOUSE_USER'], 'default')
        self.password = pick(['QMD_CLICKHOUSE_PASSWORD','REAL_LIVE_CLICKHOUSE_WRITE_PASSWORD','CLICKHOUSE_WORKSTATION_PASSWORD','CLICKHOUSE_PASSWORD'])
        if not self.url:
            raise ValueError('Missing ClickHouse endpoint')
        self.threads, self.profiles, self.active = threads, [], set()
        self.lock = threading.Lock()

    def query(self, sql, label, read=True, seconds=120):
        for attempt in range(3 if read else 1):
            try:
                return self._query(sql,label,read,seconds)
            except (ConnectionError,TimeoutError,urllib.error.URLError):
                if not read or attempt==2:
                    raise
                print(f'Retrying read | {label} | attempt={attempt+2}/3',flush=True)
                time.sleep(.25*(attempt+1))

    def _query(self, sql, label, read=True, seconds=120):
        query_id = 'structure-feasibility-' + uuid.uuid4().hex
        params = dict(query_id=query_id, max_threads=self.threads, max_insert_threads=self.threads,
                      max_memory_usage=2147483648, max_execution_time=seconds,
                      max_block_size=1 if label.startswith('close_') or label=='baseline_bytes' else 65536,
                      max_result_rows=10000, max_result_bytes=8000000, result_overflow_mode='throw')
        if read:
            params['readonly'] = 1
        url = self.url.rstrip('/') + '/?' + urllib.parse.urlencode(params)
        request = urllib.request.Request(url, data=(sql + (' FORMAT JSONEachRow' if read else '')).encode(),
                    headers={'X-ClickHouse-User':self.user,'X-ClickHouse-Key':self.password})
        started = time.perf_counter()
        with self.lock:
            self.active.add(query_id)
        status = 'failed'
        try:
            with urllib.request.urlopen(request, timeout=seconds+15) as response:
                body = response.read(8000001)
                if len(body)>8000000:
                    raise RuntimeError('Metadata response exceeded bound')
            rows = [json.loads(line) for line in body.splitlines()] if read else []
            if not read and body.strip() and label!='cancel':
                raise RuntimeError('Unexpected INSERT/DDL response')
            status = 'completed'
            return rows
        except urllib.error.HTTPError as error:
            message = error.read(2500).decode(errors='replace')
            for secret in (self.password, self.url):
                if secret:
                    message = message.replace(secret, '[redacted]')
            raise RuntimeError(f'{label}: HTTP {error.code}: {message}') from None
        finally:
            with self.lock:
                self.active.discard(query_id)
                self.profiles.append(dict(label=label, query_id=query_id, status=status,
                    read_only=read,sql_sha256=hashlib.sha256(sql.encode()).hexdigest(),
                    wall_seconds=time.perf_counter()-started))

    def cancel(self):
        with self.lock:
            ids = list(self.active)
        for query_id in ids:
            self.query('KILL QUERY WHERE query_id=' + literal(query_id) + ' SYNC', 'cancel', read=False, seconds=15)


def condition_sql(rules):
    """Mirror decoded trade-condition intersection and extended-hours Form T."""
    token = lambda predicate: '[' + ','.join(str(r['token_id']) for r in rules if predicate(r)) + ']'
    known = token(lambda r: 0 <= r['modifier_int'] <= 65535)
    price = token(lambda r: r['update_last'] == 1)
    both = token(lambda r: r['update_last'] == r['update_high_low'] == 1 or r['modifier_int'] in (0,12))
    volume = token(lambda r: r['update_volume'] == 1)
    form = token(lambda r: r['modifier_int'] == 12)
    tokens = 'arrayFilter(x -> has(' + known + ',x), [condition_token_1,condition_token_2,condition_token_3,condition_token_4,condition_token_5])'
    return f"""{tokens} AS tokens,
       (toHour(local_time)*3600+toMinute(local_time)*60+toSecond(local_time)) AS local_second,
       ((local_second<34200 OR local_second>=57600) AND hasAny(tokens,{form})
         AND arrayAll(x->has({both},x),tokens)) AS form_t,
       arrayAll(x->has({price},x) OR (form_t AND has({form},x)),tokens) AS price_eligible,
       arrayAll(x->has({volume},x) OR (form_t AND has({form},x)),tokens) AS volume_eligible"""


def raw_events_sql(ticker, start, end):
    # Canonical table partitions are UTC dates. Certification is by New York
    # date; include a possible next-year UTC tail rather than clipping at UTC
    # midnight. merge() is restricted to named canonical yearly tables only.
    last_utc_day=dt.date.fromisoformat(end)+dt.timedelta(days=1)
    years='|'.join(map(str,range(int(start[:4]),last_utc_day.year+1)))
    return f"SELECT * FROM merge('market_sip_compact','^events_({years})$') WHERE ticker={literal(ticker)} AND toDate(fromUnixTimestamp64Micro(toInt64(sip_timestamp_us),'America/New_York')) BETWEEN toDate({literal(start)}) AND toDate({literal(end)})"


def events_sql(ticker, start, end, rules):
    return f"""SELECT *,fromUnixTimestamp64Micro(toInt64(sip_timestamp_us),'America/New_York') AS local_time,
      toFloat64(price_primary_int)/if(bitAnd(event_meta,2)=2,10000.,100.) AS price,
      {condition_sql(rules)}
      FROM ({raw_events_sql(ticker,start,end)}) WHERE bitAnd(event_meta,1)=1 AND price_primary_int>0"""


def bucket_sql(frame, horizon, source, db):
    # Calendar windows are pinned to 04:00 New York, including DST.
    if frame in ('1d','1w'):
        day = 'subtractDays(toDate(local_time),toHour(local_time)<4)'
        anchor = f'toMonday({day})' if frame=='1w' else day
        bucket = f"toUnixTimestamp64Milli(toDateTime64(concat(toString({anchor}),' 04:00:00'),3,'America/New_York'))"
    else:
        bucket = f'toInt64(intDiv(sip_timestamp_us,{horizon*1000})*{horizon})'
    return f"""INSERT INTO {db}.buckets
      SELECT {literal(frame)},toUInt32({horizon}),{bucket} AS bucket_ms,
        max(price),min(price),
        argMax(sip_timestamp_us,tuple(price,ordinal)),argMax(sip_timestamp_us,tuple(-price,ordinal)),
        min(sip_timestamp_us),argMin(ordinal,tuple(sip_timestamp_us,ordinal)),
        sum(if(volume_eligible,greatest(toFloat64(size_primary),0.),0.)),count()
      FROM ({source}) WHERE price_eligible GROUP BY bucket_ms"""


def detector_sql(db):
    # A center pivot requires the right bucket to complete on the first event
    # in bucket +2. V18 clears the queue then pushes the old current bucket:
    # a left->center gap is retained; only the next two gaps suppress the triple.
    return f"""INSERT INTO {db}.candidates
      SELECT timeframe,side,if(side=-1,high,low),if(side=-1,high_us,low_us),
             confirm_us,confirm_ordinal,bucket_ms,
             toUInt8(timeframe!='100ms'),toUInt8(split_crossing)
      FROM (
        SELECT *, lagInFrame(bucket_ms,1,toInt64(-1)) OVER w AS left_ms,
          lagInFrame(high,1,0.) OVER w AS left_high,lagInFrame(low,1,0.) OVER w AS left_low,
          leadInFrame(bucket_ms,1,toInt64(-1)) OVER w AS right_ms,
          leadInFrame(bucket_ms,2,toInt64(-1)) OVER w AS completion_ms,
          leadInFrame(high,1,0.) OVER w AS right_high,leadInFrame(low,1,0.) OVER w AS right_low,
          leadInFrame(first_us,2,toUInt64(0)) OVER w AS confirm_us,
          leadInFrame(first_ordinal,2,toUInt64(0)) OVER w AS confirm_ordinal,
          arrayExists(s -> s>left_ms AND s<=completion_ms,
             (SELECT groupArray(effective_ms) FROM {db}.splits)) AS split_crossing
        FROM {db}.buckets WINDOW w AS (PARTITION BY timeframe ORDER BY bucket_ms
          ROWS BETWEEN UNBOUNDED PRECEDING AND UNBOUNDED FOLLOWING)
      ) ARRAY JOIN [toInt8(-1),toInt8(1)] AS side
      WHERE left_ms>=0 AND right_ms>=0 AND completion_ms>=0
        AND right_ms-bucket_ms<=3*toInt64(horizon_ms)
        AND completion_ms-right_ms<=3*toInt64(horizon_ms)
        AND if(side=-1,high>=left_high AND high>right_high,low<=left_low AND low<right_low)"""


def closing_sql(db, ticker, checkpoint_set, date, index):
    return f"""INSERT INTO {db}.closes
      SELECT toUInt16({index}),toDate({literal(date)}),
        JSONExtractUInt(t,'level','unified_level_id'),JSONExtractFloat(t,'level','price'),
        JSONExtractFloat(t,'level','lower'),JSONExtractFloat(t,'level','upper'),
        toInt8(JSONExtractInt(t,'level','side')),JSONExtractString(t,'level','lifecycle'),
        toInt8(JSONExtractInt(t,'level','pending_side')),
        JSONExtractInt(t,'level','created_at_ms'),JSONExtractInt(t,'level','confirmed_at_ms')
      FROM (SELECT snapshot_json FROM q_live.qmd_structure_daily_checkpoint_v2
        WHERE checkpoint_set_id={literal(checkpoint_set)} AND sym={literal(ticker)}
          AND session_date=toDate({literal(date)}) AND algorithm_version=18 AND source_complete=1
          AND notEmpty(certification_json) ORDER BY built_at DESC LIMIT 1)
      ARRAY JOIN JSONExtractArrayRaw(snapshot_json,'unified_tracks') AS t
      WHERE JSONExtractString(t,'level','lifecycle')!='retired'"""


def interval_sql(db):
    # V18 level_id is not unique within a close. Preserve the exact multiset
    # of selected state fields instead of inventing identity or dropping rows.
    return f"""INSERT INTO {db}.intervals
      SELECT level_id,price,lower,upper,side,lifecycle,pending_side,created_ms,confirmed_ms,
        multiplicity,min(session_index),max(session_index)+1,min(session_date),max(session_date)
      FROM (
        SELECT *,toInt64(session_index)-toInt64(row_number() OVER (
           PARTITION BY level_id,price,lower,upper,side,lifecycle,pending_side,created_ms,confirmed_ms,multiplicity
           ORDER BY session_index)) AS episode
        FROM (
          SELECT session_index,session_date,level_id,price,lower,upper,side,lifecycle,pending_side,
             created_ms,confirmed_ms,toUInt32(count()) AS multiplicity
          FROM {db}.closes GROUP BY ALL
        )
      ) GROUP BY level_id,episode,price,lower,upper,side,lifecycle,pending_side,created_ms,confirmed_ms,multiplicity"""


def run(args):
    root = args.runtime.resolve()
    authority = Path(r'D:\TradingML\runtimes').resolve()
    if not root.is_relative_to(authority) or not authority.is_dir():
        raise ValueError('Runtime must be under existing D:\\TradingML\\runtimes')
    root.mkdir(parents=True, exist_ok=True)
    if not re.fullmatch(r'[A-Z0-9._-]{1,32}', args.ticker):
        raise ValueError('Invalid ticker')
    client = Client(args.env_file,args.threads)
    report_path = root/'report.json'
    report = json.loads(report_path.read_text()) if report_path.exists() else {}
    if report and report.get('status') != 'completed_feasibility_only':
        raise ValueError('Incomplete/failed attempt requires a new runtime; uncertain INSERTs must not be replayed')
    db = report.get('database','structure_feasibility_'+uuid.uuid4().hex[:12])
    if not re.fullmatch(r'structure_feasibility_[a-f0-9]{12}',db):
        raise ValueError('Invalid isolated database')
    report.update(database=db,status='running',scope='SQL timeframe feasibility and v18 closing-book storage; not full-engine equivalence',
                  ticker=args.ticker,requested_start=args.start,requested_end=args.end,threads=args.threads,workers=args.workers,
                  checkpoint_set=args.checkpoint_set)
    save(report_path,report)
    done=set(report.get('completed',[]))
    phase_times=report.setdefault('phase_seconds',{})
    def execute(label,sql,read=False):
        (root/(label.replace(':','_')+'.sql')).write_text(sql,encoding='utf-8')
        return client.query(sql,label,read=read)
    def completed(label,sql):
        if label not in done:
            return False
        saved=root/(label.replace(':','_')+'.sql')
        if not saved.exists() or saved.read_text(encoding='utf-8')!=sql:
            raise ValueError(f'Completed SQL changed for {label}; use a fresh runtime')
        return True
    def stage(label,sql):
        if completed(label,sql):
            return
        print(f'Active | {label} | completed={len(done)}',flush=True)
        started=time.perf_counter()
        execute(label,sql)
        phase_times[label]=time.perf_counter()-started
        done.add(label)
        report['completed']=sorted(done)
        save(report_path,report)
    try:
        days=client.query(f"""SELECT source_date,event_count,next_ordinal,last_ordinal,
          first_sip_timestamp_us,last_sip_timestamp_us,build_step,updated_at
          FROM market_sip_compact.events_ordinal_continuity FINAL
          WHERE ticker={literal(args.ticker)} AND source_date BETWEEN {literal(args.start)} AND {literal(args.end)} ORDER BY source_date""",'coverage')
        if not days:
            raise ValueError('No canonical coverage')
        end=days[-1]['source_date']
        rules=client.query("SELECT token_id,modifier_int,update_high_low,update_last,update_volume FROM market_sip_compact.event_condition_token_reference WHERE source_family='trade_conditions' AND is_join_canonical=1 ORDER BY token_id",'rules')
        source_days=client.query(f"SELECT source_date,stats_version,source_filter_key,total_event_rows_after_filters FROM market_sip_compact.events_source_day_stats FINAL WHERE source_date BETWEEN {literal(args.start)} AND {literal(end)} ORDER BY source_date",'source_days')
        splits=client.query(f"SELECT provider_ticker,execution_date,split_from,split_to,inserted_at FROM q_live.market_stock_split_v1 FINAL WHERE provider_ticker={literal(args.ticker)} AND execution_date BETWEEN {literal(args.start)} AND {literal(end)} ORDER BY execution_date",'splits')
        baseline_sql=f"SELECT session_date,max(built_at) AS latest_built_at,count() AS versions,argMax(certification_json,built_at) AS certification FROM q_live.qmd_structure_daily_checkpoint_v2 WHERE checkpoint_set_id={literal(args.checkpoint_set)} AND sym={literal(args.ticker)} AND algorithm_version=18 AND source_complete=1 AND session_date BETWEEN {literal(args.start)} AND {literal(end)} GROUP BY session_date ORDER BY session_date"
        baseline=client.query(baseline_sql,'baseline')
        if {r['source_date'] for r in days}!={r['source_date'] for r in source_days}:
            raise ValueError('Source day statistics do not match continuity coverage')
        if {r['source_date'] for r in days}!={r['session_date'] for r in baseline} or any(r['versions']!=1 for r in baseline):
            raise ValueError('Baseline coverage missing or ambiguous; no checkpoint silently skipped')
        previous=None
        for day,row in zip(days,baseline):
            cert=json.loads(row['certification'])
            if cert['event_evidence']['event_count']!=int(day['event_count']):
                raise ValueError('Baseline event count differs from canonical continuity')
            if previous and (cert['predecessor_checkpoint_sha256']!=previous['checkpoint_sha256']
                            or cert['predecessor_chain_sha256']!=previous['chain_sha256']):
                raise ValueError('Baseline certification chain link mismatch')
            previous=cert
        fingerprint=digest([days,rules,source_days,splits,baseline])
        if report.get('source_fingerprint',fingerprint)!=fingerprint:
            raise ValueError('Pinned authority changed; use new runtime')
        report.update(source_fingerprint=fingerprint,actual_end=end,source_sessions=len(days),
          source_events=sum(int(r['event_count']) for r in days),splits=splits)
        save(root/'source-manifest.json',dict(days=days,rules=rules,source_days=source_days,splits=splits,baseline=baseline))
        storage=client.query("SELECT policy_name,disks FROM system.storage_policies WHERE policy_name='live_market_ssd'",'storage')
        if not storage or any('default' in r['disks'] for r in storage):
            raise ValueError('Required SSD policy unavailable or routes to default')
        stage('database',f'CREATE DATABASE IF NOT EXISTS {db}')
        definitions={
          'buckets':"timeframe LowCardinality(String),horizon_ms UInt32,bucket_ms Int64,high Float64,low Float64,high_us UInt64,low_us UInt64,first_us UInt64,first_ordinal UInt64,volume Float64,trades UInt64",
          'candidates':"timeframe LowCardinality(String),side Int8,price Float64,pivot_us UInt64,confirmed_us UInt64,confirmed_ordinal UInt64,bucket_ms Int64,may_found_level UInt8,split_crossing UInt8",
          'splits':'effective_ms Int64',
          'closes':"session_index UInt16,session_date Date,level_id UInt64,price Float64,lower Float64,upper Float64,side Int8,lifecycle LowCardinality(String),pending_side Int8,created_ms Int64,confirmed_ms Int64",
          'intervals':"level_id UInt64,price Float64,lower Float64,upper Float64,side Int8,lifecycle LowCardinality(String),pending_side Int8,created_ms Int64,confirmed_ms Int64,multiplicity UInt32,from_session UInt16,to_session UInt16,first_close Date,last_close Date"}
        order={'buckets':'(timeframe,bucket_ms)','candidates':'(timeframe,confirmed_us,confirmed_ordinal,side)','splits':'effective_ms','closes':'(session_index,level_id)','intervals':'(level_id,from_session)'}
        for table,columns in definitions.items():
            stage('schema_'+table,f"CREATE TABLE IF NOT EXISTS {db}.{table} ({columns}) ENGINE=MergeTree ORDER BY {order[table]} SETTINGS storage_policy='live_market_ssd'")
        policies=client.query(f"SELECT name,storage_policy FROM system.tables WHERE database={literal(db)}",'table_policies')
        if not set(definitions).issubset({r['name'] for r in policies}) or any(r['storage_policy']!='live_market_ssd' for r in policies):
            raise ValueError('Table storage policy mismatch')
        misplaced=client.query(f"SELECT count() AS n FROM system.parts WHERE active AND database={literal(db)} AND disk_name!='live_market_ssd'",'placement_before_writes')
        if int(misplaced[0]['n']):
            raise ValueError('Existing part placement mismatch; writers refused')
        if splits:
            stage('split_dates',f"INSERT INTO {db}.splits SELECT toUnixTimestamp64Milli(toDateTime64(concat(toString(execution_date),' 04:00:00'),3,'America/New_York')) FROM q_live.market_stock_split_v1 FINAL WHERE provider_ticker={literal(args.ticker)} AND execution_date BETWEEN {literal(args.start)} AND {literal(end)}")
        source=events_sql(args.ticker,args.start,end,rules)
        actual=client.query(f"SELECT toDate(fromUnixTimestamp64Micro(toInt64(sip_timestamp_us),'America/New_York')) AS session_date,count() AS n,uniqExact(ordinal) AS unique_ordinals,min(ordinal) AS first_ordinal,max(ordinal) AS last_ordinal,min(sip_timestamp_us) AS first_us,max(sip_timestamp_us) AS last_us,countIf(bitAnd(event_meta,1)=1) AS trades,countIf(bitAnd(event_meta,1)=1 AND price_primary_int=0) AS zero_price_trades FROM ({raw_events_sql(args.ticker,args.start,end)}) GROUP BY session_date ORDER BY session_date",'source_actual')
        actual_by_date={r['session_date']:r for r in actual}
        for day in days:
            row=actual_by_date.get(day['source_date'])
            if not row and int(day['event_count'])==0:
                continue
            if not row or (int(row['n']),int(row['unique_ordinals']),int(row['first_ordinal']),int(row['last_ordinal']),int(row['first_us']),int(row['last_us'])) != (int(day['event_count']),int(day['event_count']),int(day['next_ordinal'])-int(day['event_count']),int(day['last_ordinal']),int(day['first_sip_timestamp_us']),int(day['last_sip_timestamp_us'])):
                raise ValueError('Canonical actual rows disagree with pinned continuity')
        if set(actual_by_date)-{r['source_date'] for r in days}:
            raise ValueError('Canonical rows outside certified day population')
        report['actual_source_counts']=actual
        report['trade_eligibility']=client.query(f"SELECT count() AS positive_price_trades,countIf(price_eligible) AS eligible_trades,countIf(NOT price_eligible) AS excluded_by_condition,countIf(price_eligible AND NOT volume_eligible) AS eligible_without_volume FROM ({source})",'eligibility')
        def jobs(items,phase):
            todo=[item for item in items if not completed(*item)]
            started=time.perf_counter()
            # Failed/uncertain INSERTs require a fresh isolated runtime.
            with futures.ThreadPoolExecutor(max_workers=args.workers) as pool:
                pending={pool.submit(execute,label,sql):label for label,sql in todo}
                last_display=0.
                while pending:
                    finished,_=futures.wait(pending,timeout=10,return_when=futures.FIRST_COMPLETED)
                    for future in finished:
                        label=pending.pop(future)
                        try:
                            future.result()
                        except BaseException:
                            for other in pending:
                                other.cancel()
                            client.cancel()
                            raise
                        done.add(label)
                        report['completed']=sorted(done)
                        save(report_path,report)
                    if time.perf_counter()-last_display>=5 or not pending:
                        print(f'{phase} | completed={len(items)-len(pending)} active={min(args.workers,len(pending))} queued={max(0,len(pending)-args.workers)} failed=0 retried=0 skipped={len(items)-len(todo)}',flush=True)
                        last_display=time.perf_counter()
            phase_times[phase]=phase_times.get(phase,0)+time.perf_counter()-started
            save(report_path,report)
        report['attempted']=True
        save(report_path,report)
        jobs([(f'buckets_{frame}',bucket_sql(frame,h,source,db)) for frame,h in FRAMES],'aggregation')
        stage('detect',detector_sql(db))
        jobs([(f'close_{r["source_date"]}',closing_sql(db,args.ticker,args.checkpoint_set,r['source_date'],i+1)) for i,r in enumerate(days)],'closing_extraction')
        stage('intervals',interval_sql(db))
        report['candidate_counts']=client.query(f'SELECT timeframe,count() rows,countIf(split_crossing) split_sensitive_rows FROM {db}.candidates GROUP BY timeframe ORDER BY timeframe','candidate_counts')
        report['counts']=client.query(f"SELECT 'buckets' AS kind,count() rows FROM {db}.buckets UNION ALL SELECT 'closes',count() FROM {db}.closes UNION ALL SELECT 'intervals',count() FROM {db}.intervals",'counts')
        report['baseline_bytes']=client.query(f"SELECT count() rows,sum(length(snapshot_json)) snapshot_bytes FROM q_live.qmd_structure_daily_checkpoint_v2 WHERE checkpoint_set_id={literal(args.checkpoint_set)} AND sym={literal(args.ticker)} AND algorithm_version=18 AND source_complete=1 AND session_date BETWEEN {literal(args.start)} AND {literal(end)}",'baseline_bytes')
        duplicates=client.query(f'SELECT count()-uniqExact(tuple(session_index,level_id)) duplicates FROM {db}.closes','duplicates')[0]['duplicates']
        report['duplicate_level_id_occurrences']=int(duplicates)
        # Bidirectional exact multiset comparison, not a count-only assertion.
        cols='session_index,level_id,price,lower,upper,side,lifecycle,pending_side,created_ms,confirmed_ms'
        reconstructed=f"SELECT toUInt16(arrayJoin(range(toUInt64(from_session),toUInt64(to_session)))) AS session_index,level_id,price,lower,upper,side,lifecycle,pending_side,created_ms,confirmed_ms,toUInt64(multiplicity) AS multiplicity FROM {db}.intervals"
        expected=f'SELECT {cols},count() AS multiplicity FROM {db}.closes GROUP BY ALL'
        report['reconstruction']=client.query(f"SELECT count() mismatches FROM (({expected} EXCEPT ALL {reconstructed}) UNION ALL ({reconstructed} EXCEPT ALL {expected}))",'reconstruction')
        if int(report['reconstruction'][0]['mismatches']):
            raise ValueError('Interval reconstruction mismatch')
        stage('query_schema',f"CREATE TABLE IF NOT EXISTS {db}.closing_book (book_version LowCardinality(String),ticker LowCardinality(String),{definitions['intervals']}) ENGINE=MergeTree PARTITION BY cityHash64(ticker)%32 ORDER BY (book_version,ticker,from_session,level_id) SETTINGS storage_policy='live_market_ssd'")
        stage('query_layout',f"INSERT INTO {db}.closing_book SELECT {literal(args.checkpoint_set)},{literal(args.ticker)},* FROM {db}.intervals")
        stage('time_schema',f"CREATE TABLE IF NOT EXISTS {db}.closing_book_time (book_version LowCardinality(String),ticker LowCardinality(String),{definitions['intervals']},valid_from_close DateTime64(6,'UTC'),valid_to_close Nullable(DateTime64(6,'UTC'))) ENGINE=MergeTree PARTITION BY cityHash64(ticker)%32 ORDER BY (book_version,ticker,valid_from_close,level_id) SETTINGS storage_policy='live_market_ssd'")
        date_array='['+','.join(literal(r['source_date']) for r in days)+']'
        from_close="toTimeZone(toDateTime64(concat(toString(first_close),' 20:00:00'),6,'America/New_York'),'UTC')"
        to_close=f"if(to_session>{len(days)},NULL,toTimeZone(toDateTime64(concat(arrayElement({date_array},least(to_session,toUInt16({len(days)}))),' 20:00:00'),6,'America/New_York'),'UTC'))"
        stage('time_layout',f"INSERT INTO {db}.closing_book_time SELECT {literal(args.checkpoint_set)},{literal(args.ticker)},*,{from_close},{to_close} FROM {db}.intervals")
        report['query_samples']=[]
        for i in (1,len(days)//2,len(days)):
            count=client.query(f"SELECT sum(multiplicity) AS levels FROM {db}.closing_book WHERE cityHash64(ticker)%32=cityHash64({literal(args.ticker)})%32 AND book_version={literal(args.checkpoint_set)} AND ticker={literal(args.ticker)} AND from_session<={i} AND to_session>{i}",f'asof_{i}')
            report['query_samples'].append(dict(session_index=i,**count[0]))
        open_date='2026-08-21'
        prior_index=max((i+1 for i,r in enumerate(days) if r['source_date']<open_date),default=0)
        if prior_index and args.start<open_date<=end:
            cutoff=f"toDateTime64('{open_date} 04:00:00',6,'America/New_York')"
            book_filter=f"cityHash64(ticker)%32=cityHash64({literal(args.ticker)})%32 AND book_version={literal(args.checkpoint_set)} AND ticker={literal(args.ticker)}"
            observed=client.query(f"SELECT sum(multiplicity) AS levels FROM {db}.closing_book_time WHERE {book_filter} AND valid_from_close<{cutoff} AND (valid_to_close IS NULL OR valid_to_close>={cutoff})",'opening_query')[0]['levels']
            expected=client.query(f"SELECT count() AS levels FROM {db}.closes WHERE session_index={prior_index}",'opening_reference')[0]['levels']
            if observed!=expected:
                raise ValueError('Prior-session opening query mismatch')
            report['opening_query']=dict(date=open_date,prior_close=days[prior_index-1]['source_date'],levels=observed)
        # Freshly recheck pinned baseline publication metadata after extraction.
        if client.query(baseline_sql,'baseline_recheck')!=baseline:
            raise ValueError('Baseline changed during extraction')
        final_days=client.query(f"SELECT source_date,event_count,next_ordinal,last_ordinal,first_sip_timestamp_us,last_sip_timestamp_us,build_step,updated_at FROM market_sip_compact.events_ordinal_continuity FINAL WHERE ticker={literal(args.ticker)} AND source_date BETWEEN {literal(args.start)} AND {literal(args.end)} ORDER BY source_date",'coverage_recheck')
        final_rules=client.query("SELECT token_id,modifier_int,update_high_low,update_last,update_volume FROM market_sip_compact.event_condition_token_reference WHERE source_family='trade_conditions' AND is_join_canonical=1 ORDER BY token_id",'rules_recheck')
        final_splits=client.query(f"SELECT provider_ticker,execution_date,split_from,split_to,inserted_at FROM q_live.market_stock_split_v1 FINAL WHERE provider_ticker={literal(args.ticker)} AND execution_date BETWEEN {literal(args.start)} AND {literal(end)} ORDER BY execution_date",'splits_recheck')
        final_source_days=client.query(f"SELECT source_date,stats_version,source_filter_key,total_event_rows_after_filters FROM market_sip_compact.events_source_day_stats FINAL WHERE source_date BETWEEN {literal(args.start)} AND {literal(end)} ORDER BY source_date",'source_days_recheck')
        if digest([final_days,final_rules,final_source_days,final_splits,baseline])!=fingerprint:
            raise ValueError('Source authority changed during the benchmark')
        report['storage']=client.query(f'SELECT table,sum(rows) rows,sum(bytes_on_disk) disk_bytes,sum(data_uncompressed_bytes) raw_bytes,groupUniqArray(disk_name) disks FROM system.parts WHERE active AND database={literal(db)} GROUP BY table ORDER BY table','parts')
        if any(r['disks']!=['live_market_ssd'] for r in report['storage']):
            raise ValueError('Physical part placement mismatch')
        ids=list(dict.fromkeys(p['query_id'] for p in report.get('profiles',[])+client.profiles if p['status']=='completed'))
        # Query log is asynchronous. Short bounded retries do not flush shared logs.
        for _ in range(3):
            report['server_profiles']=client.query("SELECT query_id,query_duration_ms,memory_usage,read_rows,read_bytes,written_rows,written_bytes,ProfileEvents['UserTimeMicroseconds'] AS user_cpu_us FROM system.query_log WHERE event_date>=today()-1 AND type='QueryFinish' AND query_id IN ("+','.join(map(literal,ids))+")",'server_profiles')
            if len(report['server_profiles'])>=len(ids):
                break
            time.sleep(2)
        report['server_profile_expected']=len(ids)
        report['status']='completed_feasibility_only'
        report['controller_sha256']=hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
        report['limitations']=['No v18 full-engine replacement or restart-state reduction implemented.',
          'SQL candidates retain raw-price comparisons; split-crossing rows are marked diagnostic and not certified equivalent.',
          'Floating volume reductions are not claimed bit-identical to event-ordered sums.',
          'Closing projection excludes scores, counters and sources; equivalence covers selected geometry/role/lifecycle fields only.',
          'Final interval endpoint means observed through final certified close, not known future validity.']
    except BaseException as error:
        report.update(status='failed',status_before='failed',error=str(error))
        client.cancel()
        raise
    finally:
        report['profiles']=report.get('profiles',[])+client.profiles
        save(report_path,report)
    print(f'Completed feasibility | {report_path}',flush=True)


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--ticker',default='JUNS')
    parser.add_argument('--start',default='2025-01-01')
    parser.add_argument('--end',default=dt.date.today().isoformat())
    parser.add_argument('--checkpoint-set',default=SET)
    parser.add_argument('--threads',type=int,default=4)
    parser.add_argument('--workers',type=int,default=2)
    parser.add_argument('--env-file',type=Path,default=Path(r'\\DESKTOP-SAAI85T\Workstation-D\TradingML\secrets\.env'))
    parser.add_argument('--runtime',type=Path,required=True)
    args=parser.parse_args()
    if not 1<=args.threads<=8 or not 1<=args.workers<=4:
        parser.error('threads must be 1..8 and workers 1..4 (2 GiB cap per query)')
    for value in (args.start,args.end):
        dt.date.fromisoformat(value)
    if args.start>args.end:
        parser.error('start must not be after end')
    run(args)


if __name__=='__main__':
    main()
