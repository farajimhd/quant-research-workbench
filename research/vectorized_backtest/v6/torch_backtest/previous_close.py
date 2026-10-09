"""Read-only canonical closing-trade context; no prior tradability assertion."""
import os
os.environ['PYTHONDONTWRITEBYTECODE']='1'
import argparse
from datetime import date,timedelta
import json
from pathlib import Path
from .availability import configure_reader
from .materialize import prior_session,write_json,owned_run
from .runtime import require_runtime,file_hash
from .source import arte_source,arte_sql
from .source.common import digest

VERSION='v6-canonical-previous-close-v1'


def matched_identities(members,intervals,previous,current):
    """Effective identity intervals; never infer persistence from ticker alone."""
    matches=[];excluded=[]
    for member in members:
        spans=[row for row in intervals if row['listing_id']==member['listing_id']
            and row['ticker_normalized']==member['ticker'] and row['valid_from_date']<=previous
            and (row['valid_to_date_exclusive'] is None or row['valid_to_date_exclusive']>current)]
        if len(spans)==1:matches.append(dict(**member,interval=spans[0]))
        else:excluded.append(dict(listing_id=member['listing_id'],ticker=member['ticker'],reason='missing_or_ambiguous_effective_identity',matches=len(spans)))
    return matches,excluded


def canonical_close_sql(day,names,rules):
    from pipelines.market_sip.events import market_day_sql as sql
    from pipelines.market_sip.events.trade_reporting_flags import DELAYED
    _,last,_,_=sql.condition_expressions(rules)
    return f"""SELECT ticker,count() AS n,uniqExact(ordinal) AS unique_ordinals,
        min(ordinal) AS first_ordinal,max(ordinal) AS last_ordinal,
        min(sip_timestamp_us) AS first_us,max(sip_timestamp_us) AS last_us,
        sum(event_hash) AS hash,
        countIf(eligible) AS eligible_trades,
        argMaxIf(tuple(price_int,sip_timestamp_us,ordinal),tuple(sip_timestamp_us,ordinal),eligible) AS closing
      FROM (SELECT *,
        toUInt64(price_primary_int)*if(bitAnd(event_meta,2)!=0,1,100) AS price_int,
        toHour(fromUnixTimestamp64Micro(toInt64(sip_timestamp_us),'America/New_York'))*3600+
        toMinute(fromUnixTimestamp64Micro(toInt64(sip_timestamp_us),'America/New_York'))*60+
        toSecond(fromUnixTimestamp64Micro(toInt64(sip_timestamp_us),'America/New_York')) AS local_second,
        arrayFilter(t->t>0,[toUInt16(condition_token_1),toUInt16(condition_token_2),toUInt16(condition_token_3),
            toUInt16(condition_token_4),toUInt16(condition_token_5)]) AS tokens,
        false AS form_ok,
        bitAnd(event_meta,1)=1 AND bitAnd(event_meta,{DELAYED})=0 AND price_int>0
        AND isFinite(toFloat64(size_primary)) AND size_primary>0
        AND sip_timestamp_us>={sql.bounds(day,'09:30:00')} AND sip_timestamp_us<{sql.bounds(day,'16:00:00')}
        AND {last} AS eligible
        FROM (SELECT *,cityHash64(tuple(*)) AS event_hash FROM market_sip_compact.events_{day.year}
          WHERE event_date BETWEEN toDate({sql.literal(day)}) AND toDate({sql.literal(day+timedelta(days=1))})
          AND ticker IN ({','.join(sql.literal(n) for n in names)})
          AND sip_timestamp_us>={sql.bounds(day)} AND sip_timestamp_us<{sql.bounds(day+timedelta(days=1))}))
      GROUP BY ticker ORDER BY ticker"""


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--sessions',type=Path,required=True)
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--day',default='2026-07-30')
    args=parser.parse_args(argv);root=require_runtime(args.output)
    if (root/'complete.json').exists():raise ValueError('Completed context is immutable; verify and reuse it')
    spec=json.loads(args.sessions.read_text());selected=[s for s in spec['training'] if s['day']==args.day]
    if len(selected)!=1:raise ValueError('Context must be requested by a training session')
    item=selected[0];previous,_,closed=prior_session(item['day'])
    # Calendar authority supplies the exact RTH close; this contract currently
    # admits ordinary 16:00 closes only, rather than silently mishandling half days.
    from zoneinfo import ZoneInfo
    if closed.astimezone(ZoneInfo('America/New_York')).hour!=16:raise ValueError('Half-day close requires explicit SQL bounds')
    configure_reader(Path(__file__).resolve().parents[4])
    source=arte_source.load_build(item['source_manifest'],item['source_ledger'],[item['day']])
    reader=arte_source.reader(threads=1)
    try:members,population=arte_source.population(reader,source,date.fromisoformat(item['day']),regular_us_exchanges_only=True)
    finally:reader.close()
    # Producer credentials are used only with enforced readonly=1, through the
    # established read transport; no table creation or fallback to raw files.
    from scripts.build_market_day import Client,parse_args,reporting_coverage,checked_source_evidence
    from src.backend.backtest_market_data import readonly_clickhouse_client
    from research.mlops.clickhouse import ClickHouseHttpClient
    transport=readonly_clickhouse_client(v3_read_principal=True)
    producer=Client(parse_args(['--date',previous,'--plan-only','--max-threads','2']))
    client=ClickHouseHttpClient(transport.base_url,producer.http.user,producer.http.password,
        timeout_seconds=600,default_query_params=dict(readonly=1,max_threads=2,max_execution_time=550,
            max_memory_usage=2*1024**3,max_result_rows=100000,result_overflow_mode='throw'))
    class SelectReader:
        def query(self,statement,label=None):
            if not statement.lstrip().startswith('SELECT') or ';' in statement:raise ValueError('SELECT-only context')
            return [json.loads(line) for line in client.execute(statement+' FORMAT JSONEachRow').splitlines() if line]
    read=SelectReader()
    try:
      with owned_run(root):
        reporting=reporting_coverage(read,date.fromisoformat(previous),date.fromisoformat(previous))
        source_before=read.query(f"SELECT * FROM market_sip_compact.events_source_day_stats FINAL WHERE source_date={arte_sql.literal(previous)}")
        if len(source_before)!=1:raise ValueError('Require unique canonical source certificate')
        intervals=read.query(f"SELECT symbol_interval_id,listing_id,security_id,ticker_normalized,valid_from_date,valid_to_date_exclusive,source_event_id,source_content_sha256,observed_at_utc FROM q_live.id_symbol_interval_v1 FINAL WHERE mapping_status='mapped' AND is_deleted=0 AND valid_from_date<={arte_sql.literal(previous)} AND (valid_to_date_exclusive IS NULL OR valid_to_date_exclusive>{arte_sql.literal(item['day'])}) ORDER BY listing_id,symbol_interval_id")
        matched,excluded=matched_identities(members,intervals,previous,item['day'])
        if not matched:raise ValueError('No matched historical identities')
        continuity=read.query(f"SELECT * FROM market_sip_compact.events_ordinal_continuity FINAL WHERE source_date={arte_sql.literal(previous)} ORDER BY ticker")
        by_ticker={row['ticker']:row for row in continuity}
        if len(by_ticker)!=len(continuity):raise ValueError('Ambiguous canonical continuity')
        rules=read.query("SELECT token_id,modifier_int,update_high_low,update_last,update_volume FROM market_sip_compact.event_condition_token_reference WHERE source_family='trade_conditions' AND is_join_canonical=1 ORDER BY token_id")
        if not rules or len({row['token_id'] for row in rules})!=len(rules):raise ValueError('Ambiguous canonical trade rules')
        rows=[];names=sorted({row['ticker'] for row in matched if row['ticker'] in by_ticker})
        for offset in range(0,len(names),64):
            batch=read.query(canonical_close_sql(date.fromisoformat(previous),names[offset:offset+64],rules))
            if {row['ticker'] for row in batch}!=set(names[offset:offset+64]):raise ValueError('Missing canonical ticker bytes')
            for row in batch:checked_source_evidence(by_ticker[row['ticker']],row)
            rows.extend(batch)
            write_json(root/'status.json',dict(completed_tickers=len(rows),total_tickers=len(names),status='reading',validation_opened=False))
        source_after=read.query(f"SELECT * FROM market_sip_compact.events_source_day_stats FINAL WHERE source_date={arte_sql.literal(previous)}")
        if source_after!=source_before or reporting_coverage(read,date.fromisoformat(previous),date.fromisoformat(previous))!=reporting:raise ValueError('Canonical source changed during context read')
        payload=dict(version=VERSION,status='complete',current_day=item['day'],previous_day=previous,
            sessions_sha256=file_hash(args.sessions),source_manifest_sha256=file_hash(item['source_manifest']),
            population=population,source_certificate=source_before[0],reporting=reporting,rules=rules,
            matched=matched,excluded=excluded,rows=rows,validation_opened=False,
            identity_scope='effective historical mapping only; no previous-day tradability claim')
        write_json(root/'complete.json',dict(**payload,hash=digest(payload)))
        print(json.dumps(dict(status='complete',matched=len(matched),excluded=len(excluded),closing_trades=sum(row['eligible_trades']>0 for row in rows))),flush=True)
    finally:client.close();transport.close();producer.http.close();producer.cancel_http.close()
    return 0


if __name__=='__main__':raise SystemExit(main())
