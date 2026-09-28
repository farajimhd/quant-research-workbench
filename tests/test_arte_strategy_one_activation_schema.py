from src.trading_runtime.arte_journal_schema import ACTIVATION_TABLES, TABLES
from src.trading_runtime.arte_strategy_one_activation_schema import (
    LEGACY_TO_STRATEGY_ONE, STRATEGY_ONE_ACTIVATION_TABLES,
    strategy_one_activation_table,
)


def test_strategy_one_activation_schema_is_normalized_and_legacy_isolated():
    legacy = {table.name: table for table in ACTIVATION_TABLES}
    dedicated = {table.name: table for table in STRATEGY_ONE_ACTIVATION_TABLES}
    assert len(legacy) == len(dedicated) == 4
    assert not set(dedicated) & {table.name for table in TABLES}
    for logical, physical in LEGACY_TO_STRATEGY_ONE.items():
        assert physical == strategy_one_activation_table(logical)
        assert dedicated[physical].columns == legacy[logical].columns
        assert dedicated[physical].partition == "toYYYYMM(session_date)"
        assert dedicated[physical].order == legacy[logical].order
        ddl = dedicated[physical].ddl()
        assert "live_market_ssd" in ddl
        assert "payload_json" not in ddl and "Blob" not in ddl
