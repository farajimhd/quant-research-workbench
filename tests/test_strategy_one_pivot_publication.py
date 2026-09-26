"""A pivot attempt becomes visible only after verified normalized child rows."""
import json
from uuid import UUID

import pytest

from pipelines.strategy_one import pivot_publication as producer
from src.backend.backtest_market_data import (
    CertifiedMarketDayPlan, ExecutionInterval, MarketDayUnit,
)
from src.trading_runtime.strategy_one_pivot_product import (
    PivotInterval, interval_content_hash,
)
from src.trading_runtime.strategy_one_pivot_schema import PRODUCT_DIGEST


DAY = "2026-08-18"
SOURCE = "00000000-0000-0000-0000-000000000001"
DERIVED = "00000000-0000-0000-0000-000000000002"
INTERVAL = PivotInterval("high", 101_000, 1_000_000, 2_000_000,
                         2_000, None)


def market():
    units = tuple(MarketDayUnit("build", DAY, "ABCD", stage, SOURCE,
                                "source", 100, "hash")
                  for stage in ("bars", "technical", "broker_100ms"))
    return CertifiedMarketDayPlan(ExecutionInterval.fixed(100), "build",
                                  "definition", (DAY,), ("ABCD",), units,
                                  (100, 1000, 5000, 10000, 30000), "token")


class Writer:
    def __init__(self):
        self.fact = None
        self.intervals = ()
        self.calls = []

    def execute(self, sql):
        self.calls.append(sql)
        if sql.startswith("SELECT") and "pivot_coverage" in sql:
            return json.dumps(self.fact) if self.fact else ""
        if sql.startswith("SELECT") and "pivot_interval" in sql:
            return "\n".join(json.dumps({
                "side": item.side, "price_int": item.price_int,
                "pivot_at_us": item.pivot_at_us,
                "confirmed_at_us": item.confirmed_at_us,
                "valid_from_boundary_ms": item.valid_from_boundary_ms,
                "valid_to_boundary_ms": item.valid_to_boundary_ms,
            }) for item in self.intervals)
        if sql.startswith("INSERT INTO arte.strategy_one_pivot_coverage_v1"):
            self.fact = {
                "derivation_attempt_text": DERIVED,
                "bars_attempt_text": SOURCE, "product_digest": PRODUCT_DIGEST,
                "interval_count": len(self.intervals),
                "content_hash": interval_content_hash(self.intervals),
            }
            return ""
        raise AssertionError(sql)


def test_child_is_verified_before_coverage_and_retry_skips(monkeypatch):
    writer = Writer()
    monkeypatch.setattr(producer, "uuid4", lambda: UUID(DERIVED))
    monkeypatch.setattr(producer, "iter_persisted_v7_seconds",
                        lambda *_args, **_kwargs: (_ for _ in ({},)))
    monkeypatch.setattr(producer, "derive_pivot_intervals",
                        lambda *_args, **_kwargs: (INTERVAL,))

    def inserted(_writer, _scope, _attempt, intervals):
        assert writer.fact is None
        writer.intervals = intervals
        writer.calls.append("INSERT child")

    monkeypatch.setattr(producer, "_insert_intervals", inserted)
    assert producer.publish_unit(writer, object(), market(),
                                 session_date=DAY, ticker="ABCD") == "published"
    assert writer.calls.index("INSERT child") < next(
        index for index, call in enumerate(writer.calls)
        if call.startswith("INSERT INTO arte.strategy_one_pivot_coverage_v1"))
    assert producer.publish_unit(writer, object(), market(),
                                 session_date=DAY, ticker="ABCD") == "skipped"


def test_uncertain_child_insert_never_publishes_coverage(monkeypatch):
    writer = Writer()
    monkeypatch.setattr(producer, "uuid4", lambda: UUID(DERIVED))
    monkeypatch.setattr(producer, "iter_persisted_v7_seconds",
                        lambda *_args, **_kwargs: (_ for _ in ({},)))
    monkeypatch.setattr(producer, "derive_pivot_intervals",
                        lambda *_args, **_kwargs: (INTERVAL,))
    monkeypatch.setattr(producer, "_insert_intervals", lambda *_args: None)
    with pytest.raises(producer.PivotReadbackMismatch, match="read-back"):
        producer.publish_unit(writer, object(), market(),
                              session_date=DAY, ticker="ABCD")
    assert writer.fact is None


def test_empty_product_gets_coverage_without_child_rows(monkeypatch):
    writer = Writer()
    monkeypatch.setattr(producer, "uuid4", lambda: UUID(DERIVED))
    monkeypatch.setattr(producer, "iter_persisted_v7_seconds",
                        lambda *_args, **_kwargs: (_ for _ in ()))
    monkeypatch.setattr(producer, "_insert_intervals", lambda *_args:
                        pytest.fail("No empty child insert"))
    assert producer.publish_unit(writer, object(), market(),
                                 session_date=DAY, ticker="ABCD") == "published"
    assert writer.fact["interval_count"] == 0
