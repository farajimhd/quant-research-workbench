"""Historical reads retain source proof without granting a stale execution."""
from copy import deepcopy
from dataclasses import replace
from hashlib import sha256

import pytest

from src.backend import backtest_saved_source_authority as saved
from src.backend import historical_runtime_versions as versions
from src.trading_runtime.journal_contract import canonical_json

RUN = "00000000-0000-0000-0000-000000000001"


@pytest.mark.parametrize("commit,expected", [
    ("bee0dba8cec0527d6586eab99cf4542ebb0dd0d4", "a39dbe7e982d3331b61e80fe15fc5a20a46f9735ebd6c55b1d1ad0774d231df2"),
    ("43218fee91e6ea3fe4c7a5d83469ffd771bde9b1", "2145b969f7edeeef8e56f882456092f1502def052fec37419aec31a91d730eb4"),
])
def test_actual_approved_git_objects_reproduce_published_fingerprints(commit, expected):
    assert saved.approved_git_source_fingerprint(str(versions.ROOT), commit) == expected


@pytest.mark.parametrize("commit", ["HEAD", "a" * 39, "a" * 40 + " --output=x", "A" * 40])
def test_source_commit_must_be_exact_object_identity(commit):
    with pytest.raises(ValueError, match="exact approved Git commit"):
        saved.approved_git_source_fingerprint(str(versions.ROOT), commit)


def test_unavailable_source_fails_closed():
    with pytest.raises(RuntimeError, match="unavailable"):
        saved.approved_git_source_fingerprint(str(versions.ROOT), "0" * 40)


def certified_fixture(monkeypatch):
    from test_strategy_twenty_seven_configuration import parent
    payload = parent().payload
    digest = sha256(canonical_json(payload).encode()).hexdigest()
    revision = {"revision_id": "test-revision", "content_hash": digest, "payload": payload}
    context = {"run_id": RUN, "mode": "backtest", "evaluation_interval_ms": 100,
               "strategy_id": payload["strategy"]["strategy_id"],
               "strategy_revision": 26, "configuration_hash": digest}
    current = "a" * 64
    monkeypatch.setattr(versions, "LOADED_BACKEND_FINGERPRINT", current)
    monkeypatch.setattr(versions, "backend_source_fingerprint", lambda: current)
    monkeypatch.setattr(versions, "_loaded_numbered_projection", lambda *_: "b" * 64)
    monkeypatch.setattr(saved, "approved_git_source_fingerprint", lambda *_: "e" * 64)
    return context, revision


def test_historical_source_proof_does_not_approve_current_execution(monkeypatch):
    context, revision = certified_fixture(monkeypatch)
    proof = saved.certify_saved_review_source(context, revision)
    assert proof.approved_fingerprint != proof.reader_fingerprint
    check = saved.saved_review_runtime_version_check(proof, revision)
    assert check["status"] == "ready"
    assert check["evidence"]["scope"] == "saved_run_read_only_no_execution_or_resume"
    execution = versions.fixed_strategy_one_runtime_version_check(revision["payload"])
    assert execution["status"] == "blocked"
    assert "approved code fingerprint differs" in execution["summary"]


def test_bad_approved_source_is_not_replaceable_by_current_reader(monkeypatch):
    context, revision = certified_fixture(monkeypatch)
    monkeypatch.setattr(saved, "approved_git_source_fingerprint", lambda *_: "f" * 64)
    with pytest.raises(ValueError, match="published fingerprint"):
        saved.certify_saved_review_source(context, revision)


@pytest.mark.parametrize("change", ["run", "configuration", "reader", "certificate", "seal"])
def test_source_proof_rejects_changed_binding(monkeypatch, change):
    context, revision = certified_fixture(monkeypatch)
    proof = saved.certify_saved_review_source(context, revision)
    run_id = RUN
    if change == "run":
        run_id = "00000000-0000-0000-0000-000000000002"
    elif change == "configuration":
        revision = deepcopy(revision)
        revision["payload"]["assignments"] = [{"status": "disabled"}]
    elif change == "reader":
        monkeypatch.setattr(versions, "backend_source_fingerprint", lambda: "c" * 64)
    elif change == "certificate":
        proof = replace(proof, reader_certificate="c" * 64)
    else:
        proof = replace(proof, _seal=object())
    with pytest.raises((ValueError, RuntimeError)):
        saved.validate_saved_review_source(proof, revision, run_id=run_id)


def test_uncertified_object_cannot_enter_saved_preflight():
    from src.backend.replay_run_service import backtest_preflight
    from datetime import date
    with pytest.raises(ValueError, match="internally certified"):
        backtest_preflight(anchor_date=date(2026, 8, 19), session_count=1,
                           configuration_revision={"payload": {}},
                           _saved_review_authority={"scope": "read_only"})
