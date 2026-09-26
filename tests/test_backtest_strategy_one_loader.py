"""Real arte column names feed Strategy 1 without JSON row materialization."""
from datetime import date

import pyarrow as pa
import pytest

from src.backend.backtest_market_data import (
    CertifiedMarketDayPlan, ExecutionInterval, MarketDayUnit, market_day_boundary,
)
from src.backend.backtest_strategy_one_loader import (
    _numpy, _table, load_strategy_one_entry_batch, load_strategy_one_entry_batches,
)


DAY = "2026-08-18"
TICKER = "TEST"
ATTEMPT = "00000000-0000-0000-0000-000000000001"


def plan():
    units = tuple(MarketDayUnit("build", DAY, TICKER, stage, ATTEMPT,
                                "source", 1, "hash") for stage in (
                                    "bars", "technical", "broker_100ms"))
    return CertifiedMarketDayPlan(
        ExecutionInterval.fixed(100), "build", "definition", (DAY,),
        (TICKER,), units, (100, 1_000, 5_000, 10_000, 30_000), "parent-token")


def source_tables():
    origin = int(market_day_boundary(date.fromisoformat(DAY), 0)
                 .timestamp() * 1_000_000)
    low = pa.table({
        "session_date": pa.array([date.fromisoformat(DAY)] * 2, type=pa.date32()),
        "ticker": [TICKER] * 2, "resolution_ms": [100] * 2,
        "boundary_ms": [30_000, 30_100], "close_int": [100_000, 100_100],
        "price_valid": [1, 1], "indicator_resolution_ms": [100, 100],
        "bid_int": [99_900, 100_000],
        "ask_int": [100_100, 100_200], "quote_valid": [1, 1],
        "quote_timestamp_us": [origin + 29_900_000, origin + 30_000_000],
        "execution_vwap": [9.5, 9.5], "previous_close": [9., 9.],
        "cumulative_volume": [30_000., 30_100.],
        "cumulative_notional": [300_000., 301_000.],
        "volume_trade_count": [30, 30]})
    higher = pa.table({
        "session_date": pa.array([date.fromisoformat(DAY)] * 4, type=pa.date32()),
        "ticker": [TICKER] * 4,
        "resolution_ms": [1_000, 5_000, 10_000, 30_000],
        "boundary_ms": [30_000] * 4, "macd_line": [.2] * 4,
        "macd_signal": [.1] * 4, "low_int": [97_000] * 4,
        "price_valid": [1] * 4, "extremes_valid": [1] * 4,
        "indicator_resolution_ms": [1_000, 5_000, 10_000, 30_000]})
    return low, higher


@pytest.mark.parametrize("values,dtype", [
    ([100_000.5], "int64"),
    ([-1], "uint8"),
    ([256], "uint8"),
])
def test_arrow_integer_projection_rejects_lossy_casts(values, dtype):
    with pytest.raises(ValueError, match="lossy or nonnumeric"):
        _numpy(pa.table({"value": values}), "value", fill=0, dtype=dtype)


def test_arrow_market_rows_feed_vectorized_candidate_gate():
    hundred, higher = source_tables()
    class Reader:
        queries = []
        def iter_arrow_record_batches(self, sql):
            assert sql.rstrip().endswith("FORMAT ArrowStream")
            self.queries.append(sql)
            table = hundred if "SELECT l.session_date" in sql else higher
            yield from table.to_batches(max_chunksize=2)
    reader = Reader()
    candidates = load_strategy_one_entry_batch(
        plan(), session_date=DAY, ticker=TICKER,
        through_boundary_ms=30_100, client=reader)
    assert len(reader.queries) == 2
    assert all("SELECT m.*" not in query for query in reader.queries)
    assert all("i.macd_line,i.macd_signal,i.previous_close" in query
               for query in reader.queries)
    assert candidates.entry_mask.tolist() == [True, True]
    assert candidates.stop_low_int.tolist() == [97_000, 97_000]
    assert candidates.macd_boundary_ms.tolist() == [[30_000] * 4] * 2


def test_two_tickers_share_two_arrow_queries_without_crossing_scope():
    hundred, higher = source_tables()
    second_hundred = hundred.set_column(
        hundred.schema.get_field_index("ticker"), "ticker", pa.array(["NEXT"] * 2))
    second_higher = higher.set_column(
        higher.schema.get_field_index("ticker"), "ticker", pa.array(["NEXT"] * 4))
    tables = (pa.concat_tables([second_hundred, hundred]),
              pa.concat_tables([second_higher, higher]))
    units = tuple(MarketDayUnit(
        "build", DAY, ticker, stage, ATTEMPT, "source",
        2 if stage == "broker_100ms" else 6, "hash")
        for ticker in ("NEXT", "TEST")
        for stage in ("bars", "technical", "broker_100ms"))
    scoped = CertifiedMarketDayPlan(
        ExecutionInterval.fixed(100), "build", "definition", (DAY,),
        ("NEXT", "TEST"), units, (100, 1_000, 5_000, 10_000, 30_000),
        "parent-token")

    class Reader:
        def __init__(self, *, omit_second_higher=False):
            self.queries = []
            self.omit_second_higher = omit_second_higher

        def iter_arrow_record_batches(self, sql):
            self.queries.append(sql)
            table = tables[0] if "SELECT l.session_date" in sql else tables[1]
            if self.omit_second_higher and table is tables[1]:
                table = higher
            yield from table.to_batches()

    reader = Reader()
    result = load_strategy_one_entry_batches(
        scoped, session_date=DAY, tickers=("NEXT", "TEST"),
        through_boundary_ms=30_100, client=reader)
    assert len(reader.queries) == 2
    assert set(result) == {"NEXT", "TEST"}
    assert all(batch.entry_mask.tolist() == [True, True] for batch in result.values())
    with pytest.raises(ValueError, match="omitted a pinned ticker"):
        load_strategy_one_entry_batches(
            scoped, session_date=DAY, tickers=("NEXT", "TEST"),
            through_boundary_ms=30_100, client=Reader(omit_second_higher=True))


def test_arrow_loader_rejects_unpinned_resolution_and_scope():
    hundred, higher = source_tables()
    class Reader:
        def iter_arrow_record_batches(self, sql):
            table = hundred if "SELECT l.session_date" in sql else higher
            if table is higher:
                table = table.set_column(
                    table.schema.get_field_index("ticker"), "ticker",
                    pa.array(["OTHER"] * 4))
            yield from table.to_batches()
    with pytest.raises(ValueError, match="pinned scope"):
        load_strategy_one_entry_batch(
            plan(), session_date=DAY, ticker=TICKER,
            through_boundary_ms=30_100, client=Reader())
    with pytest.raises(ValueError, match="resolutions are not certified"):
        load_strategy_one_entry_batch(
            plan().__class__(ExecutionInterval.fixed(100), "build", "definition",
                             (DAY,), (TICKER,), plan().units, (100, 1_000), "token"),
            session_date=DAY, ticker=TICKER,
            through_boundary_ms=30_100, client=Reader())


def test_arrow_loader_rejects_missing_indicator_on_price_bar():
    hundred, higher = source_tables()
    hundred = hundred.set_column(
        hundred.schema.get_field_index("indicator_resolution_ms"),
        "indicator_resolution_ms", pa.array([100, None], type=pa.int64()))
    class Reader:
        def iter_arrow_record_batches(self, sql):
            yield from (hundred if "SELECT l.session_date" in sql else higher).to_batches()
    with pytest.raises(ValueError, match="lacks a pinned indicator"):
        load_strategy_one_entry_batch(
            plan(), session_date=DAY, ticker=TICKER,
            through_boundary_ms=30_100, client=Reader())


def test_arrow_source_budget_failure_closes_stream():
    closed = []
    class Reader:
        def iter_arrow_record_batches(self, _sql):
            try:
                yield pa.record_batch([[1, 2]], names=["value"])
                pytest.fail("budget violation should stop before another batch")
            finally:
                closed.append(True)
    with pytest.raises(RuntimeError, match="memory budget"):
        _table(Reader(), "SELECT value FORMAT JSONEachRow", maximum_rows=1)
    assert closed == [True]
