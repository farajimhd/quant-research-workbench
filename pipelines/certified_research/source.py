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


def inventory(query,start: date,end: date,*,sessions=None):
    if type(start) is not date or type(end) is not date or start>end:
        raise ValueError('Invalid source inventory range')
    expected=sessions if sessions is not None else exchange_sessions(start,end)
    rows=query('SELECT * FROM market_sip_compact.events_source_day_stats FINAL '
               f'WHERE source_date BETWEEN toDate({literal(start)}) AND toDate({literal(end)}) ORDER BY source_date')
    by_day={}
    for row in rows:
        day=date.fromisoformat(row['source_date'])
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
                   f"AND source_start_date BETWEEN toDate({literal(start)}) AND toDate({literal(end)})) "
                   "WHERE updated_at=last_update GROUP BY day ORDER BY day")
    states={r['day']:r['statuses'] for r in imported}
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
    missing=[];verified={}
    for day in days:
        row=by_day.get(day)
        if row is None or row['status']!='complete':missing.append(day);continue
        if row['source_digest']!=source['source_hashes'].get(str(day)):
            raise ValueError(f'{day}: reporting receipt source mismatch')
        details=json.loads(row['details']);counts=details.get('counts',{})
        expected=source['rows'][day]
        if int(counts.get('n',-1))!=int(expected['trade_event_rows']) or int(counts.get('bad',-1))!=0:
            raise ValueError(f'{day}: reporting counts are invalid')
        verified[str(day)]=dict(source_digest=row['source_digest'],revision=REVISION,counts=counts)
        if details.get('evidence_version')=='original-reporting-nanosecond-evidence-v1':
            import re
            proof=details.get('evidence_hash','')
            if not re.fullmatch('[0-9a-f]{64}',proof):raise ValueError(f'{day}: native reporting input fingerprint migration required')
            verified[str(day)]['evidence_hash']=proof
    active=query("SELECT mutation_id FROM system.mutations WHERE database='market_sip_compact' "
                 "AND startsWith(table,'events_') AND NOT is_done LIMIT 1")
    return dict(missing=tuple(missing),verified=verified,mutating=bool(active),content_hash=digest(verified))


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
