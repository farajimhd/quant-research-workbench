"""Freeze canonical source/reporting metadata and verify required session gaps."""
from __future__ import annotations

from datetime import date
import json

from pipelines.market_sip.events.trade_reporting_flags import REVISION
from .contracts import digest,literal


def exchange_sessions(start: date,end: date):
    import pandas_market_calendars as mcal
    return tuple(day.date() for day in mcal.get_calendar('XNYS').schedule(start_date=start,end_date=end).index)


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
        counts=json.loads(row['details']).get('counts',{})
        expected=source['rows'][day]
        if int(counts.get('n',-1))!=int(expected['trade_event_rows']) or int(counts.get('bad',-1))!=0:
            raise ValueError(f'{day}: reporting counts are invalid')
        verified[str(day)]=dict(source_digest=row['source_digest'],revision=REVISION,counts=counts)
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
