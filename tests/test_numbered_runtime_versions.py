"""A sealed second release cannot execute under a different source approval."""
from src.backend import historical_runtime_versions as versions
from src.backend import backtest_fixed_v4_certification as certification
from pipelines.strategy_one.strategy_two_configuration import compile_strategy_two_configuration
from src.backend.backtest_strategy_one_configuration import certify_strategy_one_configuration
from tests.test_strategy_two_configuration import Reader


def configuration(fingerprint):
    return compile_strategy_two_configuration(
        certify_strategy_one_configuration(Reader()), approved_code_commit="a" * 40,
        approved_code_fingerprint=fingerprint,
        approval_reference="test-numbered-source-seal")["payload"]


def test_second_release_requires_its_own_projection_and_approved_source(monkeypatch):
    current = versions.LOADED_BACKEND_FINGERPRINT
    monkeypatch.setattr(versions, "backend_source_fingerprint", lambda: current)
    calls = []
    monkeypatch.setattr(certification, "certify_numbered_fixed_v4_projection",
                        lambda number: calls.append(number) or "2" * 64)
    result = versions.fixed_strategy_one_runtime_version_check(configuration(current))
    assert result["status"] == "ready"
    assert result["evidence"]["projection_certificate"] == "2" * 64
    assert calls == [2]
    blocked = versions.fixed_strategy_one_runtime_version_check(configuration("0" * 64))
    assert blocked["status"] == "blocked"
    assert "approved code fingerprint" in blocked["summary"]
    assert calls == [2]


def test_second_release_rejects_cross_number_assignment(monkeypatch):
    current = versions.LOADED_BACKEND_FINGERPRINT
    monkeypatch.setattr(versions, "backend_source_fingerprint", lambda: current)
    selected = configuration(current)
    selected["assignments"] = [{"strategy_id": "early-squeeze-strategy",
                                 "strategy_revision": 1, "status": "active"}]
    result = versions.fixed_strategy_one_runtime_version_check(selected)
    assert result["status"] == "blocked"
    assert "assignments differ" in result["summary"]
