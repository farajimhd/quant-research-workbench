"""Repair-only physical query pruning; logical event/time predicates stay intact.

Canonical event_date is the UTC SIP date, copied from the ingestion-owned compact
source. Queries already constrain SIP microseconds to [begin,end). Restricting
the monthly partition expression to those UTC months therefore removes only
partitions outside the existing predicate, including NY sessions crossing UTC
midnight. Unknown SQL shapes are left unchanged, never guessed or truncated.
"""

from datetime import datetime, timezone
import re

TIME_RANGE = re.compile(r"sip_timestamp_us>=(\d+) AND sip_timestamp_us<(\d+)")
SOURCE = re.compile(
    r"SELECT \* FROM market_sip_compact\.events_20(?:19|2[0-5]) PREWHERE ticker="
)
ORDINAL_RANGE = re.compile(r"ordinal>=(\d+) AND ordinal<(\d+)")
MAX_PRUNE_ORDINALS = 250000  # Large-range date filtering regressed in the live pilot.


def ordered_candidates(sql):
    """Use prefix order instead of retaining/comparing a second ordering tuple.

    Only the exact generated candidate query is recognized. Its windows order
    every message by unique (timestamp, ordinal): the latest eligible prefix
    value is consequently identical to argMax ascending / argMin descending.
    Empty prefixes retain the same zero tuple. No WHERE or source span changes.
    """
    replacements = (
        (
            "argMaxIf(tuple(price_int,toUInt8(1)),tuple(sip_timestamp_us,ordinal),e_ok) OVER",
            "anyLastIf(tuple(price_int,toUInt8(1)),e_ok) OVER",
        ),
        (
            "argMaxIf(tuple(toUInt8(old_ok),secondary_int,price_int),tuple(sip_timestamp_us,ordinal),kind=0) OVER",
            "anyLastIf(tuple(toUInt8(old_ok),secondary_int,price_int),kind=0) OVER",
        ),
        (
            "argMinIf(tuple(price_int,toUInt8(1)),tuple(sip_timestamp_us,ordinal),e_ok) OVER",
            "anyLastIf(tuple(price_int,toUInt8(1)),e_ok) OVER",
        ),
    )
    if (
        "nominated AS (SELECT" not in sql
        or "ORDER BY sip_timestamp_us,ordinal ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW"
        not in sql
        or "ORDER BY sip_timestamp_us DESC,ordinal DESC ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW"
        not in sql
        or any(sql.count(before) != 1 for before, _ in replacements)
    ):
        return sql
    for before, after in replacements:
        sql = sql.replace(before, after)
    return sql


def prune_months(sql):
    """Add a redundant UTC-month PREWHERE to known generated repair SELECTs.

    Input/output are SQL strings; original ordinal, timestamp, condition and
    window clauses remain byte-identical. The exclusive end belongs to the
    previous microsecond, preventing an unnecessary next-month partition read.
    Reapplication is idempotent. No server setting or table is changed.
    """
    if not sql.startswith("WITH decoded AS (SELECT") or not SOURCE.search(sql):
        return sql
    ordinal_ranges = ORDINAL_RANGE.findall(sql)
    if len(ordinal_ranges) != len(SOURCE.findall(sql)) or any(
        not 0 < int(stop) - int(first) <= MAX_PRUNE_ORDINALS
        for first, stop in ordinal_ranges
    ):
        return sql
    ranges = TIME_RANGE.findall(sql)
    if len(ranges) != 1:
        return sql
    begin, end = map(int, ranges[0])
    if not 0 < begin < end or end - begin > 32 * 86400 * 1000000:
        return sql
    # Integer division avoids float rounding near a month boundary.
    first = datetime.fromtimestamp(begin // 1000000, timezone.utc)
    last = datetime.fromtimestamp((end - 1) // 1000000, timezone.utc)
    year, month = first.year, first.month
    months = []
    while (year, month) <= (last.year, last.month):
        months.append(str(year * 100 + month))
        year, month = (year + 1, 1) if month == 12 else (year, month + 1)
    clause = f"toYYYYMM(event_date) IN ({','.join(months)}) AND ticker="
    return SOURCE.sub(lambda match: match.group().replace("ticker=", clause), sql)
