"""Sealed numbered releases cannot execute under a different source approval."""
import pytest
from src.backend import historical_runtime_versions as versions
from src.backend import backtest_fixed_v4_certification as certification
from pipelines.strategy_one.strategy_two_configuration import compile_strategy_two_configuration
from src.backend.backtest_strategy_one_configuration import certify_strategy_one_configuration, certify_numbered_configuration
from tests.test_strategy_two_configuration import Reader


def configuration(fingerprint, number=2):
    reader = Reader()
    envelope = compile_strategy_two_configuration(
        certify_strategy_one_configuration(reader), approved_code_commit="a" * 40,
        approved_code_fingerprint=fingerprint,
        approval_reference="test-numbered-source-seal")
    if number >= 3:
        from pipelines.strategy_one.strategy_three_configuration import compile_strategy_three_configuration
        reader.payloads[2] = envelope["payload"]
        reader.sources[2] = (envelope["source_candidate_id"], envelope["source_candidate_hash"])
        envelope = compile_strategy_three_configuration(
            certify_numbered_configuration(reader, 2), approved_code_commit="b" * 40,
            approved_code_fingerprint=fingerprint, approval_reference="test-third-source-seal")
    if number >= 4:
        from pipelines.strategy_one.strategy_four_configuration import compile_strategy_four_configuration
        reader.payloads[3] = envelope["payload"]
        reader.sources[3] = (envelope["source_candidate_id"], envelope["source_candidate_hash"])
        envelope = compile_strategy_four_configuration(
            certify_numbered_configuration(reader, 3), approved_code_commit="c" * 40,
            approved_code_fingerprint=fingerprint, approval_reference="test-fourth-source-seal")
    if number >= 5:
        from pipelines.strategy_one.strategy_five_configuration import compile_strategy_five_configuration
        reader.payloads[4] = envelope["payload"]
        reader.sources[4] = (envelope["source_candidate_id"], envelope["source_candidate_hash"])
        envelope = compile_strategy_five_configuration(
            certify_numbered_configuration(reader, 4), approved_code_commit="d" * 40,
            approved_code_fingerprint=fingerprint, approval_reference="test-fifth-source-seal")
    if number >= 6:
        from pipelines.strategy_one.strategy_six_configuration import compile_strategy_six_configuration
        reader.payloads[5] = envelope["payload"]
        reader.sources[5] = (envelope["source_candidate_id"], envelope["source_candidate_hash"])
        envelope = compile_strategy_six_configuration(
            certify_numbered_configuration(reader, 5), approved_code_commit="e" * 40,
            approved_code_fingerprint=fingerprint, approval_reference="test-sixth-source-seal")
    if number == 7:
        from pipelines.strategy_one.strategy_seven_configuration import compile_strategy_seven_configuration
        reader.payloads[6] = envelope["payload"]
        reader.sources[6] = (envelope["source_candidate_id"], envelope["source_candidate_hash"])
        envelope = compile_strategy_seven_configuration(
            certify_numbered_configuration(reader, 6), approved_code_commit="f" * 40,
            approved_code_fingerprint=fingerprint, approval_reference="test-seventh-source-seal")
    return envelope["payload"]


@pytest.mark.parametrize("number", [2, 3, 4, 5, 6, 7])
def test_numbered_release_requires_its_own_projection_and_approved_source(monkeypatch, number):
    current = versions.LOADED_BACKEND_FINGERPRINT
    monkeypatch.setattr(versions, "backend_source_fingerprint", lambda: current)
    calls = []
    monkeypatch.setattr(certification, "certify_numbered_fixed_v4_projection",
                        lambda number: calls.append(number) or "2" * 64)
    result = versions.fixed_strategy_one_runtime_version_check(configuration(current, number))
    assert result["status"] == "ready"
    assert result["evidence"]["projection_certificate"] == "2" * 64
    assert calls == [number]
    blocked = versions.fixed_strategy_one_runtime_version_check(configuration("0" * 64, number))
    assert blocked["status"] == "blocked"
    assert "approved code fingerprint" in blocked["summary"]
    assert calls == [number]


@pytest.mark.parametrize("number", [2, 3, 4, 5, 6, 7])
def test_numbered_release_rejects_cross_number_assignment(monkeypatch, number):
    current = versions.LOADED_BACKEND_FINGERPRINT
    monkeypatch.setattr(versions, "backend_source_fingerprint", lambda: current)
    selected = configuration(current, number)
    selected["assignments"] = [{"strategy_id": "early-squeeze-strategy",
                                 "strategy_revision": 1, "status": "active"}]
    result = versions.fixed_strategy_one_runtime_version_check(selected)
    assert result["status"] == "blocked"
    assert "assignments differ" in result["summary"]
