"""Physical query optimizations must preserve clocks, conditions and cache records."""

from collections import OrderedDict
from datetime import datetime, timezone
import json
import re
import sqlite3

import numpy as np
import polars as pl
import pytest

from .quote_query_v1 import prune_months
from .quote_rebuild_v1 import Rebuilder
from .shards.sql import clock


def sql(begin, end, year=2024):
    return (
        f"WITH decoded AS (SELECT * FROM (SELECT * FROM market_sip_compact.events_{year} "
        f"PREWHERE ticker='AAPL' AND ordinal>=7 AND ordinal<100) "
        f"WHERE sip_timestamp_us>={begin} AND sip_timestamp_us<{end}) SELECT * FROM decoded"
    )


def microseconds(value):
    return (
        int(datetime.fromisoformat(value).replace(tzinfo=timezone.utc).timestamp())
        * 1000000
    )


def test_exclusive_month_boundary_and_idempotence():
    original = sql(microseconds("2024-01-31T09:00"), microseconds("2024-02-01T00:00"))
    revised = prune_months(original)
    assert "IN (202401)" in revised and "202402" not in revised
    assert "ticker='AAPL' AND ordinal>=7 AND ordinal<100" in revised
    assert (
        revised.split("WHERE sip_timestamp_us", 1)[1]
        == original.split("WHERE sip_timestamp_us", 1)[1]
    )
    assert prune_months(revised) == revised


@pytest.mark.parametrize(
    "day", ["2024-01-31", "2024-03-10", "2024-11-03", "2024-12-31"]
)
def test_ny_session_utc_dates_and_cross_year_are_covered(day):
    begin, end = clock(day, 4), clock(day, 20)
    optimized = prune_months(sql(begin, end))
    months = set(map(int, re.search(r"IN \(([^)]+)\)", optimized)[1].split(",")))
    for timestamp in (begin, (begin + end) // 2, end - 1):
        date = datetime.fromtimestamp(timestamp // 1000000, timezone.utc)
        assert date.year * 100 + date.month in months


def test_unknown_query_shape_is_never_guessed():
    for original in (
        "SELECT 1",
        sql(0, 100),
        sql(100, 99),
        sql(1, 33 * 86400 * 1000000),
        sql(1, 100) + " AND sip_timestamp_us>=10 AND sip_timestamp_us<20",
    ):
        assert prune_months(original) == original


def test_large_scans_keep_the_measured_nonregressing_path():
    original = sql(microseconds("2024-01-02T09:00"), microseconds("2024-01-03T01:00"))
    large = original.replace("ordinal<100", "ordinal<1000000")
    assert prune_months(large) == large


def test_large_quote_batches_preserve_inverse_order_and_cache_hits():
    """Five thousand unique clocks need two source queries, retaining duplicates."""
    day = "2024-01-31"
    ends = clock(day, 10) + np.arange(1, 5001, dtype=np.int64) * 1000000
    requested = np.r_[ends[::-1], ends[:10]]

    def exercise(batch_size):
        builder = Rebuilder.__new__(Rebuilder)
        builder.binding = "test"
        builder.quote_batch_size = batch_size
        builder.bounds = OrderedDict(
            AAPL=[dict(source_date=day, first_ordinal=0, next_ordinal=100)]
        )
        builder.authority = dict(
            rules=[dict(source_family="quote_conditions", token_id=2, modifier_int=0)]
        )
        builder.db = sqlite3.connect(":memory:")
        builder.db.execute(
            "CREATE TABLE quotes(binding TEXT,ticker TEXT,frame INTEGER,end INTEGER,payload BLOB,PRIMARY KEY(binding,ticker,frame,end))"
        )
        builder.intervals = lambda *_: [
            dict(begin_us=clock(day, 4), end_us=clock(day, 20))
        ]
        calls = []

        def query(statement, *_, **__):
            clocks = json.loads(
                re.search(r"arrayJoin\((\[[0-9, ]+\])\) end_us", statement)[1]
            )
            calls.append(clocks)
            return pl.DataFrame(
                dict(
                    end_us=clocks,
                    values=[[float(t % 997)] * 12 for t in clocks],
                    valid=[[1] * 12 for _ in clocks],
                )
            )

        builder.query = query
        try:
            result = builder.get("AAPL", 1000000, requested)
            count = len(calls)
            assert builder.get("AAPL", 1000000, requested).tobytes() == result.tobytes()
            assert len(calls) == count
            payload = builder.db.execute(
                "SELECT payload FROM quotes LIMIT 1"
            ).fetchone()[0]
            builder.db.execute(
                "UPDATE quotes SET payload=? WHERE end=?",
                (payload + b"extra", int(ends[0])),
            )
            with pytest.raises(ValueError, match="byte layout"):
                builder.get("AAPL", 1000000, requested)
            return result.tobytes(), count
        finally:
            builder.db.close()

    before, old_queries = exercise(512)
    after, new_queries = exercise(4096)
    assert before == after and old_queries == 10 and new_queries == 2
