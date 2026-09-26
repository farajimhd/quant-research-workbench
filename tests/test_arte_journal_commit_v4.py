"""Strategy 1 commit family authority stays tabular and exact."""
from datetime import date, datetime, timezone

import pytest

from src.trading_runtime.arte_journal_commit_v4 import prepare_commit_v4
from src.trading_runtime.arte_journal_schema import TABLES
from src.trading_runtime.arte_journal_writer import _sealed_families
from tests.test_arte_journal_writer import batch


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
