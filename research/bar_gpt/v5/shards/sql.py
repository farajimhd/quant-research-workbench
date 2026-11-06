"""SELECT-only canonical-event aggregation. Raw market events stay in ClickHouse.

Every day scan is bounded by the frozen ticker/day ordinal index. Prices use
the canonical integer scale; eligible trade categories reuse the shared market
contract. Quotes use the shared structural pair-validity policy. No database
tables are created, and no historical flatfiles are read.
"""

from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from pipelines.market_sip.events.market_day_sql import condition_expressions, literal
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
    """Return selected dense fine bars plus all 1h/1d aggregates for one session.

    Output rows are (frame,bucket,21 values,21 validity bits,end_us,final_state).
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
    trade_rules = [r for r in rules if r["source_family"] == "trade_conditions"]
    form, last, high, volume = condition_expressions(trade_rules)
    specs = []
    for frame in FRAMES:
        step = frame if frame != FRAMES[-1] else closed - opened
        count = (closed - opened) // step
        offset = seed_for(seed, ticker, str(day), frame, "offset") % 64 if frame < FRAMES[-2] else 0
        salt = seed_for(seed, ticker, str(day), frame, "keep")
        specs.append(f"tuple(toUInt64({frame}),toUInt64({step}),toUInt64({count}),toUInt64({offset}),toUInt64({salt}))")
    frame_sql = "[" + ",".join(specs) + "]"
    aggregates = []
    reducers = []
    value_names = []
    mask_names = []
    for side, price, size, price_ok, extreme_ok, activity_ok in (
        ("trade", "price_int", "size_primary", "p_ok", "e_ok", "v_ok"),
        ("bid", "secondary_int", "size_secondary", "q_ok", "q_ok", "q_ok"),
        ("ask", "price_int", "size_primary", "q_ok", "q_ok", "q_ok"),
    ):
        names = [f"{side}_{x}" for x in ("o", "h", "l", "c", "s", "n")]
        masks = [f"{side}_{x}" for x in ("pv", "ev", "av")]
        aggregates.extend([
            f"argMinIf({price},tuple(sip_timestamp_us,ordinal),{price_ok})/10000. {names[0]}",
            f"maxIf({price},{extreme_ok})/10000. {names[1]}",
            f"minIf({price},{extreme_ok})/10000. {names[2]}",
            f"argMaxIf({price},tuple(sip_timestamp_us,ordinal),{price_ok})/10000. {names[3]}",
            (f"sumIf(toFloat64({size}),{activity_ok})" if side == "trade" else
             f"argMaxIf(toFloat64({size}),tuple(sip_timestamp_us,ordinal),{activity_ok})") + f" {names[4]}",
            f"countIf({activity_ok}) {names[5]}",
            f"countIf({price_ok})>0 {masks[0]}", f"countIf({extreme_ok})>0 {masks[1]}",
            f"countIf({activity_ok})>0 {masks[2]}",
        ])
        reducers.extend([
            f"argMinIf({names[0]},b,{masks[0]}) {names[0]}",
            f"maxIf({names[1]},{masks[1]}) {names[1]}",
            f"minIf({names[2]},{masks[1]}) {names[2]}",
            f"argMaxIf({names[3]},b,{masks[0]}) {names[3]}",
            (f"sum({names[4]})" if side == "trade" else f"argMaxIf({names[4]},b,{masks[2]})") + f" {names[4]}",
            f"sum({names[5]}) {names[5]}",
            *(f"max({mask}) {mask}" for mask in masks),
        ])
        value_names.extend(names)
        # Zero counts/volume are known when source coverage is known; prices and
        # displayed size require an actual eligible observation in this interval.
        mask_names.extend([masks[0], masks[1], masks[1], masks[0],
                           "1" if side == "trade" else masks[2], "1"])
    values = ",".join("toFloat32(" + n + ")" for n in value_names)
    masks = ",".join("toUInt8(" + n + ")" for n in mask_names)
    # Aggregate 100ms once. Rollups operate on these sufficient statistics, not
    # another six scans of the event stream. Only selected final bars leave SQL.
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
        ({form}) form_ok,
        kind=1 AND price_int>0 AND size_primary>0 AND isFinite(size_primary) usable,
        usable AND {last} p_ok, usable AND {high} e_ok, usable AND {volume} v_ok,
        kind=0 AND secondary_int>0 AND price_int>=secondary_int AND
          isFinite(size_primary) AND isFinite(size_secondary) AND size_primary>=0 AND size_secondary>=0 q_ok
      FROM raw WHERE sip_timestamp_us>={opened}),
    base AS (SELECT intDiv(sip_timestamp_us-{opened},100000) b,
      {','.join(aggregates)} FROM decoded GROUP BY b),
    rolled AS (SELECT f.1 frame,toUInt64(intDiv(b*100000,f.2)) bucket,{','.join(reducers)}
      FROM base ARRAY JOIN {frame_sql} AS f GROUP BY frame,bucket),
    grid AS (SELECT toUInt8(1) join_key,f.1 frame,f.2 step,toUInt64(bucket) bucket,
      {opened}+bucket*step start_us,{opened}+(bucket+1)*step end_us
      FROM (SELECT arrayJoin({frame_sql}) f) ARRAY JOIN range(f.3) AS bucket
      WHERE f.1>={FRAMES[-2]} OR (bucket>=f.4 AND bucket<f.4+intDiv(f.3-f.4,64)*64
        AND cityHash64(toUInt64(intDiv(bucket-f.4,64)),f.5)%100<{int(keep_percent)})),
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
      FROM changes ARRAY JOIN {frame_sql} AS f WHERE at_us>={opened} GROUP BY frame,bucket),
    boundaries AS (SELECT g.*,o.state open_state,c.state close_state
      FROM (SELECT * FROM grid ORDER BY start_us) g
      ASOF LEFT JOIN at_clock o ON g.join_key=o.join_key AND g.start_us>o.at_us
      ASOF LEFT JOIN at_clock c ON g.join_key=c.join_key AND g.end_us>c.at_us)
    SELECT g.frame,g.bucket,g.end_us,
      [{values},toFloat32(g.open_state=1),toFloat32(g.close_state=1),toFloat32(t.starts)] values,
      [{masks},toUInt8(g.open_state!=2),toUInt8(g.close_state!=2),
        toUInt8(g.open_state!=2 AND t.uncertain=0)] valid,
      (SELECT argMax(state,tuple(at_us,ordinal)) FROM states) final_state,
      (SELECT toString(tuple(count(),sum(cityHash64(tuple(*)))) ) FROM raw) source_hash
    FROM boundaries g LEFT JOIN rolled r ON g.frame=r.frame AND g.bucket=r.bucket
      LEFT JOIN transitions t ON g.frame=t.frame AND g.bucket=t.bucket
    ORDER BY g.frame,g.bucket FORMAT ArrowStream"""
