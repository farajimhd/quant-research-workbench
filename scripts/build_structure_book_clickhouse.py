#!/usr/bin/env python3
"""ClickHouse-native closing book v1. No Rust invocation or market-row fetching.

This is a new, explicit algorithm: fixed price cells, confirmed timeframe
swings, completed-second lifecycle observations, and causal reaction prominence.
V18 checkpoints are comparison data only, never construction inputs.
"""
from __future__ import annotations
import argparse
import concurrent.futures as futures
import datetime as dt
import hashlib
import json
from math import isfinite
import os
from pathlib import Path
import re
import sys
import time
import uuid
os.environ['PYTHONDONTWRITEBYTECODE']='1'
sys.dont_write_bytecode=True
import prototype_structure_book_clickhouse as P
from structure_prominence_sql import fold_sql

VERSION='clickhouse-closing-book-1'
STATE="Tuple(Int8,UInt8,UInt32,Tuple(Float64,Float64,Float64,UInt8,Int8,UInt64),UInt64)"


def bind(name,value,body):
    return f'arrayElement(arrayMap({name}->{body},[{value}]),1)'


def lifecycle_fold(events,seed,lower='lower',upper='upper',tick='tick'):
    # event = (known_at_us, completed close, causal completed-minute range).
    beyond=f'if(state.1>0,event.2<{lower}-greatest({tick},event.3*.25),event.2>{upper}+greatest({tick},event.3*.25))'
    contact=f'event.2>={lower} AND event.2<={upper}'
    flip=f'if(state.1>0,event.2<{lower}-{tick},event.2>{upper}+{tick})'
    rejected=f'if(state.1>0,event.2>{upper}+{tick},event.2<{lower}-{tick})'
    phase=f"toUInt8(multiIf(state.2=0,if(flags.1,1,0),state.2=1,if(flags.1,if(state.3>=1,2,1),0),state.2=2,if(flags.2,3,2),if(flags.3 OR flags.4,0,3)))"
    role='if(state.2=3 AND flags.3,toInt8(-state.1),state.1)'
    count='toUInt32(if(flags.1 AND state.2<=1,state.3+1,0))'
    life=f'tuple({role},{phase},{count})'
    score_event=f'tuple(event.2,{lower},{upper},life.1,event.3,life.2<=1,state.2=1 AND life.2=2,1.)'
    score=fold_sql(f'[{score_event}]','state.4')
    result=f'tuple(life.1,life.2,life.3,{score},if(life.1!=state.1,event.1,state.5))'
    body=bind('flags',f'tuple({beyond},{contact},{flip},{rejected})',bind('life',life,result))
    return f'arrayFold((state,event)->{body},{events},{seed})'


def lifecycle_fast(events,seed,lower='lower',upper='upper',tick='tick'):
    """Exact range elimination, not event downsampling or future score input."""
    no_contact=f'(extrema.1>{upper} OR extrema.2<{lower})'
    favorable=f'if(initial.1>0,extrema.1>{upper},extrema.2<{lower})'
    skip=f'(initial.2=2 AND {no_contact}) OR (initial.2=0 AND {favorable})'
    # Pass an empty array into the fold itself. Conditional laziness inside
    # nested higher-order lambdas is not a reliable performance boundary.
    reduced=compress_observations(f'if({skip},arraySlice({events},1,0),{events})',lower,upper,tick)
    full=lifecycle_fold(reduced,'initial',lower,upper,tick)
    distance=f'if(initial.1>0,extrema.2-{upper},{lower}-extrema.1)'
    best=f'if(initial.4.4=0,initial.4.2,greatest(initial.4.2,greatest({distance},0.)/if(initial.4.3>0,initial.4.3,1.)))'
    advanced=bind('best',best,'tuple(initial.1,toUInt8(0),toUInt32(0),tuple(initial.4.1,best,initial.4.3,if(initial.4.4!=0 AND best>=1,toUInt8(2),initial.4.4),initial.1,initial.4.6),initial.5)')
    pending='tuple(initial.1,initial.2,toUInt32(0),initial.4,initial.5)'
    decision=f'if(empty({events}),initial,if(initial.2=2 AND {no_contact},{pending},if(initial.2=0 AND {favorable},{advanced},{full})))'
    return bind('initial',seed,bind('extrema',f'tuple(arrayMin(arrayMap(v->v.2,{events})),arrayMax(arrayMap(v->v.2,{events})))',decision))


def compress_observations(events,lower,upper,tick):
    """Exact constant-input-region reduction for the completed-second machine.

    Keep first three, last, minimum and maximum observations per run. Three
    preserve crossing, acceptance and counter reset; extrema preserve reaction
    maxima. Every branch predicate must be constant within a run. This is not
    OHLC resampling: retained observations keep their original times and range.
    """
    code=f'toUInt8(z.2<{lower})+2*toUInt8(z.2>{upper})+4*toUInt8(z.2<{lower}-{tick})+8*toUInt8(z.2>{upper}+{tick})+16*toUInt8(z.2<{lower}-greatest({tick},z.3*.25))+32*toUInt8(z.2>{upper}+greatest({tick},z.3*.25))'
    minimum='arrayFirstIndex(z->z.2=limits.1,g)'
    maximum='arrayFirstIndex(z->z.2=limits.2,g)'
    keep=f'arraySort(arrayDistinct(arrayFilter(i->i>0 AND i<=length(g),[toUInt64(1),toUInt64(2),toUInt64(3),toUInt64(length(g)),toUInt64({minimum}),toUInt64({maximum})])))'
    groups='arraySplit((z,changed)->changed!=0,raw,arrayDifference(codes))'
    selected=bind('limits','tuple(arrayMin(arrayMap(w->w.2,g)),arrayMax(arrayMap(w->w.2,g)))',f'arrayMap(i->g[i],{keep})')
    result=f'arrayFlatten(arrayMap(g->{selected},{groups}))'
    return bind('raw',events,bind('codes',f'arrayMap(z->{code},raw)',result))



def canonical_splits(rows):
    """One action per date; duplicate delivery is not another split."""
    unique = {}
    for row in rows:
        day = row['execution_date']
        ratio = (float(row['split_from']), float(row['split_to']))
        if not all(isfinite(x) and x > 0 for x in ratio):
            raise ValueError('Invalid split ratio on '+day)
        previous = unique.get(day)
        if previous is not None and ratio != (float(previous['split_from']), float(previous['split_to'])):
            raise ValueError('Conflicting split ratios on '+day)
        if previous is None or str(row['inserted_at']) > str(previous['inserted_at']):
            unique[day] = dict(row)
    return [unique[day] for day in sorted(unique)]


def factor_sql(splits,ts):
    terms=[]
    for s in splits:
        boundary=f"toUnixTimestamp64Micro(toDateTime64('{s['execution_date']} 04:00:00',6,'America/New_York'))"
        terms.append(f"if({ts}>={boundary},{float(s['split_from'])/float(s['split_to']):.17g},1.)")
    return '*'.join(terms) or '1.'


def close_us(date):
    return f"toUInt64(toUnixTimestamp64Micro(toDateTime64('{date} 20:00:00',6,'America/New_York')))"


def run(args):
    root=args.runtime.resolve()
    runtime=Path(r'D:\TradingML\runtimes').resolve()
    if not runtime.is_dir() or not root.is_relative_to(runtime):
        raise ValueError('Required runtime root unavailable or invalid')
    if not re.fullmatch('[A-Z0-9._-]{1,32}',args.ticker):
        raise ValueError('Invalid ticker')
    root.mkdir(parents=True,exist_ok=True)
    report_path=root/'report.json'
    report=json.loads(report_path.read_text()) if report_path.exists() else {}
    code_hash=hashlib.sha256(b''.join(Path(__file__).with_name(name).read_bytes() for name in
        ['build_structure_book_clickhouse.py','structure_prominence_sql.py','prototype_structure_book_clickhouse.py'])).hexdigest()
    if report and report.get('code_hash')!=code_hash and not args.resume_compatible:
        raise ValueError('Controller changed; use a new immutable runtime')
    db=report.get('database','structure_book_'+uuid.uuid4().hex[:12])
    if not re.fullmatch('structure_book_[a-f0-9]{12}',db): raise ValueError('Invalid database')
    c=P.Client(args.env_file,args.threads)
    done=set(report.get('completed',[]))
    report.update(database=db,version=VERSION,status='building',code_hash=code_hash,ticker=args.ticker,
        workers=args.workers,threads=args.threads,requested_start=args.start,requested_end=args.end)
    times=report.setdefault('phase_seconds',{})
    def save():
        report['completed']=sorted(done)
        P.save(report_path,report)
    def query(label,sql,read=True,seconds=120):
        (root/(label+'.sql')).write_text(sql,encoding='utf-8')
        return c.query(sql,label,read,seconds)
    def stage(label,sql,seconds=120):
        sql_path=root/(label+'.sql')
        if label in done:
            if sql_path.read_text()!=sql: raise ValueError('Completed SQL changed: '+label)
            return
        start=time.perf_counter()
        query(label,sql,False,seconds)
        times[label]=time.perf_counter()-start
        done.add(label); save()
    def schemas():
        definitions={
            'trades':('sip_timestamp_us UInt64,ordinal UInt64,price Float64,raw_price Float64,factor Float64,size_primary Float64','ordinal'),
            'buckets':('timeframe LowCardinality(String),horizon_ms UInt32,bucket_ms Int64,high Float64,low Float64,high_us UInt64,low_us UInt64,first_us UInt64,first_ordinal UInt64,volume Float64,trades UInt64','(timeframe,bucket_ms)'),
            'splits':('effective_ms Int64','effective_ms'),
            'candidates':('timeframe LowCardinality(String),side Int8,price Float64,pivot_us UInt64,confirmed_us UInt64,confirmed_ordinal UInt64,bucket_ms Int64,may_found_level UInt8,split_crossing UInt8','(timeframe,confirmed_us,confirmed_ordinal,side,bucket_ms)'),
            'levels':('level_id UInt64,price Float64,lower Float64,upper Float64,tick Float64,side Int8,born_us UInt64,pivot_us UInt64,timeframe LowCardinality(String)','(born_us,level_id)'),
            'observations':('session_date Date,start_us UInt64,known_us UInt64,price Float64,prior_range Float64','(session_date,known_us)'),
            'history':(f'session_date Date,level_id UInt64,state {STATE}','(session_date,level_id)'),
            'baseline':('session_index UInt16,session_date Date,level_id UInt64,price Float64,lower Float64,upper Float64,side Int8,lifecycle LowCardinality(String),pending_side Int8,created_ms Int64,confirmed_ms Int64,reference_index UInt32','(session_date,reference_index)'),
            'book':("ticker LowCardinality(String),book_version LowCardinality(String),level_id UInt64,price Float64,lower Float64,upper Float64,side Int8,lifecycle LowCardinality(String),pending_side Int8,prominence Float64,born_us UInt64,confirmed_us UInt64,valid_from_us UInt64,valid_to_us Nullable(UInt64),revision UInt64",'(book_version,ticker,valid_from_us,level_id)'),
            'split_audit':('ticker String,effective_us UInt64,price_factor Float64,affected_rows UInt64,price_mismatches UInt64,before_hash UInt64,after_hash UInt64,source_revision String','(ticker,effective_us)'),
        }
        stage('database',f'CREATE DATABASE IF NOT EXISTS {db}')
        for name,(columns,order) in definitions.items():
            partition='PARTITION BY cityHash64(ticker)%32' if name=='book' else ''
            stage('schema_'+name,f"CREATE TABLE IF NOT EXISTS {db}.{name} ({columns}) ENGINE=ReplacingMergeTree {partition} ORDER BY {order} SETTINGS storage_policy='live_market_ssd'")
        tables=query('table_policy',f"SELECT name,storage_policy FROM system.tables WHERE database='{db}'")
        if not set(definitions).issubset({x['name'] for x in tables}) or any(x['storage_policy']!='live_market_ssd' for x in tables):
            raise ValueError('Wrong table policy')
        if int(query('placement',f"SELECT count() n FROM system.parts WHERE active AND database='{db}' AND disk_name!='live_market_ssd'")[0]['n']):
            raise ValueError('Wrong physical storage placement')
    try:
        days_sql=f"SELECT source_date,event_count,next_ordinal,last_ordinal,first_sip_timestamp_us,last_sip_timestamp_us,build_step,updated_at FROM market_sip_compact.events_ordinal_continuity FINAL WHERE ticker={P.literal(args.ticker)} AND source_date BETWEEN '{args.start}' AND '{args.end}' ORDER BY source_date"
        days=query('coverage',days_sql)
        if not days: raise ValueError('No certified ticker coverage')
        end=days[-1]['source_date']
        rules_sql="SELECT token_id,modifier_int,update_high_low,update_last,update_volume FROM market_sip_compact.event_condition_token_reference WHERE source_family='trade_conditions' AND is_join_canonical=1 ORDER BY token_id"
        split_sql=f"SELECT execution_date,split_from,split_to,inserted_at FROM q_live.market_stock_split_v1 FINAL WHERE provider_ticker={P.literal(args.ticker)} AND execution_date BETWEEN '{args.start}' AND '{end}' ORDER BY execution_date"
        baseline_sql=f"SELECT session_date,max(built_at) AS latest_built_at,count() n,argMax(certification_json,built_at) AS certification FROM q_live.qmd_structure_daily_checkpoint_v2 WHERE checkpoint_set_id={P.literal(args.checkpoint_set)} AND sym={P.literal(args.ticker)} AND algorithm_version=18 AND source_complete=1 AND session_date BETWEEN '{args.start}' AND '{end}' GROUP BY session_date ORDER BY session_date"
        rules=query('rules',rules_sql); split_rows=query('split_metadata',split_sql)
        splits=canonical_splits(split_rows)
        report['split_source_audit'] = dict(source_rows=split_rows, actions=len(splits), duplicate_rows=len(split_rows)-len(splits))
        baseline=[] if args.without_v18_comparison else query('baseline_metadata',baseline_sql)
        report['v18_comparison'] = 'not_requested' if args.without_v18_comparison else 'required'
        print('V18 comparison | '+report['v18_comparison'], flush=True)
        if not args.without_v18_comparison and ([x['session_date'] for x in baseline]!=[x['source_date'] for x in days] or any(int(x['n'])!=1 for x in baseline)):
            raise ValueError('Stored v18 coverage missing or ambiguous')
        previous=None
        for day,b in zip(days,baseline):
            cert=json.loads(b['certification'])
            if int(cert['event_evidence']['event_count'])!=int(day['event_count']): raise ValueError('Baseline source count mismatch')
            if previous and (cert['predecessor_checkpoint_sha256']!=previous['checkpoint_sha256'] or cert['predecessor_chain_sha256']!=previous['chain_sha256']):
                raise ValueError('Baseline chain mismatch')
            previous=cert
        manifest=[days,rules,splits,baseline]
        fingerprint=P.digest(manifest)
        if report.get('fingerprint',fingerprint)!=fingerprint: raise ValueError('Source changed; create a successor build')
        report.update(fingerprint=fingerprint,sessions=len(days),source_events=sum(int(x['event_count']) for x in days),actual_end=end)
        P.save(root/'source_manifest.json',manifest); save()
        policy=query('policy',"SELECT disks FROM system.storage_policies WHERE policy_name='live_market_ssd'")
        if not policy or any(x['disks']!=['live_market_ssd'] for x in policy): raise ValueError('Required SSD policy unavailable')
        schemas()
        actual=query('source_actual',f"SELECT toDate(fromUnixTimestamp64Micro(toInt64(sip_timestamp_us),'America/New_York')) d,count() n,uniqExact(ordinal) u,min(ordinal) first,max(ordinal) last,min(sip_timestamp_us) lo,max(sip_timestamp_us) hi FROM ({P.raw_events_sql(args.ticker,args.start,end)}) GROUP BY d ORDER BY d")
        bydate={x['d']:x for x in actual}
        if set(bydate)-{x['source_date'] for x in days}: raise ValueError('Uncertified source dates')
        for d in days:
            a=bydate.get(d['source_date'])
            if not a and int(d['event_count'])==0: continue
            expected=[int(d['event_count'])]*2+[int(d['next_ordinal'])-int(d['event_count']),int(d['last_ordinal']),int(d['first_sip_timestamp_us']),int(d['last_sip_timestamp_us'])]
            if not a or [int(a[k]) for k in ['n','u','first','last','lo','hi']]!=expected: raise ValueError('Canonical continuity mismatch')
        factor=factor_sql(splits,'sip_timestamp_us')
        source=P.events_sql(args.ticker,args.start,end,rules)
        stage('trades',f"INSERT INTO {db}.trades SELECT sip_timestamp_us,ordinal,price/({factor}),price,({factor}),if(volume_eligible,toFloat64(size_primary),0.) FROM ({source}) WHERE price_eligible")
        for s in splits:
            stage('split_'+s['execution_date'],f"INSERT INTO {db}.splits SELECT toUnixTimestamp64Milli(toDateTime64('{s['execution_date']} 04:00:00',3,'America/New_York'))")
        normalized=f"SELECT *,fromUnixTimestamp64Micro(toInt64(sip_timestamp_us),'America/New_York') local_time,1 price_eligible,1 volume_eligible FROM {db}.trades FINAL"
        def parallel(items):
            # Only the controller mutates the journal; independent SQL jobs run concurrently.
            pending_items=[(label,sql) for label,sql in items if label not in done]
            for label,sql in items:
                if label in done and (root/(label+'.sql')).read_text()!=sql:
                    raise ValueError('Completed SQL changed: '+label)
            with futures.ThreadPoolExecutor(max_workers=args.workers) as pool:
                pending={pool.submit(query,label,sql,False):label for label,sql in pending_items}
                for task in futures.as_completed(pending):
                    label=pending[task]
                    task.result(); done.add(label); save()
        started=time.perf_counter()
        parallel([(f'buckets_{f}',P.bucket_sql(f,h,normalized,db)) for f,h in P.FRAMES])
        times.setdefault('buckets_wall',time.perf_counter()-started)
        stage('detect',P.detector_sql(db))
        # Causal, stable address cells in the starting price basis. Geometry is
        # the actual first confirmed pivot, not a quantized displayed price.
        stage('levels',f"""INSERT INTO {db}.levels
          SELECT cityHash64(tuple(region,cell)),first.1,first.1-greatest(tick,first.1*.0005),first.1+greatest(tick,first.1*.0005),tick,first.2,first.3,first.4,first.5
          FROM (SELECT price<1 AS region,if(region,.0001,.01) tick,toInt64(round(price/(2*tick))) cell,
            argMin(tuple(price,side,confirmed_us,pivot_us,timeframe),tuple(confirmed_us,confirmed_ordinal,timeframe,side,pivot_us)) first
            FROM {db}.candidates FINAL WHERE may_found_level GROUP BY region,tick,cell)""")
        stage('observations',f"""INSERT INTO {db}.observations
          SELECT toDate(fromUnixTimestamp64Micro(toInt64(s.known_us),'America/New_York')),s.start_us,s.known_us,s.price,
            if(v.known_us=0,0.,greatest(v.prior_range,if(s.price*s.factor<1,.0001,.01)/s.factor))
          FROM (SELECT 1 AS join_key,toUInt64(intDiv(sip_timestamp_us,1000000)*1000000) start_us,start_us+1000000 known_us,
            argMax(price,ordinal) price,argMax(factor,ordinal) factor FROM {db}.trades FINAL GROUP BY start_us ORDER BY known_us) s
          ASOF LEFT JOIN (SELECT 1 AS join_key,toUInt64(bucket_ms+60000)*1000 known_us,avg(tr) OVER (ORDER BY bucket_ms ROWS BETWEEN 13 PRECEDING AND CURRENT ROW) prior_range FROM
            (SELECT b.bucket_ms,greatest(b.high-b.low,abs(b.high-lagInFrame(cl.price,1,cl.price) OVER w),abs(b.low-lagInFrame(cl.price,1,cl.price) OVER w)) tr
             FROM {db}.buckets AS b FINAL INNER JOIN (SELECT toInt64(intDiv(sip_timestamp_us,60000000)*60000) bucket_ms,argMax(price,ordinal) price FROM {db}.trades FINAL GROUP BY bucket_ms) cl USING bucket_ms
             WHERE b.timeframe='1m' WINDOW w AS (ORDER BY b.bucket_ms ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW)) ORDER BY known_us) v
          ON s.join_key=v.join_key AND s.known_us>=v.known_us""")
        # Days are sequential; levels inside each day are processed by ClickHouse.
        for i,day in enumerate(days):
            date=day['source_date']; label='day_'+date
            resumed=label in done
            prior=days[i-1]['source_date'] if i else '1970-01-01'
            events='arrayFilter(x->x.1-1000000>=l.born_us,observations)'
            seed=f"if(p.level_id=0,tuple(l.side,toUInt8(0),toUInt32(0),tuple(0.,0.,0.,toUInt8(0),toInt8(0),toUInt64(0)),l.born_us),p.state)"
            fold=lifecycle_fast(events,seed,'l.lower','l.upper','l.tick')
            stage(label,f"""INSERT INTO {db}.history SELECT toDate('{date}'),l.level_id,{fold}
              FROM {db}.levels AS l FINAL LEFT JOIN (SELECT level_id,state FROM {db}.history FINAL WHERE session_date='{prior}') p ON l.level_id=p.level_id
              CROSS JOIN (SELECT arraySort(x->x.1,groupArray(tuple(known_us,price,prior_range))) observations FROM {db}.observations FINAL WHERE session_date='{date}' AND known_us<={close_us(date)}) o
              WHERE l.born_us<={close_us(date)}""",seconds=900)
            if i%10==0 or i+1==len(days):
                print(f'Closing books | completed={i+1}/{len(days)} active=0 queued={len(days)-i-1} failed=0 | {date}'+(' (retained)' if resumed else ''),flush=True)
            if (root/'STOP').exists(): raise InterruptedError('STOP requested; completed units are journaled')
        times['lifecycle_total']=sum(v for k,v in times.items() if k.startswith('day_'))
        # Narrow v18 extraction is validation only and timed separately.
        started=time.perf_counter()
        baseline_jobs=[]
        for i,d in enumerate(days if not args.without_v18_comparison else []):
            sql=P.closing_sql(db,args.ticker,args.checkpoint_set,d['source_date'],i+1).replace(f'{db}.closes',f'{db}.baseline')
            sql=sql.replace("JSONExtractInt(t,'level','confirmed_at_ms')","JSONExtractInt(t,'level','confirmed_at_ms'),toUInt32(ref_index)")
            sql=sql.replace("ARRAY JOIN JSONExtractArrayRaw(snapshot_json,'unified_tracks') AS t","ARRAY JOIN JSONExtractArrayRaw(snapshot_json,'unified_tracks') AS t,arrayEnumerate(JSONExtractArrayRaw(snapshot_json,'unified_tracks')) AS ref_index")
            baseline_jobs.append((f'close_{d["source_date"]}',sql))
        parallel(baseline_jobs)
        times.setdefault('baseline_extraction_wall',time.perf_counter()-started)
        build_output(args,db,days,splits,stage,query,report)
        if P.digest([query('coverage_final',days_sql),query('rules_final',rules_sql),canonical_splits(query('splits_final',split_sql)),([] if args.without_v18_comparison else query('baseline_final',baseline_sql))])!=fingerprint:
            raise ValueError('Authority changed during build')
        report['storage']=query('storage',f"SELECT table,sum(rows) rows,sum(bytes_on_disk) disk_bytes,sum(data_uncompressed_bytes) raw_bytes,groupUniqArray(disk_name) disks FROM system.parts WHERE active AND database='{db}' GROUP BY table ORDER BY table")
        if any(x['disks']!=['live_market_ssd'] for x in report['storage']): raise ValueError('Part placement mismatch')
        report['status']='built_pending_quality_acceptance'
        report.pop('error',None)
    except BaseException as error:
        report.update(status='failed',error=str(error)); c.cancel(); raise
    finally:
        report['profiles']=report.get('profiles',[])+c.profiles
        save()
    print(f'Completed database build | {report_path}',flush=True)


def build_output(args,db,days,splits,stage,query,report):
    """Run-length encode closing states, then split intervals at action boundaries."""
    stage('schema_episodes',f"""CREATE TABLE IF NOT EXISTS {db}.episodes ENGINE=ReplacingMergeTree ORDER BY (level_id,from_us) SETTINGS storage_policy='live_market_ssd' AS
      SELECT l.level_id AS level_id,l.price AS price,l.lower AS lower,l.upper AS upper,h.state.1 side,h.state.2 phase,log(1+h.state.4.1+h.state.4.2) prominence,l.born_us,h.state.5 confirmed_us,
        toUInt64(toUnixTimestamp64Micro(toDateTime64(concat(toString(h.session_date),' 20:00:00'),6,'America/New_York'))) from_us
      FROM {db}.history AS h FINAL INNER JOIN {db}.levels AS l FINAL USING level_id WHERE 0""")
    stage('episodes',f"""INSERT INTO {db}.episodes
      SELECT level_id,price,lower,upper,side,phase,prominence,born_us,confirmed_us,from_us FROM
      (SELECT *,row_number() OVER w rn,lagInFrame(tuple(side,phase,prominence,confirmed_us),1) OVER w prev FROM
        (SELECT l.level_id AS level_id,l.price AS price,l.lower AS lower,l.upper AS upper,h.state.1 side,h.state.2 phase,log(1+h.state.4.1+h.state.4.2) prominence,l.born_us,h.state.5 confirmed_us,
         toUInt64(toUnixTimestamp64Micro(toDateTime64(concat(toString(h.session_date),' 20:00:00'),6,'America/New_York'))) from_us
         FROM {db}.history AS h FINAL INNER JOIN {db}.levels AS l FINAL USING level_id)
       WINDOW w AS (PARTITION BY level_id ORDER BY from_us ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW))
      WHERE rn=1 OR tuple(side,phase,prominence,confirmed_us)!=prev""")
    boundaries='['+','.join(f"toUInt64(toUnixTimestamp64Micro(toDateTime64('{s['execution_date']} 04:00:00',6,'America/New_York')))" for s in splits)+']'
    if not splits: boundaries="CAST([],'Array(UInt64)')"
    maximum='toUInt64(18446744073709551615)'
    factor=factor_sql(splits,'boundary')
    stage('book',f"""INSERT INTO {db}.book
      SELECT {P.literal(args.ticker)},{P.literal(VERSION)},level_id,price*({factor}),lower*({factor}),upper*({factor}),side,
        multiIf(phase=0,'active',phase=1,'crossed',phase=2,'awaiting_retest','retest_contact'),if(phase>=2,toInt8(-side),toInt8(0)),prominence,born_us,confirmed_us,
        boundary,if(ending={maximum},NULL,ending),toUInt64(1)
      FROM (SELECT *,boundaries[i] boundary,if(i<length(boundaries),boundaries[i+1],to_us) ending
        FROM (SELECT *,arrayConcat([from_us],arrayFilter(x->x>from_us AND x<to_us,{boundaries})) boundaries
          FROM (SELECT *,leadInFrame(from_us,1,{maximum}) OVER (PARTITION BY level_id ORDER BY from_us ROWS BETWEEN UNBOUNDED PRECEDING AND UNBOUNDED FOLLOWING) to_us FROM {db}.episodes FINAL))
        ARRAY JOIN arrayEnumerate(boundaries) AS i)""")
    for s in splits:
        timestamp=f"toUInt64(toUnixTimestamp64Micro(toDateTime64('{s['execution_date']} 04:00:00',6,'America/New_York')))"
        ratio=float(s['split_from'])/float(s['split_to'])
        stage('audit_'+s['execution_date'],f"""INSERT INTO {db}.split_audit SELECT {P.literal(args.ticker)},{timestamp},{ratio},count(),
          countIf(abs(b.price-a.price*{ratio})>1e-10*greatest(1.,abs(b.price)) OR abs(b.lower-a.lower*{ratio})>1e-10*greatest(1.,abs(b.lower)) OR abs(b.upper-a.upper*{ratio})>1e-10*greatest(1.,abs(b.upper)) OR a.prominence!=b.prominence),
          groupBitXor(cityHash64(tuple(a.level_id,a.price,a.lower,a.upper,a.prominence))),groupBitXor(cityHash64(tuple(b.level_id,b.price,b.lower,b.upper,b.prominence))),{P.literal(str(s['inserted_at']))}
          FROM (SELECT * FROM {db}.book FINAL WHERE valid_to_us={timestamp}) a INNER JOIN (SELECT * FROM {db}.book FINAL WHERE valid_from_us={timestamp}) b USING level_id""")
    report['split_audit']=query('split_audit',f'SELECT * FROM {db}.split_audit FINAL')
    if any(int(x['price_mismatches']) for x in report['split_audit']): raise ValueError('Split verification failed')
    report['counts']=query('counts',f"SELECT 'book' kind,count() n FROM {db}.book FINAL UNION ALL SELECT 'levels',count() FROM {db}.levels FINAL UNION ALL SELECT 'history',count() FROM {db}.history FINAL UNION ALL SELECT 'candidates',count() FROM {db}.candidates FINAL")
    # Price/role comparisons are coverage, not identity or exact v18 equivalence.
    day_factor=factor_sql(splits,"toUInt64(toUnixTimestamp64Micro(toDateTime64(concat(toString(session_date),' 20:00:00'),6,'America/New_York')))")
    stage('schema_daily_output',f"CREATE TABLE IF NOT EXISTS {db}.daily_output ENGINE=ReplacingMergeTree ORDER BY (session_date,side,price,level_id) SETTINGS storage_policy='live_market_ssd' AS SELECT h.session_date AS session_date,l.level_id AS level_id,l.price*({day_factor}) price,l.lower*({day_factor}) lower,l.upper*({day_factor}) upper,h.state.1 side,h.state.2 phase,log(1+h.state.4.1+h.state.4.2) prominence FROM {db}.history AS h FINAL INNER JOIN {db}.levels AS l FINAL USING level_id WHERE 0")
    stage('daily_output',f"INSERT INTO {db}.daily_output SELECT h.session_date,l.level_id,l.price*({day_factor}),l.lower*({day_factor}),l.upper*({day_factor}),h.state.1,h.state.2,log(1+h.state.4.1+h.state.4.2) FROM {db}.history AS h FINAL INNER JOIN {db}.levels AS l FINAL USING level_id")
    for label,left,right in ([] if args.without_v18_comparison else [('v18_coverage','baseline','daily_output'),('new_agreement','daily_output','baseline')]):
        report[label]=query(label,f"""WITH if(b.price=0,1e100,abs(a.price-b.price)) AS down,if(c.price=0,1e100,abs(a.price-c.price)) AS up,
            greatest(a.upper-a.price,a.price-a.lower,if(a.price<1,.0002,.02)) AS tolerance
          SELECT count() total,countIf(least(down,up)<=tolerance) matched,
          quantilesExact(.5,.9,.99)(least(down,up)/greatest(a.price,.0001)) relative_distance
          FROM (SELECT * FROM {db}.{left} FINAL ORDER BY session_date,side,price) a
          ASOF LEFT JOIN (SELECT session_date,side,price FROM {db}.{right} FINAL ORDER BY session_date,side,price) b ON a.session_date=b.session_date AND a.side=b.side AND a.price>=b.price
          ASOF LEFT JOIN (SELECT session_date,side,price FROM {db}.{right} FINAL ORDER BY session_date,side,price) c ON a.session_date=c.session_date AND a.side=c.side AND a.price<=c.price""",seconds=180)
    report['score_distribution']=query('scores',f"SELECT count() rows,countIf(prominence>0) positive,quantilesExact(0,.5,.9,.99,1)(prominence) quantiles FROM {db}.daily_output")


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--ticker',default='JUNS'); p.add_argument('--start',default='2025-01-01'); p.add_argument('--end',default=dt.date.today().isoformat())
    p.add_argument('--runtime',type=Path,required=True); p.add_argument('--threads',type=int,default=4); p.add_argument('--workers',type=int,default=2)
    p.add_argument('--env-file',type=Path,default=Path(r'\\DESKTOP-SAAI85T\Workstation-D\TradingML\secrets\.env'))
    p.add_argument('--checkpoint-set',default=P.SET)
    p.add_argument('--without-v18-comparison',action='store_true',help='Build from certified SIP without a v18 reference; parity remains untested')
    p.add_argument('--resume-compatible',action='store_true',help='Allow controller edits only when every completed SQL unit is unchanged')
    args=p.parse_args()
    if dt.date.fromisoformat(args.start)>dt.date.fromisoformat(args.end): p.error('Invalid date range')
    if not 1<=args.threads<=8 or not 1<=args.workers<=4: p.error('Require 1..8 threads and 1..4 workers')
    run(args)


if __name__=='__main__': main()
