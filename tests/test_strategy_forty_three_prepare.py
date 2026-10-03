from dataclasses import replace

import pyarrow as pa
import pytest

from pipelines.strategy_one.strategy_forty_three_prepare import completed_seconds
from pipelines.strategy_one.strategy_forty_three_publication import prepare_rows
from src.backend.backtest_market_data import MarketDayUnit
from tests.test_strategy_forty_three_publication import source
from tests.test_strategy_forty_three_source import fixture


def test_producer_extracts_one_certified_unit_without_filling_missing_prices(monkeypatch):
    _, arguments = fixture(monkeypatch)
    item = replace(source(), session_end_ms=30_000)
    market = arguments["market"]
    technical = MarketDayUnit(item.build_id, item.session_date, item.ticker,
        "technical", item.attempt_id, "f" * 64, 30, "f" * 64)
    market = replace(market, units=(*market.units, technical))
    queries = []
    class Reader:
        def iter_arrow_record_batches(self, sql):
            queries.append(sql)
            assert sql.startswith("SELECT ") and sql.endswith("FORMAT ArrowStream")
            if "arte.bars_v1" in sql:
                values = dict(boundary_ms=[1000, 3000], observed=[1, 1],
                    close=[10., 12.], low=[9., 11.], high=[11., 13.])
            else:
                values = dict(boundary_ms=[1000, 2000], dollar_volume=[1000., 500.])
            yield pa.RecordBatch.from_pydict(values)
    frame = completed_seconds(source=item, market=market, identity=arguments["identity"],
        structure=arguments["structure"], reader=Reader())
    facts, _, _ = prepare_rows(item, frame)
    assert len(facts) == 30
    assert facts[1]["close"] is None and facts[1]["observed"] == 0
    assert facts[1]["dollar_volume"] == 500.
    assert facts[2]["dollar_volume"] == 0.
    assert facts[2]["ten_second_mean_movement"] is None
    assert all("14400000" in sql for sql in queries)
    with pytest.raises(ValueError, match="parent"):
        completed_seconds(source=replace(item, bars_attempt_id=item.attempt_id), market=market,
            identity=arguments["identity"], structure=arguments["structure"], reader=Reader())
