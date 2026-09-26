"""An interrupted candidate attempt is never certified or silently reused."""
import json
from uuid import UUID

import numpy as np
import pytest

from pipelines.strategy_one import candidate_producer as producer
from src.backend.backtest_market_data import (
    CertifiedMarketDayPlan, ExecutionInterval, MarketDayUnit,
)
from src.backend.backtest_strategy_one_candidate_contract import candidate_content_hash
from src.backend.backtest_strategy_one_preparation import PreparedStrategyOneTicker


DAY = "2026-08-18"
SOURCE = "00000000-0000-0000-0000-000000000001"
DERIVED = "00000000-0000-0000-0000-000000000002"
STRATEGY = "a" * 64


def _market():
    units = tuple(MarketDayUnit("build", DAY, "ABCD", stage, SOURCE,
                                "source", 100, "hash")
                  for stage in ("bars", "technical", "broker_100ms"))
    return CertifiedMarketDayPlan(ExecutionInterval.fixed(100), "build",
                                  "definition", (DAY,), ("ABCD",), units,
                                  (100, 1000, 5000, 10000, 30000), "token")


def _prepared():
    return PreparedStrategyOneTicker(
        "ABCD", 100, np.array([10], dtype=np.int64),
        np.array([30_000], dtype=np.int64),
        np.array([29_900], dtype=np.int64),
        np.array([[30_000] * 4], dtype=np.int64),
        np.array([30_000], dtype=np.int64),
        np.array([150_000], dtype=np.int64))


class Client:
    def __init__(self):
        self.fact = None
        self.children = ()
        self.queries = []

    def execute(self, query):
        self.queries.append(query)
        if query.startswith("SELECT") and "strategy_one_candidate_coverage_v1" in query:
            return json.dumps(self.fact) if self.fact else ""
        if query.startswith("SELECT") and "strategy_one_candidate_v1" in query:
            return "\n".join(json.dumps({key: value for key, value in row.items()
                if key in producer.VALUE_FIELDS}) for row in self.children)
        if query.startswith("INSERT INTO arte.strategy_one_candidate_coverage_v1"):
            self.fact = dict(derivation_attempt_text=DERIVED,
                             bars_attempt_text=SOURCE,
                             technical_attempt_text=SOURCE,
                             liquidity_attempt_text=SOURCE,
                             strategy_digest=STRATEGY,
                             candidate_count=len(self.children),
                             content_hash=candidate_content_hash(self.children))
            return ""
        raise AssertionError(query)


def test_producer_publishes_child_then_coverage_and_skips_exact_retry(monkeypatch):
    client = Client()
    monkeypatch.setattr(producer, "uuid4", lambda: UUID(DERIVED))
    def inserted(_, rows):
        assert client.fact is None
        client.children = rows
        client.queries.append("INSERT child")
    monkeypatch.setattr(producer, "_insert_rows", inserted)
    kwargs = dict(session_date=DAY, ticker="ABCD", strategy_digest=STRATEGY,
                  has_episode=True, prepared=_prepared())
    assert producer.publish_unit(client, _market(), **kwargs) == "published"
    assert client.queries.index("INSERT child") < next(index for index, query
        in enumerate(client.queries) if query.startswith(
            "INSERT INTO arte.strategy_one_candidate_coverage_v1"))
    assert producer.publish_unit(client, _market(), **kwargs) == "skipped"


def test_zero_candidates_publish_coverage_without_fabricated_child(monkeypatch):
    client = Client()
    monkeypatch.setattr(producer, "uuid4", lambda: UUID(DERIVED))
    monkeypatch.setattr(producer, "_insert_rows", lambda *_: pytest.fail(
        "empty candidate scope must not write child rows"))
    assert producer.publish_unit(client, _market(), session_date=DAY,
        ticker="ABCD", strategy_digest=STRATEGY,
        has_episode=False, prepared=None) == "published"
    assert client.fact["candidate_count"] == 0


def test_uncertain_child_attempt_cannot_publish_bad_coverage(monkeypatch):
    client = Client()
    monkeypatch.setattr(producer, "uuid4", lambda: UUID(DERIVED))
    monkeypatch.setattr(producer, "_insert_rows", lambda *_: None)
    with pytest.raises(RuntimeError, match="child INSERT failed"):
        producer.publish_unit(client, _market(), session_date=DAY,
            ticker="ABCD", strategy_digest=STRATEGY,
            has_episode=True, prepared=_prepared())
    assert client.fact is None
