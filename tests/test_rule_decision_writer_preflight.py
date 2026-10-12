import pytest

from src.trading_runtime import rule_decision_writer_preflight as module
from src.trading_runtime import arte_journal_writer as writer
from src.trading_runtime import rule_decision_publication as publication
from src.trading_runtime.rule_decision_basis_schema import STAGED_TABLES


def test_program_write_catalog_is_exact_extension_without_legacy_mutation():
    original = writer.v4_journal_write_tables()
    assert module.rule_decision_write_tables() == original | {table.name for table in STAGED_TABLES}
    assert writer.v4_journal_write_tables() == original


@pytest.mark.parametrize("failure", [None, "storage", "grants"])
def test_preflight_orders_readonly_storage_and_exact_grant_audits(monkeypatch, failure):
    client = object()
    calls = []
    def storage(actual):
        assert actual is client
        calls.append("storage")
        if failure == "storage":
            raise RuntimeError("storage")
    def grants(actual, *, journal_tables, read_only_tables):
        assert actual is client
        assert journal_tables == module.rule_decision_write_tables()
        assert read_only_tables == {table.name for table in writer.fixed_backtest_v2_contracts()} - journal_tables
        calls.append("grants")
        if failure == "grants":
            raise RuntimeError("grants")
    monkeypatch.setattr(publication, "preflight_rule_decision_storage", storage)
    monkeypatch.setattr(writer, "journal_permission_preflight", grants)
    if failure:
        with pytest.raises(RuntimeError, match=failure):
            module.preflight_rule_decision_writer(client)
    else:
        module.preflight_rule_decision_writer(client)
    assert calls == (["storage"] if failure == "storage" else ["storage", "grants"])


@pytest.mark.parametrize("attribute", ["live_v4_lease", "automatic_ladder_profile", "entry_spread_risk_profile"])
def test_other_writer_profiles_rejected_before_storage_access(attribute):
    with pytest.raises(ValueError, match="independent Backtest profile"):
        module.preflight_rule_decision_writer(type("Client", (), {attribute: True})())
