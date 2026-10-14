"""Freeze canonical source/reporting metadata and verify required session gaps."""
from __future__ import annotations

from datetime import date
import json

from pipelines.market_sip.events.trade_reporting_flags import REVISION
from .contracts import digest,literal


def exchange_sessions(start: date,end: date):
    import pandas_market_calendars as mcal
    return tuple(day.date() for day in mcal.get_calendar('XNYS').schedule(start_date=start,end_date=end).index)


def market_day_hashes(canonical,reporting):
    """Bind each market day to canonical AND reporting evidence, not future days."""
    if canonical['missing'] or canonical['incomplete'] or reporting['missing'] or reporting['mutating']:
        raise ValueError('Market day bindings require complete frozen source/reporting prerequisites')
    result={}
    for day in canonical['expected']:
        key=str(day);source_hash=canonical['source_hashes'].get(key);proof=reporting['verified'].get(key)
        if not source_hash or proof is None or proof['source_digest']!=source_hash:
            raise ValueError('Market day reporting evidence differs from canonical source')
        result[day]=digest(dict(version='canonical-reporting-day-input-v1',day=day,canonical=source_hash,reporting=proof))
    return result


def certified_units(canonical,reporting):
    """Expose already prepared source days as verifiable execution roots.

    Acquisition and global canonical migrations finish before freezing a
    derived plan. These roots verify their resulting daily authorities; they
    never open files or implicitly invoke a provider or migration.
    """
    from .campaign import PlannedUnit
    from .execution import Unit
    hashes=market_day_hashes(canonical,reporting)
    plans=tuple(PlannedUnit(Unit('source:'+str(day),'canonical_source'),
        dict(day=str(day),source_hash=value)) for day,value in sorted(hashes.items()))
    return plans,{date.fromisoformat(p.arguments['day']):(p.unit.key,) for p in plans}


def require_exact_symbol(query,day,ticker,*,source_ticker=None):
    """Do not certify empty bars when normalization hides a source symbol.

    Uses the ingestion-certified compact ticker/day index, never events or
    retained files. This detects ambiguity; it does not invent an alias map.
    A genuinely absent source symbol remains a possible no-event session.
    """
    if type(day) is not date or not isinstance(ticker,str) or not ticker or len(ticker.encode())>64:
        raise ValueError('Invalid canonical source symbol check')
    expected=ticker if source_ticker is None else source_ticker
    if not isinstance(expected,str) or not expected or expected.upper()!=ticker.upper():
        raise ValueError('Explicit source spelling changes normalized symbol identity')
    rows=query('SELECT ticker,event_count FROM market_sip_compact.events_ticker_day_index FINAL '
        f'WHERE source_date=toDate({literal(day)}) AND upper(ticker)=upper({literal(ticker)}) ORDER BY ticker LIMIT 3')
    if len(rows)>1:raise ValueError('Canonical source symbol is ambiguous after normalization; explicit historical mapping is required')
    if rows and rows[0]['ticker']!=expected:
        raise ValueError('Canonical source ticker spelling differs from admitted symbol; explicit historical mapping is required')
    if rows and int(rows[0]['event_count'])<=0:raise ValueError('Canonical ticker/day index has an invalid event count')
    if not rows and expected!=ticker:raise ValueError('Explicit source spelling is absent from the canonical day index')
    return bool(rows)


def resolve_spellings(query,sessions,*,guard,batch_size=1024,maximum_sessions=250000):
    """Freeze unique case-only source matches in bounded metadata batches."""
    from dataclasses import replace
    from itertools import islice
    if (type(batch_size) is not int or not 1<=batch_size<=2048 or type(maximum_sessions) is not int
            or not 1<=maximum_sessions<=1000000):raise ValueError('Invalid source spelling metadata bounds')
    sessions=tuple(islice(sessions,maximum_sessions+1))
    if len(sessions)>maximum_sessions:raise ValueError('Source spelling discovery exceeds session bound')
    owners={};keys=set()
    for session in sessions:
        key=(session.day,session.ticker.upper());pair=(session.listing_key,session.day)
        if pair in keys or key in owners:raise ValueError('Competing admitted source-symbol assignments')
        keys.add(pair);owners[key]=session
    records={};ordered=sorted(owners);guard()
    for offset in range(0,len(ordered),batch_size):
        batch=ordered[offset:offset+batch_size];requested=set(batch)
        predicate=','.join(f'(toDate({literal(day)}),{literal(ticker)})' for day,ticker in batch)
        rows=query('SELECT source_date,ticker,event_count FROM market_sip_compact.events_ticker_day_index FINAL '
            f'WHERE (source_date,upper(ticker)) IN ({predicate}) ORDER BY source_date,ticker LIMIT {2*len(batch)+1}')
        if len(rows)>2*len(batch):raise ValueError('Source spelling result exceeds ambiguity bound')
        for row in rows:
            day=date.fromisoformat(str(row['source_date']));key=(day,row['ticker'].upper())
            if key not in requested or key in records:raise ValueError('Ambiguous or out-of-scope canonical source spelling')
            if int(row['event_count'])<=0:raise ValueError('Canonical source index has invalid event count')
            records[key]=dict(source_date=str(day),ticker=row['ticker'],event_count=int(row['event_count']))
        guard()
    mapped=[];spellings={};absent=[]
    for session in sessions:
        row=records.get((session.day,session.ticker.upper()))
        if row is None:
            if session.source_ticker is not None and session.source_ticker!=session.ticker:
                raise ValueError('Frozen source spelling has no canonical day evidence')
            absent.append((session.listing_key,session.day));mapped.append(session);continue
        raw=row['ticker']
        if session.source_ticker is not None and session.source_ticker!=raw:raise ValueError('Frozen source spelling changed')
        if raw!=session.ticker:spellings[(session.listing_key,session.day)]=raw
        mapped.append(replace(session,source_ticker=raw if raw!=session.ticker else None))
    evidence=sorted(records.values(),key=lambda r:(r['source_date'],r['ticker']))
    return dict(sessions=tuple(mapped),source_spellings=spellings,absent=tuple(absent),
        evidence_hash=digest(dict(version='case-source-index-v1',records=evidence,
            requested=[dict(day=d,ticker=t,listing=owners[(d,t)].listing_key) for d,t in ordered])))


def inventory(query,start: date,end: date,*,sessions=None):
    if type(start) is not date or type(end) is not date or start>end:
        raise ValueError('Invalid source inventory range')
    expected=sessions if sessions is not None else exchange_sessions(start,end)
    scope=''
    if sessions is not None:
        expected=tuple(sessions)
        if (not expected or tuple(sorted(set(expected)))!=expected
                or any(type(day) is not date or not start<=day<=end for day in expected)):
            raise ValueError('Explicit source sessions must be sorted unique dates within the source range')
        dates=','.join(f'toDate({literal(day)})' for day in expected)
        scope=f' AND source_date IN ({dates})'
    rows=query('SELECT * FROM market_sip_compact.events_source_day_stats FINAL '
               f'WHERE source_date BETWEEN toDate({literal(start)}) AND toDate({literal(end)}){scope} ORDER BY source_date')
    by_day={}
    for row in rows:
        day=date.fromisoformat(row['source_date'])
        if sessions is not None and day not in expected:raise ValueError('Explicit source read returned an unrequested session')
        if day in by_day:raise ValueError('Ambiguous source-day certificates')
        by_day[day]=row
    missing=tuple(day for day in expected if day not in by_day)
    # Ignore only operational certification-update clocks; all source/filter
    # metadata remains in the frozen identity.
    hashes={str(day):digest({k:v for k,v in row.items() if k!='updated_at'}) for day,row in by_day.items()}
    imported=query("SELECT day,groupUniqArray(status) statuses FROM ("
                   "SELECT toString(source_start_date) day,status,updated_at,"
                   "max(updated_at) OVER (PARTITION BY source_start_date) last_update "
                   "FROM market_sip_compact.events_build_manifest WHERE ticker='__ALL__' "
                   "AND source_start_date=source_end_date AND startsWith(target_table,'events_') "
                   f"AND source_start_date BETWEEN toDate({literal(start)}) AND toDate({literal(end)})"+
                   (f" AND source_start_date IN ({dates})" if sessions is not None else '')+
                   ") "
                   "WHERE updated_at=last_update GROUP BY day ORDER BY day")
    states={r['day']:r['statuses'] for r in imported}
    if sessions is not None and any(day not in {str(value) for value in expected} for day in states):
        raise ValueError('Explicit import-manifest read returned an unrequested session')
    incomplete=tuple(day for day in expected if day in by_day and states.get(str(day))!=['ok'])
    return dict(expected=expected,rows=by_day,missing=missing,source_hashes=hashes,
                incomplete=incomplete,import_statuses=states,content_hash=digest(hashes))


def reporting_inventory(query,source):
    days=source['expected']
    if not days:raise ValueError('No required source sessions')
    rows=query('SELECT source_date,revision,source_digest,status,details FROM '
               'q_live.historical_trade_reporting_coverage_v1 FINAL '
               f'WHERE source_date BETWEEN toDate({literal(min(days))}) AND toDate({literal(max(days))}) '
               f'AND revision={literal(REVISION)} ORDER BY source_date')
    by_day={}
    for row in rows:
        day=date.fromisoformat(row['source_date'])
        if day in by_day:raise ValueError('Ambiguous reporting coverage')
        by_day[day]=row
    missing=[];verified={};unqualified={}
    for day in days:
        row=by_day.get(day)
        if row is None or row['status']!='complete':missing.append(day);continue
        if row['source_digest']!=source['source_hashes'].get(str(day)):
            raise ValueError(f'{day}: reporting receipt source mismatch')
        details=json.loads(row['details']);counts=details.get('counts',{})
        expected=source['rows'][day]
        if int(counts.get('n',-1))!=int(expected['trade_event_rows']) or int(counts.get('bad',-1))!=0:
            raise ValueError(f'{day}: reporting counts are invalid')
        if details.get('evidence_version')!='original-reporting-nanosecond-evidence-v1':
            missing.append(day);unqualified[str(day)]='original_reporting_evidence_migration_required'
            continue
        verified[str(day)]=dict(source_digest=row['source_digest'],revision=REVISION,counts=counts)
        if details.get('evidence_version')=='original-reporting-nanosecond-evidence-v1':
            import re
            proof=details.get('evidence_hash','')
            if not re.fullmatch('[0-9a-f]{64}',proof):raise ValueError(f'{day}: native reporting input fingerprint migration required')
            verified[str(day)]['evidence_hash']=proof
    active=query("SELECT mutation_id FROM system.mutations WHERE database='market_sip_compact' "
                 "AND startsWith(table,'events_') AND NOT is_done LIMIT 1")
    return dict(missing=tuple(missing),verified=verified,unqualified=unqualified,mutating=bool(active),content_hash=digest(verified))


def require_ready(query,source,reporting):
    if source['missing'] or source['incomplete'] or reporting['missing'] or reporting['mutating']:
        raise ValueError('Canonical source/reporting prerequisites are incomplete or mutating')
    current=inventory(query,min(source['expected']),max(source['expected']),sessions=source['expected'])
    if current['content_hash']!=source['content_hash'] or current['incomplete']:
        raise ValueError('Canonical source inventory changed after plan freeze')
    fresh=reporting_inventory(query,current)
    if fresh['missing'] or fresh['mutating'] or fresh['content_hash']!=reporting['content_hash']:
        raise ValueError('Reporting prerequisites changed or became mutating after plan freeze')
    return digest(dict(source=source['content_hash'],reporting=reporting['content_hash']))


def clock_evidence_inventory(query,start,end):
    """Describe existing clock inputs without treating microsecond fallback as NS evidence."""
    if type(start) is not date or type(end) is not date or start>end:raise ValueError('Invalid clock evidence range')
    names=('historical_event_execution_clock_v1','historical_event_execution_clock_rest_v2')
    values=','.join(literal(n) for n in names)
    columns=query(f"SELECT table,name,type FROM system.columns WHERE database='q_live' AND table IN ({values}) ORDER BY table,position")
    policies=query(f"SELECT name,storage_policy FROM system.tables WHERE database='q_live' AND name IN ({values}) ORDER BY name")
    parts=query(f"SELECT table,disk_name,sum(rows) rows,sum(data_compressed_bytes) compressed_bytes FROM system.parts "
        f"WHERE active AND database='q_live' AND table IN ({values}) GROUP BY table,disk_name ORDER BY table,disk_name")
    for row in parts:
        row['rows']=int(row['rows']);row['compressed_bytes']=int(row['compressed_bytes'])
    coverage=[]
    available={r['name'] for r in policies}
    for name in names:
        if name not in available:continue
        coverage_table=name.replace('execution_clock_','execution_clock_coverage_')
        exists=query(f"SELECT count() n FROM system.tables WHERE database='q_live' AND name={literal(coverage_table)}")[0]['n']
        if not int(exists):continue
        rows=query(f'SELECT source_date,count() ticker_certificates,sum(trade_count) trades,sum(clock_count) clocks '
            f'FROM q_live.{coverage_table} FINAL WHERE source_date BETWEEN toDate({literal(start)}) AND toDate({literal(end)}) '
            'GROUP BY source_date ORDER BY source_date')
        coverage.append(dict(table=coverage_table,days=rows))
    return dict(columns=columns,policies=policies,parts=parts,coverage=coverage,reporting_ready=False,
        reason='Microsecond execution clocks with SIP fallback do not prove original participant-clock validity or nanosecond threshold edges. Reporting repair requires certified full reporting evidence; these counts alone cannot certify it.')


def corporate_evidence_inventory(query):
    """Current Gateway split rows are not original-public vintage certificates."""
    columns=query("SELECT name,type FROM system.columns WHERE database='q_live' AND table='market_stock_split_v1' ORDER BY position")
    parts=query("SELECT disk_name,sum(rows) rows,sum(data_compressed_bytes) compressed_bytes FROM system.parts "
        "WHERE active AND database='q_live' AND table='market_stock_split_v1' GROUP BY disk_name ORDER BY disk_name")
    for row in parts:
        row['rows']=int(row['rows']);row['compressed_bytes']=int(row['compressed_bytes'])
    names={r['name'] for r in columns}
    return dict(table='q_live.market_stock_split_v1',columns=columns,parts=parts,
        has_execution_date='execution_date' in names,has_local_insertion_time='inserted_at' in names,
        original_public_vintage_certified=False,
        reason='Execution date and insertion time do not prove original public availability, announcement history, confirmation or revision vintages. Certified float needs an independently qualified corporate parent.')
