"""Source-first, conservative quote-issue intervals for selective shard repair.

Inputs are frozen ticker/session ordinal ranges and the shared condition rules.
Only final candidate intervals leave ClickHouse. Adjacent eligible extreme-trade
prices nominate quote events; carried quotes nominate trade events. These are
offline search hints, NEVER replacement prices or future-dependent input gates.
The existing completed-bar policy determines repairs after candidate selection.
"""

from datetime import datetime

import numpy as np

from .bar_semantics_v1 import predicates
from .quote_quality_v1 import POLICY
from .quote_rebuild_v1 import NY
from .shards.range_sql import event_source
from .shards.sql import clock


def statement(ticker, bounds, rules):
    """Build a SELECT-only session-bounded candidate query over canonical events.

    ``bounds`` contains complete frozen NY sessions for one ticker. Window state
    resets each session, including invalid quote updates. Raw Float64 comparison
    uses a small conservative margin around the final Float32 tenfold threshold.
    False positives cost a candidate file read; false negatives are unacceptable.
    """
    if not bounds:
        raise ValueError("Candidate discovery requires frozen session bounds")
    days = [row["source_date"] for row in bounds]
    if days != sorted(set(days)):
        raise ValueError("Require unique ordered source sessions")
    raw = event_source(
        ticker, bounds, int(bounds[0]["first_ordinal"]), int(bounds[-1]["next_ordinal"])
    )
    limit = POLICY["maximum_price_multiple"] * (1 - 1e-5)
    order = "PARTITION BY session_open ORDER BY sip_timestamp_us,ordinal"
    previous = order + " ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW"
    # Reverse-prefix windows are linear accumulations. A forward window ending
    # at UNBOUNDED FOLLOWING can repeatedly recompute argMin over its suffix.
    reverse = "PARTITION BY session_open ORDER BY sip_timestamp_us DESC,ordinal DESC"
    following = reverse + " ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW"
    after = reverse + " ROWS BETWEEN UNBOUNDED PRECEDING AND 1 PRECEDING"

    def dislocated(bid, ask, reference):
        return f"({reference}.2 AND ({ask}>{reference}.1*{limit} OR {bid}*{limit}<{reference}.1))"

    quote_hint = f"old_ok AND (NOT q_ok OR price_int>secondary_int*{limit} OR {dislocated('secondary_int','price_int','previous_trade')} OR {dislocated('secondary_int','price_int','next_trade')})"
    trade_hint = f"e_ok AND old_book.1 AND (old_book.3>price_int*{limit} OR old_book.2*{limit}<price_int)"
    return f"""WITH decoded AS (SELECT *,bitAnd(event_meta,1) kind,
      toUInt64(price_primary_int)*if(bitAnd(event_meta,2)!=0,1,100) price_int,
      toUInt64(price_secondary_int)*if(bitAnd(event_meta,4)!=0,1,100) secondary_int,
      fromUnixTimestamp64Micro(toInt64(sip_timestamp_us),'America/New_York') local_time,
      toString(toDate(local_time)) source_date,
      toInt64(toUnixTimestamp64Micro(toDateTime64(concat(source_date,' 04:00:00'),6,'America/New_York'))) session_open,
      toHour(local_time)*3600+toMinute(local_time)*60+toSecond(local_time) local_second,
      arrayFilter(t->t>0,[toUInt16(condition_token_1),toUInt16(condition_token_2),toUInt16(condition_token_3),toUInt16(condition_token_4),toUInt16(condition_token_5)]) tokens,
      {predicates(rules)},
      kind=0 AND secondary_int>0 AND price_int>=secondary_int AND isFinite(size_primary) AND isFinite(size_secondary) AND size_primary>=0 AND size_secondary>=0 old_ok
      FROM ({raw}) WHERE sip_timestamp_us>={clock(days[0],4)} AND sip_timestamp_us<{clock(days[-1],20)}),
    projected AS (SELECT source_date,session_open,sip_timestamp_us,ordinal,kind,price_int,secondary_int,e_ok,q_ok,old_ok
      FROM decoded WHERE local_second>=14400 AND local_second<72000),
    history AS (SELECT *,
      argMaxIf(tuple(price_int,toUInt8(1)),tuple(sip_timestamp_us,ordinal),e_ok) OVER ({previous}) previous_trade,
      argMaxIf(tuple(toUInt8(old_ok),secondary_int,price_int),tuple(sip_timestamp_us,ordinal),kind=0) OVER ({previous}) old_book FROM projected),
    context AS (SELECT *,
      argMinIf(tuple(price_int,toUInt8(1)),tuple(sip_timestamp_us,ordinal),e_ok) OVER ({following}) next_trade,
      minIf(toInt64(sip_timestamp_us),kind=0) OVER ({after}) next_quote_us FROM history),
    nominated AS (SELECT *,({quote_hint}) quote_hint,({trade_hint}) trade_hint,
      toInt64(intDiv(sip_timestamp_us,100000)*100000) begin_us FROM context)
    SELECT source_date,begin_us,
      max(if(quote_hint,if(next_quote_us=0,session_open+57600000000,intDiv(next_quote_us,100000)*100000+100000),begin_us+100000)) end_us,
      countIf(quote_hint) quote_hints,countIf(trade_hint) trade_hints,
      countIf(old_ok AND NOT q_ok) source_changes
      FROM nominated WHERE quote_hint OR trade_hint GROUP BY source_date,begin_us ORDER BY source_date,begin_us FORMAT ArrowStream"""


def overlaps(closes, frame, intervals):
    """Return conservative candidate flags for Int64[N] completed bar clocks.

    Daily starts are regular-session 09:30 NY; all other bars start close-frame.
    Intervals include carry effects and are half-open. A boundary touch may cause
    an extra read, matching the existing reconstruction's conservative behavior.
    """
    closes = np.asarray(closes, dtype=np.int64)
    result = np.zeros(closes.shape, dtype=bool)
    if not intervals or not closes.size:
        return result
    begins = np.array([row["begin_us"] for row in intervals], dtype=np.int64)
    ends = np.maximum.accumulate(
        np.array([row["end_us"] for row in intervals], dtype=np.int64)
    )
    if np.any(np.diff(begins) < 0):
        raise ValueError("Candidate intervals must be ordered")
    starts = closes - frame
    if frame == 86400000000:
        starts = np.array(
            [
                clock(
                    datetime.fromtimestamp(int(t - 1) / 1e6, NY).date().isoformat(), 9
                )
                + 1800000000
                for t in closes
            ],
            dtype=np.int64,
        )
    previous = np.searchsorted(begins, closes, side="left") - 1
    return (previous >= 0) & (ends[np.maximum(previous, 0)] >= starts)


def file_candidate(first_start_us, last_end_us, intervals):
    """Prune a sealed file using its entire context envelope, never only origins.

    Missing bounds fail open to candidate processing. Gaps and overlapping saved
    windows can produce extra candidates but cannot exclude an affected bar.
    """
    if first_start_us is None or last_end_us is None:
        return True
    if not intervals:
        return False
    begins = np.array([row["begin_us"] for row in intervals], dtype=np.int64)
    ends = np.maximum.accumulate(
        np.array([row["end_us"] for row in intervals], dtype=np.int64)
    )
    previous = np.searchsorted(begins, int(last_end_us), side="left") - 1
    return bool(previous >= 0 and ends[max(previous, 0)] >= int(first_start_us))
