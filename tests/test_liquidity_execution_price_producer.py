"""Coverage-last producer behavior; no test writes a real market table."""
from datetime import date
import json
import re

import pytest

from pipelines.market_sip.events import liquidity_execution_price_producer as producer
from src.trading_runtime.eligible_price_contract import (
    legacy_summary_digest, matches_summary_digest, summary_digest,
)


DAY = date(2026, 8, 18)
SOURCE = "00000000-0000-0000-0000-000000000001"
DERIVED = "00000000-0000-0000-0000-000000000002"


class Client:
    def __init__(self, *, mismatch=False, already=False, legacy=False):
        self.queries = []
        self.mismatch = mismatch
        self.attempt = DERIVED if already else ""
        self.digest = ""
        self.legacy = legacy

    def execute(self, query):
        self.queries.append(query)
        if "FROM arte.liquidity_execution_price_coverage_v1" in query:
            if not self.attempt:
                return ""
            summary = dict(row_count=2, unique_keys=2,
                           eligible_bucket_count=1,
                           total_execution_volume=40.0, row_hash="123")
            published_volume = 40.00000001 if self.legacy else 40.0
            return json.dumps(dict(attempt_id=self.attempt,
                                   price_row_count=2, eligible_bucket_count=1,
                                   total_execution_volume=published_volume,
                                   content_hash=self.digest or (
                                       legacy_summary_digest(summary,
                                           published_volume=published_volume)
                                       if self.legacy else producer._digest(summary))))
        if "FULL OUTER JOIN prices" in query:
            return json.dumps(dict(bucket_index=1)) if self.mismatch else ""
        if "sum(cityHash64(tuple(*)))" in query:
            return json.dumps(dict(row_count=2, unique_keys=2,
                                   eligible_bucket_count=1,
                                   total_execution_volume=40.0, row_hash="123"))
        if query.startswith("INSERT INTO arte.liquidity_execution_price_coverage_v1"):
            self.attempt = re.search(r"toUUID\('([0-9a-f-]+)'\),\s*toUInt64", query).group(1)
            self.digest = re.search(r"'([0-9a-f]{64})',now64", query).group(1)
        return ""


def test_new_attempt_is_published_only_after_price_and_bucket_parity(monkeypatch):
    monkeypatch.setattr(producer, "uuid4", lambda: DERIVED)
    client = Client()
    status = producer.publish_unit(client, build_id="build", day=DAY,
        ticker="ABCD", source_attempt_id=SOURCE, rules=[])
    assert status == "published"
    queries = "\n".join(client.queries)
    assert "merge('market_sip_compact'" in queries
    assert queries.index("FULL OUTER JOIN prices") < queries.index(
        "INSERT INTO arte.liquidity_execution_price_coverage_v1")
    assert client.attempt == DERIVED


def test_parity_failure_never_publishes_coverage(monkeypatch):
    monkeypatch.setattr(producer, "uuid4", lambda: DERIVED)
    client = Client(mismatch=True)
    with pytest.raises(RuntimeError, match="bucket parity"):
        producer.publish_unit(client, build_id="build", day=DAY,
            ticker="ABCD", source_attempt_id=SOURCE, rules=[])
    assert not any(query.startswith(
        "INSERT INTO arte.liquidity_execution_price_coverage_v1")
        for query in client.queries)


def test_existing_coverage_is_verified_and_skipped_without_insert():
    client = Client(already=True)
    assert producer.publish_unit(client, build_id="build", day=DAY,
        ticker="ABCD", source_attempt_id=SOURCE, rules=[]) == "skipped"
    assert all(not query.startswith("INSERT") for query in client.queries)


def test_existing_coverage_fails_closed_on_corrupt_child():
    client = Client(already=True, mismatch=True)
    with pytest.raises(RuntimeError, match="differs from its coverage"):
        producer.publish_unit(client, build_id="build", day=DAY,
            ticker="ABCD", source_attempt_id=SOURCE, rules=[])


def test_legacy_float_sum_drift_is_verified_without_rewriting_coverage():
    client = Client(already=True, legacy=True)
    assert producer.publish_unit(client, build_id="build", day=DAY,
        ticker="ABCD", source_attempt_id=SOURCE, rules=[]) == "skipped"
    assert all(not query.startswith("INSERT") for query in client.queries)


def test_v2_digest_is_invariant_to_float_sum_order_but_binds_persisted_rows():
    summary = dict(row_count=2, unique_keys=2,
                   eligible_bucket_count=1, total_execution_volume=40.0,
                   row_hash="123")
    stable = summary_digest(summary)
    assert stable == summary_digest({**summary,
                                    "total_execution_volume": 40.00000001})
    assert not matches_summary_digest({**summary, "row_hash": "124"},
                                      content_hash=stable, published_volume=40.0)
