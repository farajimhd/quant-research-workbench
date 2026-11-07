"""SELECT-only canonical-event aggregation. Raw market events stay in ClickHouse.

Every day scan is bounded by the frozen ticker/day ordinal index. Prices use
the canonical integer scale; eligible trade categories reuse the shared market
contract. Quotes use the shared structural pair-validity policy. No database
tables are created, and no historical flatfiles are read.
"""

from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from pipelines.market_sip.events.market_day_sql import literal
from research.bar_gpt.v5.bar_semantics_v1 import family_statistics, predicates, quote_carry_projection
from pipelines.market_sip.events.market_signals import token_sets
from .contract import FRAMES, seed_for

NY = ZoneInfo("America/New_York")


def clock(day, hour):
    """Resolve a local session boundary to UTC microseconds, including DST."""
    return int(datetime.combine(date.fromisoformat(str(day)), time(hour), NY).timestamp() * 1_000_000)


def source(ticker, bounds, begin, end):
    """Select a ticker's frozen ordinal ranges intersecting [begin,end)."""
    parts = []
    for row in bounds:
        if int(row["last_sip_timestamp_us"]) < begin or int(row["first_sip_timestamp_us"]) >= end:
            continue
        year = int(str(row["source_date"])[:4])
        parts.append(f"""SELECT * FROM market_sip_compact.events_{year}
            PREWHERE ticker={literal(ticker)} AND ordinal>={int(row['first_ordinal'])}
              AND ordinal<{int(row['next_ordinal'])}
            WHERE sip_timestamp_us>={begin} AND sip_timestamp_us<{end}""")
    if not parts:
        raise ValueError("No frozen ordinal coverage intersects the requested clock range.")
    return " UNION ALL ".join(parts)


def day_query(ticker, day, bounds, rules, initial_state, seed, keep_percent, previous_close=None):
    """Return selected consecutive observed fine bars and observed coarse bars.

    Clock buckets retain actual timestamps; observed_index supplies consecutive
    sequence positions before sampling. No synthetic empty clock slots exist.
    Fine windows are nonoverlapping, randomly offset, and selected by a stable
    hash. Coarse aggregates are later packed across sessions by NumPy only.
    Halts are decoded independently of price eligibility. State 2 means unknown.
    Only known 0→1 changes count; ambiguity invalidates the affected count.
    """
    opened, closed = clock(day, 4), clock(day, 20)
    previous = date.fromisoformat(str(day)) - timedelta(days=1)
    seed_clock = previous_close if previous_close is not None else clock(previous, 20)
    raw = source(ticker, bounds, seed_clock, closed)
    halt, resume = token_sets(rules)
    # Explicit trading pauses/circuit breakers supplement the shared mapping;
    # generic LULD bands and quotation-resumption messages do not change state.
    halt = sorted(set(halt) | {int(r["token_id"]) for r in rules
                               if r["source_family"] == "halt_reason"
                               and int(r["modifier_int"]) in (177, 181, 182, 183, 186)})
    resume = sorted(set(resume) | {int(r["token_id"]) for r in rules
                                   if r["source_family"] == "halt_reason"
                                   and int(r["modifier_int"]) == 178})
    specs = []
    for frame in FRAMES:
        step = frame if frame != FRAMES[-1] else closed - opened
        count = (closed - opened) // step
        offset = seed_for(seed, ticker, str(day), frame, "offset") % 64 if frame < FRAMES[-2] else 0
        salt = seed_for(seed, ticker, str(day), frame, "keep")
        specs.append(f"tuple(toUInt64({frame}),toUInt64({step}),toUInt64({count}),toUInt64({offset}),toUInt64({salt}))")
    frame_sql = "[" + ",".join(specs) + "]"
    regular_open = opened + (9*3600+30*60-4*3600)*1_000_000
    regular_close = clock(day, 16)
    aggregates, reducers, value_names, mask_names = family_statistics()
    # Carry sufficient statistics through selection and the temporal joins.
    # Rejoining rolled at the end would expand and execute that CTE again.
    carried = [f"{side}_{field}" for side in ("trade", "bid", "ask")
               for field in ("o", "h", "l", "c", "s", "n", "pv", "ev", "av")]
    empty_statistics = ["toUInt64(0)" if name.endswith("_n") else
                        "toUInt8(0)" if name.endswith(("_pv", "_ev", "_av")) else
                        "toFloat64(0)" for name in carried]
    aggregates.extend([f"countIf(hasAny(tokens,{halt+resume})) condition_events", "countIf(kind=0) quote_events"])
    reducers.extend(["sum(condition_events) condition_events", "sum(quote_events) quote_events"])
    value_names, mask_names = quote_carry_projection(value_names, mask_names)
    values = ",".join("toFloat32(" + n + ")" for n in value_names)
    masks = ",".join("toUInt8(" + n + ")" for n in mask_names)
    # Aggregate 100ms once. Rollups operate on these sufficient statistics, not
    # another six scans of the event stream. Only selected final bars leave SQL.
    # Keep a selected session tail even when it contains fewer than 64 bars;
    # NumPy adds masked right padding. Sessions shorter than 64 start at index 0.
    # All output boundaries align to 100ms. quote_clock can therefore retain
    # only each bucket's LAST actual quote (valid or invalid), ordered by both
    # timestamp and ordinal. Earlier intrabucket messages cannot affect an ASOF
    # lookup at any output end; observed OHLC/counts still use every eligible quote.
    # Frame zero is always returned as metadata and is never packed. This avoids
    # a second expansion of selected_grid solely to test whether it is empty.
    return f"""WITH
    raw AS ({raw}),
    decoded AS (
      SELECT *,bitAnd(event_meta,1) kind,
        toUInt64(price_primary_int)*if(bitAnd(event_meta,2)!=0,1,100) price_int,
        toUInt64(price_secondary_int)*if(bitAnd(event_meta,4)!=0,1,100) secondary_int,
        fromUnixTimestamp64Micro(toInt64(sip_timestamp_us),'America/New_York') local_time,
        toHour(local_time)*3600+toMinute(local_time)*60+toSecond(local_time) local_second,
        arrayFilter(t->t>0,[toUInt16(condition_token_1),toUInt16(condition_token_2),
          toUInt16(condition_token_3),toUInt16(condition_token_4),toUInt16(condition_token_5)]) tokens,
        {predicates(rules)}
      FROM raw WHERE sip_timestamp_us>={opened}),
    base AS (SELECT intDiv(sip_timestamp_us-{opened},100000) b,
      {','.join(aggregates)} FROM decoded GROUP BY b),
    rolled AS (SELECT f.1 frame,toUInt64(intDiv(b*100000,f.2)) bucket,{','.join(reducers)}
      FROM base ARRAY JOIN {frame_sql} AS f
      WHERE f.1!={FRAMES[-1]} OR (b*100000>={regular_open-opened} AND b*100000<{regular_close-opened})
      GROUP BY frame,bucket),
    observed AS (SELECT *,toUInt64(row_number() OVER (PARTITION BY frame ORDER BY bucket)-1) observed_index,
      count() OVER (PARTITION BY frame) observed_count
      FROM rolled WHERE trade_pv OR trade_ev OR trade_av OR bid_pv OR ask_pv OR condition_events>0 OR quote_events>0),
    selected_grid AS (SELECT toUInt8(1) join_key,r.frame frame,f.2 step,r.bucket bucket,r.observed_index observed_index,
      if(r.frame={FRAMES[-1]},toUInt64({regular_open}),{opened}+r.bucket*f.2) start_us,
      if(r.frame={FRAMES[-1]},toUInt64({regular_close}),{opened}+(r.bucket+1)*f.2) end_us,
      {','.join('r.'+name for name in carried)}
      FROM observed r INNER JOIN (SELECT arrayJoin({frame_sql}) f) specs ON r.frame=f.1
      WHERE r.frame>={FRAMES[-2]} OR (r.observed_index>=if(r.observed_count<64,0,f.4)
        AND cityHash64(toUInt64(intDiv(toInt64(r.observed_index)-toInt64(if(r.observed_count<64,0,f.4)),64)),f.5)%100<{int(keep_percent)})),
    grid AS (SELECT * FROM selected_grid UNION ALL
      SELECT toUInt8(1),toUInt64(0),toUInt64(0),toUInt64(0),toUInt64(0),toUInt64({opened}),toUInt64({opened}),
      {','.join(empty_statistics)}),
    states AS (SELECT sip_timestamp_us at_us,ordinal,
      multiIf(hasAny(tokens,{halt}) AND hasAny(tokens,{resume}),2,hasAny(tokens,{halt}),1,0) state
      FROM (SELECT *,arrayFilter(t->t>0,[toUInt16(condition_token_1),toUInt16(condition_token_2),
        toUInt16(condition_token_3),toUInt16(condition_token_4),toUInt16(condition_token_5)]) tokens FROM raw)
      WHERE hasAny(tokens,{halt+resume})
      UNION ALL SELECT toUInt64({seed_clock-1}),toUInt64(0),toUInt8({initial_state})),
    changes AS (SELECT *,lagInFrame(state,1,toUInt8(2)) OVER
      (ORDER BY at_us,ordinal ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW) previous FROM states),
    at_clock AS (SELECT toUInt8(1) join_key,at_us,argMax(state,ordinal) state FROM states GROUP BY at_us ORDER BY at_us),
    transitions AS (SELECT f.1 frame,toUInt64(intDiv(at_us-{opened},f.2)) bucket,
      countIf(state=1 AND previous=0) starts,
      countIf(state=2 OR (state=1 AND previous=2)) uncertain
      FROM changes ARRAY JOIN {frame_sql} AS f WHERE at_us>={opened}
        AND (f.1!={FRAMES[-1]} OR (at_us>={regular_open} AND at_us<{regular_close})) GROUP BY frame,bucket),
    quote_clock AS (SELECT toUInt8(1) join_key,max(sip_timestamp_us) at_us,
      argMax(tuple(toUInt8(q_ok),secondary_int,price_int,toFloat64(size_secondary),toFloat64(size_primary)),tuple(sip_timestamp_us,ordinal)) book
      FROM decoded WHERE kind=0 GROUP BY intDiv(sip_timestamp_us-{opened},100000) ORDER BY at_us),
    boundaries AS (SELECT g.*,o.state open_state,c.state close_state
      FROM (SELECT * FROM grid ORDER BY start_us) g
      ASOF LEFT JOIN at_clock o ON g.join_key=o.join_key AND g.start_us>o.at_us
      ASOF LEFT JOIN at_clock c ON g.join_key=c.join_key AND g.end_us>c.at_us)
    SELECT g.frame,g.bucket,g.end_us,g.observed_index AS observed_index,
      [{values},toFloat32(g.open_state=1),toFloat32(g.close_state=1),toFloat32(t.starts)] values,
      [{masks},toUInt8(g.open_state!=2),toUInt8(g.close_state!=2),
        toUInt8(g.open_state!=2 AND t.uncertain=0)] valid,
      (SELECT argMax(state,tuple(at_us,ordinal)) FROM states) final_state,
      (SELECT toString(tuple(countIf(kind=1),countIf(kind=1 AND bitAnd(event_meta,192)=0),
        countIf(kind=1 AND bitAnd(event_meta,128)!=0),countIf(usable))) FROM decoded) reporting_counts,
      (SELECT toString(tuple(count(),sum(cityHash64(tuple(*)))) ) FROM raw) source_hash
    FROM boundaries g
      LEFT JOIN transitions t ON g.frame=t.frame AND g.bucket=t.bucket
      ASOF LEFT JOIN quote_clock qb ON g.join_key=qb.join_key AND g.end_us>qb.at_us
    ORDER BY g.frame,g.bucket FORMAT ArrowStream"""
