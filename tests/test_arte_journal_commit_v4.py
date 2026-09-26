"""Strategy 1 commit family authority stays tabular and exact."""
from datetime import date, datetime, timezone

import pytest

from src.trading_runtime.arte_journal_commit_v4 import (
    load_verified_commit_v4, prepare_commit_v4, verify_commit_v4,
)
from src.trading_runtime.arte_journal_schema import TABLES
from src.trading_runtime.arte_journal_writer import _sealed_families
from tests.test_arte_journal_writer import MemoryClient, batch


def source():
    item = batch()
    return dict(
        run_id=item.run_id, run_month=item.run_month,
        attempt_id=item.attempt_id, batch_id=item.batch_id,
        prior_batch_id=item.prior_batch_id,
        first_sequence=item.first_sequence, last_sequence=item.last_sequence,
        source_cursor=item.source_cursor, status=item.status,
        sealed_families=_sealed_families(item),
        committed_at=datetime(2026, 8, 18, 8, 5, tzinfo=timezone.utc),
    )


def test_v4_commit_has_two_narrow_normalized_ssd_tables():
    contracts = {table.name: table for table in TABLES}
    for name in ("trading_commit_v4", "trading_commit_family_v4"):
        ddl = contracts[name].ddl()
        assert "storage_policy = 'live_market_ssd'" in ddl
        assert "PARTITION BY toYYYYMM(run_month)" in ddl
        assert not any(word in ddl for word in ("JSON", "Array(", "Map(", "Blob"))
    commit, families = prepare_commit_v4(**source())
    assert set(commit) == {name for name, _ in contracts["trading_commit_v4"].columns}
    assert set(families[0]) == {
        name for name, _ in contracts["trading_commit_family_v4"].columns}
    assert commit["event_count"] == 1
    assert commit["family_count"] == 1
    assert families[0]["family_name"] == "trading_event_v1"
    assert prepare_commit_v4(**source()) == (commit, families)


def test_v4_commit_rejects_missing_duplicate_or_foreign_detail_family():
    options = source()
    options["sealed_families"] = ()
    with pytest.raises(ValueError, match="event family"):
        prepare_commit_v4(**options)
    options = source()
    options["sealed_families"] = options["sealed_families"] * 2
    with pytest.raises(ValueError, match="duplicate"):
        prepare_commit_v4(**options)
    options = source()
    name, rows = options["sealed_families"][0]
    options["sealed_families"] = ((name, ({**rows[0], "run_id": "other"},)),)
    with pytest.raises(ValueError, match="batch authority"):
        prepare_commit_v4(**options)
    options = source()
    options["last_sequence"] = 2
    with pytest.raises(ValueError, match="sequence span"):
        prepare_commit_v4(**options)
    options = source()
    name, rows = options["sealed_families"][0]
    options["last_sequence"] = 2
    options["sealed_families"] = ((name, (
        rows[0], {**rows[0], "content_hash": "0" * 64})),)
    with pytest.raises(ValueError, match="repeated a typed row identity"):
        prepare_commit_v4(**options)


def test_v4_readback_requires_exact_family_set_and_detail_hashes():
    options = source()
    commit, families = prepare_commit_v4(**options)
    event = options["sealed_families"][0][1][0]
    details = {"trading_event_v1": [(event["record_id"], event["content_hash"])]}
    verify_commit_v4(commit, families, details)
    with pytest.raises(ValueError, match="detail identities"):
        verify_commit_v4(commit, families,
                         {"trading_event_v1": [(event["record_id"], "0" * 64)]})
    with pytest.raises(ValueError, match="scalar content"):
        verify_commit_v4({**commit, "family_set_hash": "0" * 64}, families, details)
    with pytest.raises(ValueError, match="scalar content"):
        verify_commit_v4({**commit, "source_cursor": "changed"}, families, details)
    with pytest.raises(ValueError, match="normalized families"):
        verify_commit_v4(commit, families, {})
    with pytest.raises(ValueError, match="count or sequence"):
        verify_commit_v4({**commit, "event_count": 2}, families, details)


def test_v4_cold_readback_recomputes_each_typed_row_hash():
    options = source()
    commit, families = prepare_commit_v4(**options)
    event = dict(options["sealed_families"][0][1][0])
    for column, precision in (("event_time", 9), ("recorded_at", 6)):
        parsed = datetime.fromisoformat(event[column]).astimezone(timezone.utc)
        event[column] = (parsed.strftime("%Y-%m-%d %H:%M:%S.%f")
                         + ("000" if precision == 9 else ""))
    client = MemoryClient()
    client.tables = {
        "trading_commit_v4": [dict(commit)],
        "trading_commit_family_v4": [dict(families[0])],
        "trading_event_v1": [event],
    }
    assert load_verified_commit_v4(
        client, run_id=commit["run_id"], batch_id=commit["batch_id"]
    ) == (commit, families)
    assert all("FORMAT JSONEachRow" in query for query in client.selects)
    client.tables["trading_event_v1"][0]["entity_id"] = "tampered"
    with pytest.raises(RuntimeError, match="row hash"):
        load_verified_commit_v4(
            client, run_id=commit["run_id"], batch_id=commit["batch_id"])
    client.tables["trading_event_v1"][0]["entity_id"] = (
        options["sealed_families"][0][1][0]["entity_id"])
    client.tables["trading_commit_v4"][0]["source_cursor"] = "changed"
    with pytest.raises(RuntimeError, match="family seal"):
        load_verified_commit_v4(
            client, run_id=commit["run_id"], batch_id=commit["batch_id"])
