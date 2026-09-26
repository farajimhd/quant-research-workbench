"""Strategy 1 preflight certifies local source and never needs QMD health."""
from src.backend import historical_runtime_versions as versions


def _configuration():
    return {"strategy": {"strategy_number": 1,
                         "strategy_id": "early-squeeze-strategy", "revision": 1},
            "assignments": [{"strategy_id": "early-squeeze-strategy",
                             "strategy_revision": 1, "status": "active"}]}


def test_fixed_strategy_one_uses_source_certificate_without_qmd(monkeypatch):
    monkeypatch.setattr(versions, "backend_source_fingerprint",
                        lambda: versions.LOADED_BACKEND_FINGERPRINT)
    from src.backend import backtest_fixed_v4_certification as certification
    monkeypatch.setattr(certification, "certify_strategy_one_v4_projection",
                        lambda: "a" * 64)
    result = versions.fixed_strategy_one_runtime_version_check(_configuration())
    assert result["status"] == "ready"
    assert result["evidence"]["projection_certificate"] == "a" * 64


def test_fixed_strategy_one_rejects_foreign_assignment_and_code_drift(monkeypatch):
    monkeypatch.setattr(versions, "backend_source_fingerprint",
                        lambda: "changed")
    configuration = _configuration()
    configuration["assignments"][0]["strategy_revision"] = 2
    result = versions.fixed_strategy_one_runtime_version_check(configuration)
    assert result["status"] == "blocked"
    assert "Strategy 1" in result["summary"]
    assert "restart" in result["summary"]
