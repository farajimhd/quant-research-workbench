from __future__ import annotations

import hashlib
import ast
from pathlib import Path

import pytest

from src.backend import backtest_fixed_v3_certification as cert


def test_real_controller_direct_emitters_fail_closed_on_unprojected_families():
    with pytest.raises(ValueError, match="configuration.*watchlist_membership"):
        cert.certify_direct_v3_projection()


def test_fixed_v7_warning_is_excluded_only_with_proven_early_return():
    source = cert._CONTROLLER.read_text(encoding="utf-8")
    assert ("warning", "level_book_coverage") in cert.direct_controller_families(source)
    assert cert._fixed_v7_warning_unreachable(source)
    changed = source.replace(
        "if ExecutionInterval.parse(self.definition.execution_interval).kind == \"fixed\":",
        "if ExecutionInterval.parse(self.definition.execution_interval).kind == \"other\":",
        1,
    )
    assert not cert._fixed_v7_warning_unreachable(changed)


def test_direct_inventory_is_deterministic_and_rejects_dynamic_family(tmp_path):
    source = '''
def emit(self):
    self._journal.append(category="checkpoint", entity_type="market_boundary")
    self._journal.append_once_many({"category": "market_discovery_signal",
        "entity_type": "signal_occurrence"} for x in xs)
'''
    path = tmp_path / "controller.py"
    path.write_text(source, encoding="utf-8")
    first = cert.certify_direct_v3_projection(source_path=path)
    assert first == cert.certify_direct_v3_projection(source_path=path)
    path.write_text(source.replace('category="checkpoint"', 'category=kind'), encoding="utf-8")
    with pytest.raises(ValueError, match="Dynamic direct journal emitter"):
        cert.certify_direct_v3_projection(source_path=path)


def test_direct_inventory_rejects_unsupported_and_source_drift(tmp_path):
    path = tmp_path / "controller.py"
    path.write_text('self._journal.append(category="checkpoint", entity_type="market_boundary")',
                    encoding="utf-8")
    before = cert.certify_direct_v3_projection(source_path=path)
    path.write_text(path.read_text(encoding="utf-8") + "\n# changed\n", encoding="utf-8")
    assert cert.certify_direct_v3_projection(source_path=path) != before
    path.write_text('self._journal.append(category="unknown", entity_type="x")',
                    encoding="utf-8")
    with pytest.raises(ValueError, match="unknown"):
        cert.certify_direct_v3_projection(source_path=path)


def test_squeeze_query_certificate_binds_exact_query_stream_and_boundary(monkeypatch):
    seen = []
    monkeypatch.setattr(cert, "validate_stream", lambda stream, activation:
                        seen.append((stream, activation)))
    monkeypatch.setattr(cert, "first_squeeze_sql", lambda plan, *, through_boundary_ms:
                        f"SELECT {plan} {through_boundary_ms}")
    digest = hashlib.sha256(b"SELECT pinned 100").hexdigest()
    assert cert.certify_pinned_squeeze_query(
        "pinned", stream={"id": 1}, activation={"rules": 2},
        through_boundary_ms=100, expected_query_sha256=digest) == digest
    assert seen == [({"id": 1}, {"rules": 2})]
    with pytest.raises(ValueError, match="query hash changed"):
        cert.certify_pinned_squeeze_query(
            "pinned", stream={}, activation={}, through_boundary_ms=200,
            expected_query_sha256=digest)


def test_real_indirect_inventory_resolves_forwarders_and_exposes_unsupported_families():
    families, dynamic = cert.indirect_journal_inventory()
    assert ("portfolio_management", "portfolio_reservation") in families
    assert ("order_management", "order_group_state") in families
    assert ("snapshot", "portfolio") in families
    assert dynamic == ()
    assert ("execution", "commission") in families
    assert ("strategy_decision", "intent_deferral") in families
    assert ("strategy_decision", "intent_rejection") in families
    with pytest.raises(ValueError, match="lack typed projection") as failure:
        cert.certify_indirect_v3_projection()
    assert "portfolio_decision" not in str(failure.value)
    assert "portfolio_reconciliation" not in str(failure.value)
    assert "portfolio_control" not in str(failure.value)


def test_forwarded_record_requires_literal_callers_and_exact_wrapper(tmp_path):
    path = tmp_path / "portfolio.py"
    source = '''
def _record(self, entity_type):
    self.journal.append(category="portfolio_management", entity_type=entity_type)
def emit(self):
    self._record("portfolio_decision")
'''
    path.write_text(source, encoding="utf-8")
    families, dynamic = cert.indirect_journal_inventory((path,))
    assert ("portfolio_management", "portfolio_decision") in families
    assert dynamic == ()
    path.write_text(source.replace('self._record("portfolio_decision")',
                                   'self._record(kind)'), encoding="utf-8")
    assert cert.indirect_journal_inventory((path,))[1]
    path.write_text(source.replace('category="portfolio_management"',
                                   'category="other"'), encoding="utf-8")
    assert cert.indirect_journal_inventory((path,))[1]


def test_local_append_many_rejects_aliases_and_unknown_mutations(tmp_path):
    path = tmp_path / "runtime.py"
    source = '''
def emit(self):
    entries = [{"category": "execution", "entity_type": "fill"}]
    entries.append({"category": "execution", "entity_type": "commission"})
    self.journal.append_many(entries)
'''
    path.write_text(source, encoding="utf-8")
    families, dynamic = cert.indirect_journal_inventory((path,))
    assert dynamic == ()
    assert {("execution", "fill"), ("execution", "commission")} <= set(families)
    path.write_text(source.replace("    self.journal.append_many(entries)",
                                   "    alias = entries\n    alias.append(other)\n"
                                   "    self.journal.append_many(entries)"), encoding="utf-8")
    assert cert.indirect_journal_inventory((path,))[1]


def test_indirect_certificate_rejects_unsupported_and_source_drift(tmp_path):
    path = tmp_path / "runtime.py"
    path.write_text('self.journal.append(category="lifecycle", entity_type="run")',
                    encoding="utf-8")
    before = cert.certify_indirect_v3_projection((path,))
    path.write_text(path.read_text(encoding="utf-8") + "\n# changed\n", encoding="utf-8")
    assert cert.certify_indirect_v3_projection((path,)) != before
    path.write_text('self.journal.append(category="unknown", entity_type="x")',
                    encoding="utf-8")
    with pytest.raises(ValueError, match="lack typed projection"):
        cert.certify_indirect_v3_projection((path,))


def test_fixed_controller_exposes_trade_proposal_to_injected_runtime():
    source = cert._CONTROLLER.read_text(encoding="utf-8")
    tree = ast.parse(source)
    methods = [node for node in ast.walk(tree) if isinstance(node, ast.AsyncFunctionDef)
               and node.name == "submit_trade_proposal"]
    assert len(methods) == 1
    assert any(isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
               and node.func.attr == "submit_external_intent"
               and isinstance(node.func.value, ast.Attribute)
               and node.func.value.attr == "_runtime"
               for node in ast.walk(methods[0]))
    assert ("trade_proposal", "trade_proposal_confirmed") in cert.indirect_journal_inventory()[0]


def test_fixed_simulated_broker_cannot_start_live_stream_callbacks():
    broker_source = (Path(__file__).parents[1] / "src/trading_runtime/simulated_broker.py")
    tree = ast.parse(broker_source.read_text(encoding="utf-8"))
    broker = next(node for node in tree.body if isinstance(node, ast.ClassDef)
                  and node.name == "SimulatedBrokerAdapter")
    assert not any(isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
                   and node.name == "stream_broker_messages" for node in broker.body)
    runtime_source = (cert._RUNTIME_ROOT / "runtime.py").read_text(encoding="utf-8")
    assert 'if hasattr(self.broker, "stream_broker_messages"):' in runtime_source
