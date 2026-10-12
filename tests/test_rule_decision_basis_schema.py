from src.trading_runtime.rule_decision_basis_schema import BASIS, INTENT, STAGED_TABLES
from src.trading_runtime.arte_journal_schema import TABLES


def test_shared_decision_contract_has_explicit_references_and_no_active_installation():
    assert {table.name for table in STAGED_TABLES}.isdisjoint(table.name for table in TABLES)
    assert {"source_commit_hash", "release_content_hash", "history_input_hash",
        "basis_sequence", "decision_sequence", "assignment_id", "listing_id"} <= dict(BASIS.columns).keys()
    assert {"decision_id", "parent_record_id", "ordinal", "stage", "strategy_intent_id"} <= dict(INTENT.columns).keys()
    for table in STAGED_TABLES:
        assert "storage_policy = 'live_market_ssd'" in table.ddl()
        assert not any("JSON" in kind or "Map(" in kind for _, kind in table.columns)
        assert not any(name in {"payload", "metadata", "broker_state", "position_state"}
                       for name, _ in table.columns)
