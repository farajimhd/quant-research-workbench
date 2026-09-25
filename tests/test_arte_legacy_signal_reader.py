"""Historical V1 signal hash recipe, without whole-run recovery authority."""
from hashlib import sha256

import pytest

from src.trading_runtime.arte_legacy_signal_reader import (
    _legacy_signal_content, load_legacy_v1_signal_batch,
)
from src.trading_runtime.arte_journal_schema import (
    LEGACY_COMMIT_V1, LEGACY_STRATEGY_SIGNAL_V1,
)
from src.trading_runtime.journal_contract import canonical_json


RUN = "00000000-0000-0000-0000-000000000a01"
BATCH = "00000000-0000-0000-0000-000000000a02"
RECORD = "00000000-0000-0000-0000-000000000a03"


def _fixture():
    source = {
        "record_id": RECORD, "run_id": RUN, "event_month": "2026-08-01",
        "batch_id": BATCH, "account_id": "DU1", "strategy_id": "s1",
        "strategy_revision": 1, "signal_id": "signal-1",
        "signal_type": "entry", "ticker": "ABCD", "action": "buy",
        "direction": "long", "score": "0.750000000000000000",
        "confidence": "0.500000000000000000", "reason": "setup",
        "working_timeframe": "1m", "invalidation_price": "10.0000000000",
        "source_signal_count": 0,
        "source_event_time": "2026-08-18 14:00:00.123456789",
    }
    digest = sha256(canonical_json(_legacy_signal_content(source)).encode()).hexdigest()
    row = {**source, "content_hash": digest}
    family_hash = sha256(canonical_json([(RECORD, digest)]).encode()).hexdigest()
    commit = {"run_id": RUN, "batch_id": BATCH,
              "signal_count": 1, "signal_hash": family_hash}
    return row, commit


class FakeClient:
    def __init__(self, row, commit):
        self.rows = [row]
        self.commits = [commit]
        self.queries = []

    def execute(self, sql):
        self.queries.append(sql)
        assert sql.startswith("SELECT ")
        if "FROM system.columns" in sql:
            rows = [{"table": contract.name, "name": name, "type": kind}
                    for contract in (LEGACY_COMMIT_V1, LEGACY_STRATEGY_SIGNAL_V1)
                    for name, kind in contract.columns]
        else:
            rows = self.commits if "FROM arte.trading_commit_v1" in sql else self.rows
        return "\n".join(canonical_json(row) for row in rows)


def test_legacy_signal_batch_uses_exact_pre_cursor_row_hash_and_commit_hash():
    row, commit = _fixture()
    assert tuple(name for name, _ in LEGACY_STRATEGY_SIGNAL_V1.columns) == (
        "record_id", "run_id", "event_month", "batch_id", "account_id",
        "strategy_id", "strategy_revision", "signal_id", "signal_type",
        "ticker", "action", "direction", "score", "confidence", "reason",
        "working_timeframe", "invalidation_price", "source_signal_count",
        "source_event_time", "content_hash")
    client = FakeClient(row, commit)
    assert load_legacy_v1_signal_batch(client, run_id=RUN, batch_id=BATCH)[0][
        "content_hash"] == row["content_hash"]
    assert all(query.startswith("SELECT ") for query in client.queries)
    signal_query = next(query for query in client.queries
                        if "FROM arte.trading_strategy_signal_v1" in query)
    assert "toString(score) AS score" in signal_query
    assert "toString(invalidation_price) AS invalidation_price" in signal_query


def test_legacy_signal_batch_accepts_proven_pre_canonical_zero_fraction_recipe():
    row, commit = _fixture()
    row["source_event_time"] = "2026-08-18 14:00:00.000000000"
    raw = _legacy_signal_content({key: value for key, value in row.items()
                                  if key != "content_hash"})
    raw["source_event_time"] = "2026-08-18T14:00:00+00:00"
    digest = sha256(canonical_json(raw).encode()).hexdigest()
    row["content_hash"] = digest
    commit["signal_hash"] = sha256(canonical_json([(RECORD, digest)]).encode()).hexdigest()
    assert load_legacy_v1_signal_batch(FakeClient(row, commit), run_id=RUN,
                                       batch_id=BATCH)[0]["content_hash"] == digest
    row["source_event_time"] = "2026-08-18 14:00:00.000000001"
    with pytest.raises(RuntimeError, match="row hash"):
        load_legacy_v1_signal_batch(FakeClient(row, commit), run_id=RUN,
                                    batch_id=BATCH)


def test_legacy_signal_batch_rejects_tamper_duplicate_and_missing_commit():
    row, commit = _fixture()
    client = FakeClient({**row, "reason": "changed"}, commit)
    with pytest.raises(RuntimeError, match="row hash"):
        load_legacy_v1_signal_batch(client, run_id=RUN, batch_id=BATCH)
    client = FakeClient(row, commit)
    client.rows.append(row)
    with pytest.raises(RuntimeError, match="repeats a record"):
        load_legacy_v1_signal_batch(client, run_id=RUN, batch_id=BATCH)
    client = FakeClient(row, commit)
    client.commits.clear()
    with pytest.raises(RuntimeError, match="lacks one commit"):
        load_legacy_v1_signal_batch(client, run_id=RUN, batch_id=BATCH)


def test_legacy_reader_rejects_newer_columns_without_defaulting_them():
    row, commit = _fixture()
    client = FakeClient({**row, "evidence_node_count": 0}, commit)
    with pytest.raises(ValueError, match="extra columns"):
        load_legacy_v1_signal_batch(client, run_id=RUN, batch_id=BATCH)


def test_legacy_reader_rejects_widened_deployed_signal_schema():
    row, commit = _fixture()
    client = FakeClient(row, commit)
    original = client.execute
    def widened(sql):
        if "FROM system.columns" in sql:
            rows = [dict(table=contract.name, name=name, type=kind)
                    for contract in (LEGACY_COMMIT_V1, LEGACY_STRATEGY_SIGNAL_V1)
                    for name, kind in contract.columns]
            rows.append(dict(table="trading_strategy_signal_v1",
                             name="decision_status", type="Nullable(String)"))
            return "\n".join(canonical_json(value) for value in rows)
        return original(sql)
    client.execute = widened
    with pytest.raises(RuntimeError, match="deployed schema differs"):
        load_legacy_v1_signal_batch(client, run_id=RUN, batch_id=BATCH)
