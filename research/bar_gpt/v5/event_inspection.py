"""Read-only event-derived comparison charts; never consume shard bar payloads."""

from datetime import date, datetime, timedelta
from pathlib import Path

from pipelines.market_sip.events.market_day_sql import split_factor
from .bar_semantics_v1 import family_statistics, predicates
from .phase2_ar_shards import inspection
from .phase2_ar_shards.source import partition_page
from .shard_inspection import singleflight_cache
from .shards import source as sip
from .shards.balanced_plan import ticker_bounds
from .shards.contract import digest
from .shards.range_sql import event_source
from .shards.sql import clock

FRAMES = {"100ms":100_000,"1s":1_000_000,"5s":5_000_000,"10s":10_000_000,
          "30s":30_000_000,"1m":60_000_000,"5m":300_000_000,"1h":3_600_000_000}
CALENDAR = {"1d":"session", "1w":"toMonday(session)",
            "1mo":"toStartOfMonth(session)", "1y":"toStartOfYear(session)"}


@singleflight_cache(maxsize=4)
def bounds(root, ticker):
    plan = inspection._plan(root)
    job = next(j for j in plan["jobs"] if j["ticker"] == ticker)
    return ticker_bounds(Path(root)/"inventory",job)


def query(ticker, rows, rules, actions, timeframe, start_us, end_us, basis_day, limit):
    """Use shared trade eligibility/statistics, independently of saved bars.

    Calendar prices are normalized BEFORE OHLC aggregation, so a split inside
    a week/month cannot fabricate its extrema. Daily input is regular hours.
    """
    sources = []
    for year in sorted({r["source_date"][:4] for r in rows}):
        group = [r for r in rows if r["source_date"].startswith(year)]
        sources.append(partition_page(event_source(ticker,group,
            min(int(r["first_ordinal"]) for r in group),
            max(int(r["next_ordinal"]) for r in group)),group))
    raw = " UNION ALL ".join(sources)
    factor = split_factor(actions,basis_day,ticker,date_column="session")
    aggregate, _, _, _ = family_statistics()
    calendar = timeframe in CALENDAR
    bucket = CALENDAR[timeframe] if calendar else f"intDiv(sip_timestamp_us,{FRAMES[timeframe]})*{FRAMES[timeframe]}"
    opened = "toInt64(toUnixTimestamp64Micro(toDateTime64(concat(toString(bucket),' 09:30:00'),6,'America/New_York')))" if calendar else "toInt64(bucket)"
    # Actual completed session bounds, not artificial UTC midnight daily bars.
    closed = "max(toInt64(session_open)+43200000000)" if calendar else f"toInt64(bucket)+{FRAMES[timeframe]}"
    session_filter = "local_second>=34200 AND local_second<57600 AND toInt64(session_open)+43200000000<="+str(end_us) if calendar else "local_second>=14400 AND local_second<72000"
    return f"""WITH raw AS ({raw}), decoded AS (
      SELECT *,bitAnd(event_meta,1) kind,
        toUInt64(price_primary_int)*if(bitAnd(event_meta,2)!=0,1,100) price_int,
        toUInt64(price_secondary_int)*if(bitAnd(event_meta,4)!=0,1,100) secondary_int,
        fromUnixTimestamp64Micro(toInt64(sip_timestamp_us),'America/New_York') local_time,
        toDate(local_time) session,
        toUInt64(toUnixTimestamp64Micro(toDateTime64(concat(toString(session),' 04:00:00'),6,'America/New_York'))) session_open,
        toHour(local_time)*3600+toMinute(local_time)*60+toSecond(local_time) local_second,
        arrayFilter(t->t>0,[toUInt16(condition_token_1),toUInt16(condition_token_2),toUInt16(condition_token_3),toUInt16(condition_token_4),toUInt16(condition_token_5)]) tokens,
        {predicates(rules)} FROM raw
      WHERE bitAnd(event_meta,1)=1 AND sip_timestamp_us>={start_us} AND sip_timestamp_us<{end_us}),
      adjusted AS (SELECT * EXCEPT(price_int,size_primary),
        price_int*({factor}) price_int,toFloat64(size_primary)/({factor}) size_primary FROM decoded),
      bars AS (SELECT {bucket} bucket,{opened} start_us,{closed} end_us,
        {','.join(aggregate[:9])} FROM adjusted WHERE {session_filter} GROUP BY bucket)
      SELECT start_us,end_us,trade_o open,trade_h high,trade_l low,trade_c close,
        trade_s volume,trade_n trades FROM bars
      WHERE trade_pv AND trade_ev AND trade_n>0 AND end_us<={end_us}
      ORDER BY start_us DESC LIMIT {limit+1}"""


def _period_start(day, timeframe):
    if timeframe == "1w":return day-timedelta(days=day.weekday()+7*12)
    if timeframe == "1mo":return day.replace(year=day.year-1,month=day.month,day=1)
    if timeframe == "1y":return day.replace(year=max(2019,day.year-2),month=1,day=1)
    return day-timedelta(days=119)


@singleflight_cache(maxsize=8)
def chart(stream, unit, timeframe="1s", before_us=None, limit=4000):
    if timeframe not in FRAMES and timeframe not in CALENDAR:
        raise ValueError("Unsupported event-chart timeframe")
    if not 1 <= limit <= 50_000:
        raise ValueError("Invalid event-chart row budget")
    day = datetime.strptime(str(unit),"%Y%m%d").date()
    plan,entry = inspection._stream(stream)
    if day.year != entry["year"]:
        raise ValueError("Session does not belong to the selected ticker-year")
    basis_end = clock(day,20)
    end = basis_end if before_us is None else min(int(before_us),basis_end)
    if end <= 0:raise ValueError("Invalid history cursor")
    all_rows = bounds(str(inspection._root()),entry["ticker"])
    candidates = [r for r in all_rows if clock(r["source_date"],4)<end]
    if not candidates:
        return dict(bars=[],has_more=False,next_before_us=None,timeframe=timeframe,source="market_sip_compact.events_YYYY",basis=str(day))
    last_day = date.fromisoformat(candidates[-1]["source_date"])
    end = min(end,clock(last_day,20))
    first_day = _period_start(last_day,timeframe) if timeframe in CALENDAR else last_day
    start = clock(first_day,4)
    rows = [r for r in candidates if r["source_date"]>=str(first_day)]
    actions = [r for r in plan["authority"]["corporate_actions"]["rows"] if r["provider_ticker"]==entry["ticker"]]
    sql = query(entry["ticker"],rows,plan["authority"]["rules"],actions,timeframe,start,end,day,limit)
    client = sip.client(memory_gib=1)
    client.default_query_params.update(max_threads=1,max_execution_time=30)
    try:result = sip.rows(client,sql)
    finally:sip.close(client)
    extra = len(result)>limit
    result = list(reversed(result[:limit]))
    for bar in result:
        bar["start_us"],bar["end_us"] = int(bar["start_us"]),int(bar["end_us"])
        if not start <= bar["start_us"] < bar["end_us"] <= end:
            raise ValueError("Invalid canonical candle bounds")
    next_cursor = result[0]["start_us"] if extra and result else start
    has_more = any(clock(r["source_date"],4)<next_cursor for r in all_rows)
    return dict(bars=result,has_more=has_more,next_before_us=next_cursor if has_more else None,
        timeframe=timeframe,ticker=entry["ticker"],basis=str(day),source="market_sip_compact.events_YYYY",
        source_contract=plan["hash"],query_hash=digest(sql),time_basis="SIP availability; UTC clocks; NY sessions",
        price_basis="selected session; frozen split actions",window_start_us=start,window_end_us=end)

