"""Canonical range aggregation for ordinal chunks and independent coarse spans.

Only completed trade-bearing bars leave ClickHouse. Intermediate buckets retain
independent ARTE price/extreme/volume contributions, including volume-only trades.
Quotes are carried after aggregation, never counted as invented updates.
"""

from research.bar_gpt.v5.bar_semantics_v1 import family_statistics, predicates
from .sql import literal


def event_source(ticker, bounds, first, stop):
    """Build a bounded UNION over frozen yearly ordinal intervals [first,stop)."""
    pieces = []
    years = sorted(
        {
            int(row["source_date"][:4])
            for row in bounds
            if int(row["next_ordinal"]) > first and int(row["first_ordinal"]) < stop
        }
    )
    for year in years:
        pieces.append(
            f"SELECT * FROM market_sip_compact.events_{year} "
            f"PREWHERE ticker={literal(ticker)} AND ordinal>={first} AND ordinal<{stop}"
        )
    if not pieces:
        raise ValueError("Requested ordinal interval is outside the frozen index")
    return " UNION ALL ".join(pieces)


def range_query(ticker, bounds, rules, first, stop, *, begin_us=None, end_us=None):
    """Return sparse final bars from an ordinal chunk or timestamp coarse span.

    first/stop are frozen UInt64 ordinal bounds. With begin_us/end_us, aggregate
    only hourly/daily bars inside that timestamp span. Otherwise return the four
    fine frames and discard partial clock buckets at both chunk boundaries.
    Quote seeds are the last actual quote before the range in its local session.
    Output values/masks have shape [bars,18]; timestamps are UTC microseconds.
    """
    raw = event_source(ticker, bounds, first, stop)
    # Seed only the last actual quote from preceding source days. Quotes reset
    # per NY session; no halt/status history is queried for phase one.
    prior = [r for r in bounds if int(r["first_ordinal"]) < first]
    prefix = (
        event_source(
            ticker,
            bounds,
            int(prior[-2 if len(prior) > 1 else -1]["first_ordinal"]),
            first,
        )
        if prior
        else None
    )
    seeds = (
        ""
        if prefix is None
        else f" UNION ALL SELECT *,toUInt8(0) core FROM (SELECT * FROM ({prefix}) WHERE bitAnd(event_meta,1)=0 ORDER BY ordinal DESC LIMIT 1)"
    )
    tokens = "arrayFilter(t->t>0,[toUInt16(condition_token_1),toUInt16(condition_token_2),toUInt16(condition_token_3),toUInt16(condition_token_4),toUInt16(condition_token_5)])"
    coarse = begin_us is not None
    frames = (
        "[3600000000,86400000000]" if coarse else "[100000,1000000,10000000,60000000]"
    )
    # For daily bars, the grouping key remains the NY date, not an epoch-day
    # interval; DST therefore cannot shift the regular-session boundaries.
    bucket = "if(frame=86400000000,session_open+19800000000,intDiv(sip_timestamp_us,frame)*frame)"
    aggregates, reducers, values, masks = family_statistics()
    if coarse:
        # Direct hour/day aggregation avoids materializing months of 100ms rows.
        base_key = "bucket"
        aggregation_input = f"SELECT *,arrayJoin({frames}) frame,{bucket} bucket FROM decoded WHERE in_session AND (core=1 OR kind=0)"
        aggregate_sql = ",".join(aggregates)
        regular = "(frame!=86400000000 OR (local_second>=34200 AND local_second<57600))"
        for flag in ("p_ok", "e_ok", "v_ok", "q_ok"):
            aggregate_sql = aggregate_sql.replace(flag, f"({flag} AND {regular})")
        rolled = "SELECT * FROM base"
    else:
        base_key = "intDiv(sip_timestamp_us,100000)*100000"
        aggregation_input = (
            "SELECT * FROM decoded WHERE in_session AND (core=1 OR kind=0)"
        )
        aggregate_sql = ",".join(aggregates)
        # Reducers consume original sufficient statistics, not filled OHLC bars.
        rolled = f"""SELECT frame,intDiv(b,frame)*frame bucket,session,
          any(session_open) session_open,{','.join(reducers)},
          argMax(book,b) book,sum(quote_events) quote_events
          FROM carried ARRAY JOIN {frames} AS frame GROUP BY frame,bucket,session"""
    grouping = "frame,bucket,session" if coarse else "b,session"
    identifiers = "frame,bucket,bucket b" if coarse else f"{base_key} b"
    quote_book = (
        "argMaxIf(tuple(toUInt8(q_ok),secondary_int,price_int,toFloat64(size_secondary),toFloat64(size_primary)),tuple(sip_timestamp_us,ordinal),kind=0"
        + (" AND (frame!=86400000000 OR local_second<57600)" if coarse else "")
        + ")"
    )
    # The last quote, including an invalidation, is propagated across sparse
    # buckets within the same NY session. No carry crosses a session boundary.
    carry_order = "bucket" if coarse else "b"
    carry_partition = "frame,session" if coarse else "session"
    if coarse:
        rolled = "SELECT * FROM carried"
    projected_values, projected_masks = list(values), list(masks)
    for off, side, p, size in ((6, "bid", 2, 4), (12, "ask", 3, 5)):
        price = f"if(book.1,book.{p}/10000.,0.)"
        for i, suffix in enumerate(("o", "h", "l")):
            projected_values[off + i] = f"if({side}_n>0,{side}_{suffix},{price})"
            projected_masks[off + i] = f"({side}_pv OR book.1)"
        projected_values[off + 3] = price
        projected_values[off + 4] = f"if(book.1,book.{size},0.)"
        projected_masks[off + 3] = projected_masks[off + 4] = "book.1"
    begin = (
        str(int(begin_us))
        if coarse
        else "(SELECT minIf(sip_timestamp_us,core=1) FROM decoded)"
    )
    end = (
        str(int(end_us))
        if coarse
        else "(SELECT maxIf(sip_timestamp_us,core=1) FROM decoded)"
    )
    value_sql = ",".join(f"toFloat32({v})" for v in projected_values)
    mask_sql = ",".join(f"toUInt8({v})" for v in projected_masks)
    return f"""WITH raw AS (SELECT *,toUInt8(1) core FROM ({raw}) {seeds}),
    decoded AS (SELECT *,bitAnd(event_meta,1) kind,
      toUInt64(price_primary_int)*if(bitAnd(event_meta,2)!=0,1,100) price_int,
      toUInt64(price_secondary_int)*if(bitAnd(event_meta,4)!=0,1,100) secondary_int,
      fromUnixTimestamp64Micro(toInt64(sip_timestamp_us),'America/New_York') local_time,
      toDate(local_time) session,
      toUInt64(toUnixTimestamp64Micro(toDateTime64(concat(toString(session),' 04:00:00'),6,'America/New_York'))) session_open,
      toHour(local_time)*3600+toMinute(local_time)*60+toSecond(local_time) local_second,
      local_second>=14400 AND local_second<72000 in_session,{tokens} tokens,
      {predicates(rules)} FROM raw),
    base AS (SELECT {identifiers},session,any(session_open) session_open,
      {aggregate_sql},{quote_book} actual_book,countIf(kind=0) quote_events
      FROM ({aggregation_input})
      GROUP BY {grouping}),
    carried AS (SELECT * EXCEPT(actual_book),
      argMaxIf(actual_book,{carry_order},quote_events>0) OVER
        (PARTITION BY {carry_partition} ORDER BY {carry_order} ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW) book
      FROM base),
    rolled AS ({rolled}),
    seeded AS (SELECT r.*,
      if(frame=86400000000,bucket+23400000000,bucket+frame) end_us,
      toUInt8(1) join_key FROM rolled r),
    selected AS (SELECT * FROM seeded
      WHERE trade_pv AND trade_ev AND trade_n>0 AND bucket>={begin} AND end_us<={end}),
    output AS (SELECT g.frame resolution,g.end_us end_us,
      [{value_sql}] values,[{mask_sql}] valid FROM selected g)
    SELECT * FROM output ORDER BY resolution,end_us FORMAT ArrowStream"""
