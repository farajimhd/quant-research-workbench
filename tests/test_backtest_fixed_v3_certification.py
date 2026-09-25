from __future__ import annotations

import hashlib

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
