import pytest

from src.trading_runtime import rule_decision_publication as module
from src.trading_runtime import arte_journal_writer as writer
from src.trading_runtime.rule_decision_basis_schema import STAGED_TABLES


def test_opt_in_catalog_preserves_original_v4_and_declares_shared_tables_once():
    original = writer.v4_storage_contracts()
    combined = module.rule_decision_storage_contracts()
    assert len(combined) == len(original) + len(STAGED_TABLES)
    assert combined[:len(original)] == original
    assert len({table.name for table in combined}) == len(combined)
    assert {table.name for table in STAGED_TABLES}.isdisjoint(table.name for table in original)


@pytest.mark.parametrize("failure", [False, True])
def test_storage_preflight_uses_shared_audit_and_propagates_failures(monkeypatch, failure):
    client = object()
    calls = []
    def audit(actual, *, tables):
        assert actual is client
        assert tables == module.rule_decision_storage_contracts()
        calls.append(True)
        if failure:
            raise RuntimeError("Synthetic incorrect part placement")
    monkeypatch.setattr(writer, "storage_preflight", audit)
    if failure:
        with pytest.raises(RuntimeError, match="part placement"):
            module.preflight_rule_decision_storage(client)
    else:
        module.preflight_rule_decision_storage(client)
    assert calls == [True]
