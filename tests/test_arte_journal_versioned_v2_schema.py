"""Occupied V1 families must never be widened in place for Backtest cutover."""
from src.trading_runtime.arte_journal_schema import (
    TABLES, VERSIONED_JOURNAL_V2_TABLES, versioned_journal_v2_ddl,
)


def test_staged_v2_signal_and_commit_copy_complete_typed_contracts():
    active = {table.name: table for table in TABLES}
    staged = {table.name: table for table in VERSIONED_JOURNAL_V2_TABLES}
    assert set(staged) == {"trading_strategy_signal_v2", "trading_commit_v2"}
    for stem in ("trading_strategy_signal", "trading_commit"):
        old, new = active[f"{stem}_v1"], staged[f"{stem}_v2"]
        assert new.columns == old.columns
        assert (new.partition, new.order) == (old.partition, old.order)
        assert new.name not in active
        assert "storage_policy = 'live_market_ssd'" in new.ddl()
    assert len(versioned_journal_v2_ddl()) == 2
    assert all("CREATE TABLE IF NOT EXISTS arte." in statement
               and "ALTER TABLE" not in statement
               for statement in versioned_journal_v2_ddl())
    assert not any("json" in name.lower() or "blob" in name.lower()
                   for table in staged.values() for name, _ in table.columns)
