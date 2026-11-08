"""Selection-only adapter around the unchanged phase-one aggregation SQL.

Phase one samples arbitrary ordinal chunks and drops edge buckets. Phase two
reads complete source sessions, so its known 04:00–20:00 boundaries replace
those chunk-edge cuts. Aggregation, trade conditions, quote invalidation/carry,
daily regular hours and Float32 output expressions remain byte-for-byte shared.
"""

from ..shards.range_sql import range_query
from ..shards.sql import clock


def query(ticker, bounds, rules, coarse=False):
    """Return canonical bars for complete source-day rows in ``bounds``.

    Input rows are frozen ordinal-summary dictionaries for one contiguous span.
    Only the final five timeframes are returned; 100ms remains an internal
    sufficient-statistics level in the shared producer, never an output token.
    """
    first, stop = int(bounds[0]["first_ordinal"]), int(bounds[-1]["next_ordinal"])
    begin, end = clock(bounds[0]["source_date"], 4), clock(
        bounds[-1]["source_date"], 20
    )
    if coarse:
        return range_query(
            ticker, bounds, rules, first, stop, begin_us=begin, end_us=end
        )
    statement = range_query(ticker, bounds, rules, first, stop)
    replacements = {
        "[100000,1000000,10000000,60000000]": "[1000000,10000000,60000000]",
        "(SELECT minIf(sip_timestamp_us,core=1) FROM decoded)": str(begin),
        "(SELECT maxIf(sip_timestamp_us,core=1) FROM decoded)": str(end),
    }
    for old, new in replacements.items():
        if statement.count(old) != 1:
            raise ValueError("Shared SQL changed: selection adapter needs review")
        statement = statement.replace(old, new)
    return statement
