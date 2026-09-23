"""Versioned ClickHouse-only market-day calculations. No market rows leave SQL.

Prices use a fixed 1e-4 integer scale (the lossless common canonical SIP scale).
Only complete, sparse trade buckets are persisted. Zero prices are invalid,
not synthetic candles. Event cursors are (sip_timestamp_us, ordinal).
"""
from __future__ import annotations

import math
import re
from datetime import timedelta
from pipelines.market_sip.events.trade_reporting_flags import DELAYED, REVISION as REPORTING_REVISION

VERSION = "market-day-core-v4"
EMAS = (7, 9, 12, 15, 20, 26, 50)
FRAMES = (100, 1000, 5000, 10000, 30000, 60000, 300000, 3600000)
POLICY = "live_market_ssd"
WARMUP_DAYS = 0


def literal(value):
    return "'" + str(value).replace("\\", "\\\\").replace("'", "\\'") + "'"


def identifier(value):
    if not re.fullmatch(r"[a-zA-Z_][a-zA-Z0-9_]*", value):
        raise ValueError("Invalid SQL identifier")
    return value


def table(db, name):
    return f"{identifier(db)}.market_day_{name}_v1"


def ddl(db):
    common = "build_id String, session_date Date, ticker LowCardinality(String), attempt_id UUID"
    definitions = {
        "events": (f"""{common}, sip_timestamp_us UInt64 CODEC(Delta,ZSTD(1)),
            ordinal UInt64 CODEC(Delta,ZSTD(1)), kind UInt8,
            price_int UInt64 CODEC(T64,ZSTD(1)), size Float64,
            price_valid UInt8, extremes_valid UInt8, volume_valid UInt8,
            execution_valid UInt8, quote_timestamp_us UInt64 CODEC(Delta,ZSTD(1)),
            bid_int UInt64, ask_int UInt64, bid_size Float64, ask_size Float64,
            cumulative_volume Float64, cumulative_notional Float64,
            execution_volume Float64, execution_notional Float64,
            execution_vwap Float64, spread Float64, nbbo_valid UInt8""",
            "build_id, session_date, ticker, attempt_id, sip_timestamp_us, ordinal"),
        "bars": (f"""{common}, resolution_ms UInt32, bucket_index UInt32,
            open_int UInt64, high_int UInt64, low_int UInt64, close_int UInt64,
            volume Float64, trade_count UInt64, notional Float64,
            execution_volume Float64, execution_notional Float64,
            price_valid UInt8, extremes_valid UInt8""",
            "build_id, session_date, ticker, attempt_id, resolution_ms, bucket_index"),
        "technical": (f"""{common}, resolution_ms UInt32, bucket_index UInt32,
            {','.join(f'ema_{p} Float64' for p in EMAS)},
            macd_line Float64, macd_signal Float64, macd_histogram Float64,
            rsi_14 Float64, atr_14 Float64, rsi_ready UInt8, atr_ready UInt8,
            previous_close Float64, sample_count UInt64,
            avg_gain Float64, avg_loss Float64""",
            "build_id, session_date, ticker, attempt_id, resolution_ms, bucket_index"),
        "seed": (f"""{common}, mode UInt8, predecessor_date String,
            prior_build_id String, prior_state_hash String""",
            "build_id, session_date, ticker, attempt_id"),
        "units": ("""build_id String, session_date Date, ticker LowCardinality(String),
            stage LowCardinality(String), attempt_id UUID, source_hash String,
            output_rows UInt64, output_hash UInt64, status LowCardinality(String),
            updated_at DateTime64(6,'UTC')""", "build_id, session_date, ticker, stage"),
        "builds": ("""build_id String, session_date Date, definition_json String,
            status LowCardinality(String), updated_at DateTime64(6,'UTC')""", "build_id"),
    }
    for name, (columns, order) in definitions.items():
        engine = "ReplacingMergeTree(updated_at)" if name in ("units","builds") else "MergeTree"
        yield f"""CREATE TABLE IF NOT EXISTS {table(db,name)} ({columns})
            ENGINE={engine} PARTITION BY toYYYYMM(session_date) ORDER BY ({order})
            SETTINGS storage_policy='{POLICY}'"""


def selection(build, day, ticker, attempt=None):
    result = f"build_id={literal(build)} AND session_date=toDate({literal(day)}) AND ticker={literal(ticker)}"
    return result + (f" AND attempt_id=toUUID({literal(attempt)})" if attempt else "")


def bounds(day, hour="00:00:00"):
    return f"toUInt64(toUnixTimestamp64Micro(toDateTime64('{day} {hour}',6,'America/New_York')))"


def canonical_source(day, ticker):
    # Winter after-hours crosses UTC midnight, including the yearly table edge.
    tomorrow=day+timedelta(days=1)
    years='|'.join(map(str,sorted({day.year,tomorrow.year})))
    return f"""SELECT * FROM merge('market_sip_compact','^events_({years})$')
        WHERE ticker={literal(ticker)} AND event_date BETWEEN toDate({literal(day)}) AND toDate({literal(tomorrow)})
        AND sip_timestamp_us>={bounds(day,'04:00:00')}
        AND sip_timestamp_us<{bounds(day,'20:00:00')}"""


def condition_expressions(rules):
    def tokens(predicate):
        return '[' + ','.join(str(int(r['token_id'])) for r in rules if predicate(r)) + ']'
    known = tokens(lambda r: True)
    form = tokens(lambda r: int(r['modifier_int']) == 12)
    last = tokens(lambda r: int(r['update_last']) == 1)
    high = tokens(lambda r: int(r['update_high_low']) == 1)
    volume = tokens(lambda r: int(r['update_volume']) == 1)
    form_ok = f"(local_second<34200 OR local_second>=57600) AND hasAny(tokens,{form}) AND arrayAll(t->has({form},t) OR (has({last},t) AND has({high},t)),tokens)"
    eligible = lambda allowed: f"arrayAll(t->has({known},t) AND (has({allowed},t) OR (form_ok AND has({form},t))),tokens)"
    return form_ok, eligible(last), eligible(high), eligible(volume)


def events_sql(db, build, day, ticker, attempt, rules, source=None):
    form, last, high, volume = condition_expressions(rules)
    source = source or canonical_source(day, ticker)
    window = "ORDER BY sip_timestamp_us,ordinal ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW"
    return f"""INSERT INTO {table(db,'events')}
    WITH decoded AS (
      SELECT *,bitAnd(event_meta,1) AS kind,
        toUInt64(price_primary_int)*if(bitAnd(event_meta,2)!=0,1,100) AS price_int,
        toUInt64(price_secondary_int)*if(bitAnd(event_meta,4)!=0,1,100) AS secondary_int,
        toFloat64(size_primary) AS size,
        fromUnixTimestamp64Micro(toInt64(sip_timestamp_us),'America/New_York') AS local_time,
        toHour(local_time)*3600+toMinute(local_time)*60+toSecond(local_time) AS local_second,
        arrayFilter(t->t>0,[toUInt16(condition_token_1),toUInt16(condition_token_2),
          toUInt16(condition_token_3),toUInt16(condition_token_4),toUInt16(condition_token_5)]) AS tokens,
        ({form}) AS form_ok,
        kind=1 AND bitAnd(event_meta,{DELAYED})=0 AND price_int>0 AND size>0 AND isFinite(size) AS usable,
        toUInt8(usable AND {last}) AS price_valid,
        toUInt8(usable AND {high}) AS extremes_valid,
        toUInt8(usable AND {volume}) AS volume_valid,
        kind=0 AND secondary_int>0 AND price_int>=secondary_int AS valid_quote
      FROM ({source})
    ), quoted AS (
      SELECT *,argMaxIf(tuple(sip_timestamp_us,secondary_int,price_int,
          toFloat64(size_secondary),size),tuple(sip_timestamp_us,ordinal),valid_quote) OVER ({window}) AS q
      FROM decoded
    ), classified AS (
      SELECT *,toUInt8(q.1>0 AND sip_timestamp_us>=q.1 AND intDiv(sip_timestamp_us-q.1,1000)<=1000) AS nbbo_valid,
        toUInt8(volume_valid AND nbbo_valid AND
          price_int+greatest(q.2,q.3,price_int)*1e-9>=q.2 AND
          price_int<=q.3+greatest(q.2,q.3,price_int)*1e-9) AS execution_valid
      FROM quoted
    ), cumulative AS (
      SELECT *,sumIf(size,volume_valid) OVER ({window}) AS cv,
        sumIf(price_int/10000.*size,volume_valid) OVER ({window}) AS cn,
        sumIf(size,execution_valid) OVER ({window}) AS ev,
        sumIf(price_int/10000.*size,execution_valid) OVER ({window}) AS en
      FROM classified
    ) SELECT {literal(build)},toDate({literal(day)}),{literal(ticker)},toUUID({literal(attempt)}),
      sip_timestamp_us,ordinal,kind,price_int,size,price_valid,extremes_valid,volume_valid,execution_valid,
      q.1,q.2,q.3,q.4,q.5,cv,cn,ev,en,if(ev>0,en/ev,0.),
      if(q.1>0,(q.3-q.2)/10000.,0.),nbbo_valid FROM cumulative"""


def base_sql(db, build, day, ticker, attempt):
    return f"""INSERT INTO {table(db,'bars')}
    SELECT build_id,session_date,ticker,attempt_id,toUInt32(100),
      toUInt32(intDiv(sip_timestamp_us-{bounds(day)},100000)) AS bucket,
      argMinIf(price_int,tuple(sip_timestamp_us,ordinal),price_valid),
      maxIf(price_int,extremes_valid),minIf(price_int,extremes_valid),
      argMaxIf(price_int,tuple(sip_timestamp_us,ordinal),price_valid),
      sumIf(size,volume_valid),countIf(volume_valid),sumIf(price_int/10000.*size,volume_valid),
      sumIf(size,execution_valid),sumIf(price_int/10000.*size,execution_valid),
      max(price_valid),max(extremes_valid)
    FROM {table(db,'events')} WHERE {selection(build,day,ticker,attempt)}
      AND kind=1 AND (price_valid OR extremes_valid OR volume_valid)
    GROUP BY build_id,session_date,ticker,attempt_id,bucket"""


def rollup_sql(db, build, day, ticker, attempt, parent, targets):
    if any(target % parent for target in targets):
        raise ValueError("Unaligned parent resolution")
    return f"""INSERT INTO {table(db,'bars')}
    SELECT build_id,session_date,ticker,attempt_id,toUInt32(target),
      toUInt32(intDiv(toUInt64(bucket_index)*{parent},target)) AS bucket,
      argMinIf(open_int,bucket_index,price_valid),maxIf(high_int,extremes_valid),
      minIf(low_int,extremes_valid),argMaxIf(close_int,bucket_index,price_valid),
      sum(volume),sum(trade_count),sum(notional),sum(execution_volume),sum(execution_notional),
      max(price_valid),max(extremes_valid)
    FROM {table(db,'bars')} ARRAY JOIN {list(targets)} AS target
    WHERE {selection(build,day,ticker,attempt)} AND resolution_ms={parent}
    GROUP BY build_id,session_date,ticker,attempt_id,target,bucket"""


def ema(expression, period, index="n", window="w", prior=None):
    alpha = 2. / (period + 1)
    half = math.log(.5) / math.log1p(-alpha)
    # Native EMA starts at zero. Scaling just the first input establishes the
    # same first-value seed as EmaState without unstable inverse exponentials.
    first = expression if prior is None else f"if(has_prior,({prior})*(1-{alpha:.17g})+({expression})*{alpha:.17g},({expression}))"
    return f"exponentialMovingAverage({half:.17g})(if({index}=1,({first})/{alpha:.17g},({expression})),{index}) OVER {window}"


def split_factor(splits, day, ticker, date_column='b.session_date'):
    terms=[]
    for row in splits:
        if row['provider_ticker']==ticker and str(row['execution_date'])<=str(day):
            ratio=float(row['split_from'])/float(row['split_to'])
            terms.append(f"if({date_column}<toDate({literal(row['execution_date'])}),{ratio:.17g},1.)")
    return '*'.join(terms) or '1.'


def technical_sql(db, build, day, ticker, attempt, prior=None):
    source = f"""SELECT b.session_date,b.resolution_ms,b.bucket_index,
      b.close_int,b.high_int,b.low_int
      FROM {table(db,'bars')} b
      INNER JOIN (SELECT session_date,attempt_id FROM {table(db,'units')} FINAL
        WHERE build_id={literal(build)} AND ticker={literal(ticker)} AND stage='bars' AND status='complete'
        AND session_date=toDate({literal(day)})) u
      ON b.session_date=u.session_date AND b.attempt_id=u.attempt_id
      WHERE b.build_id={literal(build)} AND b.ticker={literal(ticker)} AND b.price_valid=1 AND b.extremes_valid=1"""
    return technical_from_source(db, build, day, ticker, attempt, source, prior)


def technical_from_source(db, build, day, ticker, attempt, source, prior=None):
    prior=prior or {}
    frames=','.join(str(frame) for frame in FRAMES)
    has_prior=f"has([{frames}],resolution_ms)" if prior else '0'
    def state(name):
        values=[]
        for frame in FRAMES:
            value=float(prior.get(frame,{}).get(name,0.))
            if not math.isfinite(value): raise ValueError('Nonfinite persisted indicator state')
            values.append(f'{value:.17g}')
        return f"transform(resolution_ms,[{frames}],[{','.join(values)}],0.)"
    if prior and set(prior)!=set(FRAMES):
        raise ValueError('Incomplete persisted indicator state')
    ordered = "PARTITION BY resolution_ms ORDER BY session_date,bucket_index ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW"
    session = "PARTITION BY resolution_ms,session_date ORDER BY bucket_index ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW"
    decay = 13. / 14
    half = math.log(.5) / math.log(decay)
    def wilder(value, counter):
        # Exactly 14 seed samples, then the Wilder recurrence. During warmup
        # the published value is zero and readiness is false, matching QMD.
        seed = f"sumIf({value},{counter}<=14) OVER s / 14."
        tail = f"exponentialMovingAverage({half:.17g})(if({counter}>14,{value},0.),k) OVER s"
        return f"if({counter}>=14,({seed})*pow({decay:.17g},greatest(toInt64({counter})-14,0))+({tail}),0.)"
    return f"""INSERT INTO {table(db,'technical')}
    WITH numbered AS (
      SELECT *,close_int/10000. AS close,high_int/10000. AS high,low_int/10000. AS low,
        row_number() OVER ({ordered}) AS n,
        row_number() OVER ({session}) AS k,
        {has_prior} AS has_prior,
        lagInFrame(close_int/10000.,1,if(has_prior,{state('close')},0.)) OVER ({ordered}) AS prev
      FROM ({source})
    ), averages AS (
      SELECT *,{','.join(ema('close',p,prior=state(f'ema_{p}') if prior else None)+f' AS ema_{p}' for p in EMAS)},
        greatest(close-prev,0.) AS gain,greatest(prev-close,0.) AS loss,
        if(prev>0,greatest(high-low,abs(high-prev),abs(low-prev)),high-low) AS tr,
        k-if(first_value(prev) OVER s>0,0,1) AS changes
      FROM numbered WINDOW w AS ({ordered}),s AS ({session})
    ), smoothed AS (
      SELECT *,ema_12-ema_26 AS line,
        {wilder('if(prev>0,gain,0.)','changes')} AS ag,
        {wilder('if(prev>0,loss,0.)','changes')} AS al,
        {wilder('tr','k')} AS atr
      FROM averages WINDOW s AS ({session})
    ), signals AS (
      SELECT *,{ema('line',9,prior=state('macd_signal') if prior else None)} AS signal FROM smoothed WINDOW w AS ({ordered})
    ) SELECT {literal(build)},session_date,{literal(ticker)},toUUID({literal(attempt)}),resolution_ms,bucket_index,
      {','.join(f'ema_{p}' for p in EMAS)},line,signal,line-signal,
      if(changes<14,0.,if(al<=0,100.,100.-100./(1.+ag/al))),atr,
      toUInt8(changes>=14),toUInt8(k>=14),prev,k,ag,al
    FROM signals WHERE session_date=toDate({literal(day)})"""
