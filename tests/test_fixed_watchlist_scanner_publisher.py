from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from src.backend.fixed_watchlist_scanner_publisher import (
    load_attested_scanner_boundary, load_attested_scanner_boundaries_batch,
    publish_operator_scanner_boundary,
    scanner_sidecar_storage_preflight,
)
from src.backend.fixed_watchlist_scanner_sidecar import (
    SCORE_REVISION, SCANNER_SCHEMA, load_scanner_boundary,
)


AT = datetime(2026, 8, 18, 14, 0, 0, 100000, tzinfo=timezone.utc)


def snapshot():
    return {
        "as_of": AT.isoformat(), "schema_version": SCANNER_SCHEMA,
        "scanner_score_revision": SCORE_REVISION, "event_count": 3000,
        "source_revision": {"token": "revision-1", "source_plan_hash": "source-1",
                            "complete_for_history": True, "request_complete": True},
        "market_row_count": 1, "market_rows_sha256": "c" * 64,
        "market_rows": [{
            "ticker": "AAPL", "last_event_ts": AT.isoformat(),
            "event_age_ms": 0, "quality_state": "ready",
            "quality_flags": [], "degradation_reason": None,
            "liquidity_eligibility_reasons": [], "liquidity_eligible": True,
            "last_price": 10.0, "bid": 9.99, "ask": 10.01,
            "bid_size": 100, "ask_size": 100, "day_dollar_volume": 2_000_000.0,
            "day_volume": 200_000.0, "day_trade_count": 2000,
            "trade_rate_10s": 1.2, "trade_rate_60s": 0.8,
            "spread": 0.02, "liquidity_score": 72.5, "liquidity_rank": 1,
            "previous_close": 9.0,
        }],
    }


class FakeStorage:
    def __init__(self):
        self.policy = ["live_market_ssd"]
        self.table_policy = "live_market_ssd"
        self.bad_part = False
        self.rows = {"arte.qmd_scanner_symbol_v1": [],
                     "arte.qmd_scanner_boundary_v1": []}
        self.inserts = []
        self.fail_after_insert = False
        self.corrupt_readback = False
        self.move_part_after_symbol = False

    def iter_json_each_row(self, sql):
        if "system.storage_policies" in sql:
            return iter([{"disks": self.policy}])
        if "system.tables" in sql:
            return iter({"name": name.removeprefix("arte."),
                         "storage_policy": self.table_policy, "engine": "MergeTree"}
                        for name in self.rows)
        if "system.parts" in sql:
            return iter([{"table": "qmd_scanner_symbol_v1", "disk_name": "default"}]
                        if self.bad_part else [])
        if "arte.qmd_scanner_symbol_v1" in sql:
            rows = list(self.rows["arte.qmd_scanner_symbol_v1"])
            if self.corrupt_readback and rows:
                rows[0] = {**rows[0], "liquidity_score": "99.0"}
            return iter(rows)
        if "arte.qmd_scanner_boundary_v1" in sql:
            return iter(self.rows["arte.qmd_scanner_boundary_v1"])
        raise AssertionError(sql)

    def insert_json_each_row(self, table, rows):
        self.inserts.append((table, len(rows)))
        self.rows[table].extend(dict(row) for row in rows)
        if self.move_part_after_symbol and table == "arte.qmd_scanner_symbol_v1":
            self.bad_part = True
        if self.fail_after_insert:
            self.fail_after_insert = False
            raise TimeoutError("acknowledgment lost")


class NodeExistsError(Exception):
    pass


class FakeKeeper:
    def __init__(self):
        self.connected = True
        self.client_state = SimpleNamespace(name="CONNECTED")
        self.nodes = {}
        self.fail_after_create = False
        self.fail_before_commit = False

    def ensure_path(self, path):
        assert path == "/trading/qmd_scanner_publication/v1"

    def create(self, path, value, *, ephemeral):
        assert ephemeral is False
        if path in self.nodes:
            raise NodeExistsError()
        self.nodes[path] = (value, 0)
        if self.fail_after_create:
            self.fail_after_create = False
            raise TimeoutError("claim acknowledgment lost")

    def get(self, path):
        value, version = self.nodes[path]
        return value, SimpleNamespace(version=version)

    def set(self, path, value, *, version):
        assert self.nodes[path][1] == version
        if self.fail_before_commit:
            raise TimeoutError("commit acknowledgment absent")
        self.nodes[path] = (value, version + 1)


def test_publishes_symbol_then_boundary_and_cold_verifies_once():
    storage, keeper = FakeStorage(), FakeKeeper()
    boundary = publish_operator_scanner_boundary(
        storage, keeper, snapshot(), market_plan_token="a" * 64)
    assert storage.inserts == [("arte.qmd_scanner_symbol_v1", 1),
                               ("arte.qmd_scanner_boundary_v1", 1)]
    assert boundary["market_row_count"] == 1
    assert list(keeper.nodes.values())[0][0].startswith(b"1\ncommitted\n")
    with pytest.raises(RuntimeError, match="claim exists"):
        publish_operator_scanner_boundary(storage, keeper, snapshot(),
                                          market_plan_token="a" * 64)
    assert len(storage.inserts) == 2


@pytest.mark.parametrize("field,value", [
    ("policy", ["default"]), ("table_policy", "default"), ("bad_part", True),
])
def test_preflight_rejects_misplacement_before_claim_or_insert(field, value):
    storage, keeper = FakeStorage(), FakeKeeper()
    setattr(storage, field, value)
    with pytest.raises(RuntimeError, match="live_market_ssd"):
        publish_operator_scanner_boundary(storage, keeper, snapshot(),
                                          market_plan_token="a" * 64)
    assert not keeper.nodes and not storage.inserts


def test_ambiguous_claim_and_write_are_single_use():
    storage, keeper = FakeStorage(), FakeKeeper()
    keeper.fail_after_create = True
    with pytest.raises(RuntimeError, match="ambiguous"):
        publish_operator_scanner_boundary(storage, keeper, snapshot(),
                                          market_plan_token="a" * 64)
    assert not storage.inserts
    with pytest.raises(RuntimeError, match="claim exists"):
        publish_operator_scanner_boundary(storage, keeper, snapshot(),
                                          market_plan_token="a" * 64)

    storage, keeper = FakeStorage(), FakeKeeper()
    storage.fail_after_insert = True
    with pytest.raises(TimeoutError):
        publish_operator_scanner_boundary(storage, keeper, snapshot(),
                                          market_plan_token="a" * 64)
    assert len(storage.inserts) == 1
    with pytest.raises(RuntimeError, match="claim exists"):
        publish_operator_scanner_boundary(storage, keeper, snapshot(),
                                          market_plan_token="a" * 64)


def test_cold_readback_corruption_never_commits_claim():
    storage, keeper = FakeStorage(), FakeKeeper()
    storage.corrupt_readback = True
    with pytest.raises(RuntimeError, match="content or full-scope"):
        publish_operator_scanner_boundary(storage, keeper, snapshot(),
                                          market_plan_token="a" * 64)
    assert list(keeper.nodes.values())[0][0].startswith(b"1\nstarted\n")


def test_post_write_misplacement_never_commits_claim():
    storage, keeper = FakeStorage(), FakeKeeper()
    storage.move_part_after_symbol = True
    with pytest.raises(RuntimeError, match="outside live_market_ssd"):
        publish_operator_scanner_boundary(storage, keeper, snapshot(),
                                          market_plan_token="a" * 64)
    assert storage.inserts == [("arte.qmd_scanner_symbol_v1", 1)]
    assert list(keeper.nodes.values())[0][0].startswith(b"1\nstarted\n")


def test_preflight_is_read_only():
    storage = FakeStorage()
    scanner_sidecar_storage_preflight(storage)
    assert not storage.inserts


def test_boundary_written_before_keeper_commit_is_diagnostic_only():
    storage, keeper = FakeStorage(), FakeKeeper()
    keeper.fail_before_commit = True
    with pytest.raises(RuntimeError, match="commit claim is ambiguous"):
        publish_operator_scanner_boundary(storage, keeper, snapshot(),
                                          market_plan_token="a" * 64)
    boundary = storage.rows["arte.qmd_scanner_boundary_v1"][0]
    identity = dict(market_plan_token="a" * 64,
                    source_revision_token=boundary["source_revision_token"],
                    boundary_at=AT)
    assert load_scanner_boundary(storage, boundary["boundary_id"], **identity)[0] == boundary
    with pytest.raises(RuntimeError, match="not committed"):
        load_attested_scanner_boundary(storage, keeper, boundary["boundary_id"], **identity)


def test_attested_reader_rejects_tampered_or_missing_keeper_proof():
    storage, keeper = FakeStorage(), FakeKeeper()
    boundary = publish_operator_scanner_boundary(
        storage, keeper, snapshot(), market_plan_token="a" * 64)
    identity = dict(market_plan_token="a" * 64,
                    source_revision_token=boundary["source_revision_token"],
                    boundary_at=AT)
    assert load_attested_scanner_boundary(
        storage, keeper, boundary["boundary_id"], **identity)[0] == boundary
    path = next(iter(keeper.nodes))
    original, version = keeper.nodes[path]
    keeper.nodes[path] = (b"1\ncommitted\n" + b"0" * 64, version)
    with pytest.raises(RuntimeError, match="differs"):
        load_attested_scanner_boundary(storage, keeper, boundary["boundary_id"], **identity)
    keeper.nodes[path] = (original, version)
    del keeper.nodes[path]
    with pytest.raises(RuntimeError, match="unavailable"):
        load_attested_scanner_boundary(storage, keeper, boundary["boundary_id"], **identity)


def test_batch_reader_preserves_seal_and_keeper_proof():
    storage, keeper = FakeStorage(), FakeKeeper()
    boundary = publish_operator_scanner_boundary(
        storage, keeper, snapshot(), market_plan_token="a" * 64)
    refs = ((boundary["boundary_id"], boundary["source_revision_token"], AT),)
    result = load_attested_scanner_boundaries_batch(
        storage, keeper, refs, market_plan_token="a" * 64)
    assert result[0][0] == boundary
    assert result[0][1][0]["ticker"] == "AAPL"
    storage.corrupt_readback = True
    with pytest.raises(RuntimeError, match="content or full-scope"):
        load_attested_scanner_boundaries_batch(
            storage, keeper, refs, market_plan_token="a" * 64)
