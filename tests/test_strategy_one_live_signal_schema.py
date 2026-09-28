"""Strategy 1 live signal authority is physically distinct and normalized."""
import pytest

from src.backend.live_signal_journal_preflight import LIVE_SIGNAL_TABLES
from src.backend.strategy_one_live_signal_schema import (
    LEGACY_TO_STRATEGY_ONE_SIGNAL, STRATEGY_ONE_SIGNAL_TABLES,
    strategy_one_signal_table,
)


def test_strategy_one_signal_tables_preserve_typed_layout_without_shared_names():
    assert len(LIVE_SIGNAL_TABLES) == len(STRATEGY_ONE_SIGNAL_TABLES) == 13
    assert len(set(LEGACY_TO_STRATEGY_ONE_SIGNAL.values())) == 13
    for shared, isolated in zip(LIVE_SIGNAL_TABLES, STRATEGY_ONE_SIGNAL_TABLES):
        assert isolated.name == strategy_one_signal_table(shared.name)
        assert isolated.name != shared.name
        assert isolated.columns == shared.columns
        assert isolated.order == shared.order
        ddl = isolated.ddl()
        assert "PARTITION BY toYYYYMM(session_key)" in ddl
        assert "storage_policy = 'live_market_ssd'" in ddl
        assert "JSON" not in ddl and "Blob" not in ddl


def test_strategy_one_signal_table_rejects_unknown_family():
    with pytest.raises(ValueError, match="Unknown normalized"):
        strategy_one_signal_table("bars_v1")
