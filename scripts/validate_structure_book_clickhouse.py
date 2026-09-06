#!/usr/bin/env python3
"""Validate an isolated ClickHouse book; fetch counts/profiles, never events."""
import argparse
import concurrent.futures as futures
import json
import os
from pathlib import Path
import re
import sys
import time
os.environ['PYTHONDONTWRITEBYTECODE']='1'
sys.dont_write_bytecode=True
import prototype_structure_book_clickhouse as P
import build_structure_book_clickhouse as B


def run(args):
    root=args.runtime.resolve()
    if not root.is_relative_to(Path(r'D:\TradingML\runtimes').resolve()): raise ValueError('Invalid runtime')
    report=json.loads((root/'report.json').read_text())
    if report.get('status')!='built_pending_quality_acceptance': raise ValueError('Complete the build before validation')
    db=report['database']
    if not re.fullmatch('structure_book_[a-f0-9]{12}',db): raise ValueError('Invalid isolated database')
    manifest=json.loads((root/'source_manifest.json').read_text())
    days,rules,splits,baseline=manifest
    c=P.Client(args.env_file,4)
    result={'database':db,'status':'validating','checks':{}}
    def q(name,sql,read=True,seconds=180):
        (root/('validation_'+name+'.sql')).write_text(sql,encoding='utf-8')
        return c.query(sql,name,read,seconds)
    def zero(name,sql):
        value=q(name,sql)
        result['checks'][name]=value
        if any(int(row['mismatches']) for row in value): raise ValueError(name+' failed')
    try:
        zero('initial_storage',f"SELECT (SELECT count() FROM system.tables WHERE database='{db}' AND storage_policy!='live_market_ssd')+(SELECT count() FROM system.parts WHERE active AND database='{db}' AND disk_name!='live_market_ssd') mismatches")
        dates='['+','.join(f"toDate('{d['source_date']}')" for d in days)+']'
        boundaries=f"SELECT session_date,toUInt64(toUnixTimestamp64Micro(toDateTime64(concat(toString(session_date),' 20:00:00'),6,'America/New_York'))) cutoff FROM (SELECT arrayJoin({dates}) session_date)"
        fields='session_date,level_id,price,lower,upper,side,phase,prominence'
        restored=f"SELECT d.session_date,b.level_id,b.price,b.lower,b.upper,b.side,toUInt8(multiIf(b.lifecycle='active',0,b.lifecycle='crossed',1,b.lifecycle='awaiting_retest',2,3)),b.prominence FROM {db}.book AS b FINAL CROSS JOIN ({boundaries}) d WHERE b.valid_from_us<=d.cutoff AND (b.valid_to_us IS NULL OR d.cutoff<b.valid_to_us)"
        expected=f'SELECT {fields} FROM {db}.daily_output FINAL'
        zero('interval_roundtrip',f'SELECT count() mismatches FROM (({expected} EXCEPT ALL {restored}) UNION ALL ({restored} EXCEPT ALL {expected}))')
        zero('interval_overlap',f"SELECT countIf(valid_from_us>=ifNull(valid_to_us,toUInt64(18446744073709551615)) OR valid_from_us<born_us OR (next!=0 AND ifNull(valid_to_us,toUInt64(18446744073709551615))!=next)) mismatches FROM (SELECT *,leadInFrame(valid_from_us,1,toUInt64(0)) OVER (PARTITION BY level_id ORDER BY valid_from_us ROWS BETWEEN UNBOUNDED PRECEDING AND UNBOUNDED FOLLOWING) next FROM {db}.book FINAL)")
        zero('future_confirmation',f"SELECT countIf(h.state.5>d.cutoff) mismatches FROM {db}.history AS h FINAL INNER JOIN ({boundaries}) d USING session_date")
        result['observation_window']=q('observation_window',f"SELECT count() observations,countIf(known_us>toUInt64(toUnixTimestamp64Micro(toDateTime64(concat(toString(session_date),' 20:00:00'),6,'America/New_York')))) after_close FROM {db}.observations FINAL")
        if int(result['observation_window'][0]['after_close']): raise ValueError('Out-of-session observations need an explicit policy')
        result['coverage']=q('coverage_counts',f'SELECT count() rows,uniqExact(session_date) sessions FROM {db}.history FINAL')
        if int(result['coverage'][0]['sessions'])!=len(days): raise ValueError('Missing closing sessions')
        for s in splits:
            timestamp=f"toUInt64(toUnixTimestamp64Micro(toDateTime64('{s['execution_date']} 04:00:00',6,'America/New_York')))"
            zero('split_'+s['execution_date'],f"SELECT toUInt64(abs((SELECT count() FROM {db}.book FINAL WHERE valid_to_us={timestamp})-(SELECT count() FROM {db}.book FINAL WHERE valid_from_us={timestamp})))+(SELECT sum(price_mismatches) FROM {db}.split_audit FINAL WHERE effective_us={timestamp}) mismatches")
        # Compare compression against every durably completed uncompressed day.
        if args.uncompressed_runtime:
            other=json.loads((args.uncompressed_runtime/'report.json').read_text())
            other_db=other['database']
            if not re.fullmatch('structure_book_[a-f0-9]{12}',other_db): raise ValueError('Invalid reference database')
            complete=[label[4:] for label in other['completed'] if label.startswith('day_')]
            selected='['+','.join(f"toDate('{d}')" for d in complete)+']'
            a=f'SELECT session_date,level_id,state FROM {db}.history FINAL WHERE has({selected},session_date)'
            b=f'SELECT session_date,level_id,state FROM {other_db}.history FINAL WHERE has({selected},session_date)'
            zero('compression_equivalence',f'SELECT count() mismatches FROM (({a} EXCEPT ALL {b}) UNION ALL ({b} EXCEPT ALL {a}))')
            result['uncompressed_reference_sessions']=len(complete)
        # Rebuild from midpoint/end prefixes and every actual split date.
        # These prevent hidden future-bucket dependence for any selected ticker.
        result['prefixes']=[]
        prefix_numbers=[]
        cuts = [days[len(days)//2]['source_date']+' 20:00:00', days[-1]['source_date']+' 12:00:00']
        cuts += [s['execution_date']+' 12:00:00' for s in splits]
        for number,cut in enumerate(dict.fromkeys(cuts)):
            if not (days[0]['source_date']<=cut[:10]<=days[-1]['source_date']): continue
            cutoff=f"toUInt64(toUnixTimestamp64Micro(toDateTime64('{cut}',6,'America/New_York')))"
            bucket=f'{db}.validation_buckets_{number}'
            candidates=f'{db}.validation_candidates_{number}'
            q('schema_prefix_buckets_'+str(number),f"CREATE TABLE IF NOT EXISTS {bucket} AS {db}.buckets ENGINE=ReplacingMergeTree ORDER BY (timeframe,bucket_ms) SETTINGS storage_policy='live_market_ssd'",False)
            q('schema_prefix_candidates_'+str(number),f"CREATE TABLE IF NOT EXISTS {candidates} AS {db}.candidates ENGINE=ReplacingMergeTree ORDER BY (timeframe,confirmed_us,confirmed_ordinal,side,bucket_ms) SETTINGS storage_policy='live_market_ssd'",False)
            zero('prefix_storage_'+str(number),f"SELECT (SELECT count() FROM system.tables WHERE database='{db}' AND name IN ('validation_buckets_{number}','validation_candidates_{number}') AND storage_policy!='live_market_ssd')+(SELECT count() FROM system.parts WHERE active AND database='{db}' AND table IN ('validation_buckets_{number}','validation_candidates_{number}') AND disk_name!='live_market_ssd') mismatches")
            source=f"SELECT *,1 price_eligible,1 volume_eligible,fromUnixTimestamp64Micro(toInt64(sip_timestamp_us),'America/New_York') local_time FROM {db}.trades FINAL WHERE sip_timestamp_us<={cutoff}"
            with futures.ThreadPoolExecutor(max_workers=2) as pool:
                jobs=[pool.submit(q,f'prefix_{number}_{frame}',P.bucket_sql(frame,h,source,db).replace(f'INSERT INTO {db}.buckets',f'INSERT INTO {bucket}'),False) for frame,h in P.FRAMES]
                for job in jobs: job.result()
            sql=P.detector_sql(db).replace(f'INSERT INTO {db}.candidates',f'INSERT INTO {candidates}').replace(f'FROM {db}.buckets',f'FROM {bucket} FINAL')
            q('prefix_detect_'+str(number),sql,False)
            fields='timeframe,side,price,pivot_us,confirmed_us,confirmed_ordinal'
            a=f'SELECT {fields} FROM {db}.candidates FINAL WHERE confirmed_us<={cutoff}'
            b=f'SELECT {fields} FROM {candidates} FINAL WHERE confirmed_us<={cutoff}'
            zero('prefix_'+str(number),f'SELECT count() mismatches FROM (({a} EXCEPT ALL {b}) UNION ALL ({b} EXCEPT ALL {a}))')
            observation_sql=(root/'observations.sql').read_text().split(f'INSERT INTO {db}.observations',1)[1]
            observation_sql=observation_sql.replace(f'{db}.buckets AS b FINAL',f'{bucket} AS b FINAL').replace(f'{db}.trades FINAL',f'(SELECT * FROM {db}.trades FINAL WHERE sip_timestamp_us<={cutoff})')
            # Positional union columns: the SELECT expression aliases need not
            # match the destination table's named columns.
            a=f'SELECT * FROM {db}.observations FINAL WHERE known_us<={cutoff}'
            b=f'SELECT * FROM ({observation_sql}) WHERE known_us<={cutoff}'
            zero('prefix_observations_'+str(number),f'SELECT count() mismatches FROM (({a} EXCEPT ALL {b}) UNION ALL ({b} EXCEPT ALL {a}))')
            result['prefixes'].append(cut)
            prefix_numbers.append(number)
        result['comparison_by_day']=[]
        result['v18_comparison'] = 'not_tested_no_reference' if report.get('v18_comparison') == 'not_requested' else 'performed'
        for direction,left,right in ([] if report.get('v18_comparison') == 'not_requested' else [('v18_coverage','baseline','daily_output'),('new_agreement','daily_output','baseline')]):
            # Count same-role coverage and price-only coverage separately. These
            # are nearest-neighbor, many-to-one metrics, not level identities.
            for same_role in [True,False]:
                role=' AND a.side=b.side' if same_role else ''
                role_c=' AND a.side=c.side' if same_role else ''
                rows=q(direction+('_role' if same_role else '_price'),f"""WITH if(b.price=0,1e100,abs(a.price-b.price)) AS down,if(c.price=0,1e100,abs(a.price-c.price)) AS up,
                    greatest(a.upper-a.price,a.price-a.lower,if(a.price<1,.0002,.02)) AS tolerance
                  SELECT a.session_date session_date,count() total,countIf(least(down,up)<=tolerance) matched
                  FROM (SELECT * FROM {db}.{left} FINAL ORDER BY session_date,side,price) a
                  ASOF LEFT JOIN (SELECT session_date,side,price FROM {db}.{right} FINAL ORDER BY session_date,side,price) b ON a.session_date=b.session_date{role} AND a.price>=b.price
                  ASOF LEFT JOIN (SELECT session_date,side,price FROM {db}.{right} FINAL ORDER BY session_date,side,price) c ON a.session_date=c.session_date{role_c} AND a.price<=c.price
                  GROUP BY session_date ORDER BY session_date""")
                result['comparison_by_day'].append({'direction':direction,'same_role':same_role,'rows':rows})
        # Idempotent publication test: a second identical insertion must not
        # change logical rows or values. ReplacingMergeTree reads use FINAL.
        digest_sql=f'SELECT count() n,groupBitXor(cityHash64(tuple(*))) hash FROM {db}.book FINAL'
        before=q('publication_before',digest_sql)
        q('publication_retry',f'INSERT INTO {db}.book SELECT * FROM {db}.book FINAL',False)
        after=q('publication_after',digest_sql)
        if before!=after: raise ValueError('Publication retry changed logical book')
        result['idempotent_publication']={'before':before,'after':after}
        # Merge only this isolated output's retry duplicates before measuring
        # physical storage. Never mutate the retained v18 checkpoint tables.
        q('compact_publication',f'OPTIMIZE TABLE {db}.book FINAL',False)
        if q('publication_compacted',digest_sql)!=before: raise ValueError('Compaction changed logical book')
        for number in prefix_numbers:
            for kind in ['buckets','candidates']:
                q(f'cleanup_prefix_{kind}_{number}',f'DROP TABLE IF EXISTS {db}.validation_{kind}_{number} SYNC',False)
        result['opening_queries']=[]
        for date in dict.fromkeys([days[len(days)//2]['source_date'], days[-1]['source_date'], *[s['execution_date'] for s in splits]]):
            cutoff=f"toUInt64(toUnixTimestamp64Micro(toDateTime64('{date} 04:00:00',6,'America/New_York')))"
            result['opening_queries'].append({'date':date,'result':q('opening_'+date,f"SELECT count() levels,min(price) low,max(price) high,sum(prominence) score_sum FROM {db}.book FINAL WHERE ticker={P.literal(report['ticker'])} AND book_version={P.literal(B.VERSION)} AND cityHash64(ticker)%32=cityHash64({P.literal(report['ticker'])})%32 AND valid_from_us<={cutoff} AND (valid_to_us IS NULL OR {cutoff}<valid_to_us)")})
        # Query-log collection is bounded and does not flush shared server logs.
        profiles=report.get('profiles',[])+c.profiles
        ids=list(dict.fromkeys(x['query_id'] for x in profiles if x['status']=='completed'))
        for attempt in range(4):
            server=q('server_profiles',"SELECT query_id,query_duration_ms,memory_usage,read_rows,read_bytes,written_rows,ProfileEvents['UserTimeMicroseconds'] cpu_us FROM system.query_log WHERE event_date>=today()-1 AND type='QueryFinish' AND query_id IN ("+','.join(map(P.literal,ids))+")")
            if len(server)>=len(ids): break
            time.sleep(2)
        result.update(server_profiles=server,expected_profiles=len(ids),status='passed')
        result['storage']=q('validated_storage',f"SELECT table,sum(rows) rows,sum(bytes_on_disk) disk_bytes,sum(data_uncompressed_bytes) raw_bytes,groupUniqArray(disk_name) disks FROM system.parts WHERE active AND database='{db}' GROUP BY table ORDER BY table")
        if any(x['disks']!=['live_market_ssd'] for x in result['storage']): raise ValueError('Wrong part placement')
    except BaseException as error:
        result.update(status='failed',error=str(error)); c.cancel(); raise
    finally:
        result['profiles']=c.profiles
        P.save(root/'validation.json',result)
    print('Validated | '+str(root/'validation.json'))


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--runtime',type=Path,required=True)
    p.add_argument('--uncompressed-runtime',type=Path)
    p.add_argument('--env-file',type=Path,default=Path(r'\\DESKTOP-SAAI85T\Workstation-D\TradingML\secrets\.env'))
    run(p.parse_args())
