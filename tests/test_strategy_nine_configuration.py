"""Strategy 9 seals one exit rule and rejects any different parent authority."""
from copy import deepcopy
from dataclasses import replace
from hashlib import sha256
import pytest

from test_strategy_eight_configuration import prepared_eight
from pipelines.strategy_one.configuration_publisher import _verified_numbered_envelope
from pipelines.strategy_one.strategy_nine_configuration import compile_strategy_nine_configuration
from src.trading_runtime.journal_contract import canonical_json
from src.trading_runtime.strategy_nine_release import (
    PARENT_REVISION_ID, PARENT_PAYLOAD_HASH, FOLLOWTHROUGH_POLICY,
    verify_strategy_nine_manifest,
)
from src.trading_runtime.strategy_registry import numbered_strategy_parent, numbered_strategy, fixed_strategy_executor
from test_fixed_numbered_registry import assignment


def pinned_parent():
    # Synthetic inherited payload for compiler checks only. This does not claim
    # a certified DB read or recompute the production parent's content hash.
    reader, _, _ = prepared_eight()
    from src.backend.backtest_strategy_one_configuration import certify_numbered_configuration
    parent = certify_numbered_configuration(reader, 8)
    return replace(parent, attempt_id=PARENT_REVISION_ID.split(":")[1], payload_hash=PARENT_PAYLOAD_HASH)


def compile_nine(parent):
    return compile_strategy_nine_configuration(parent, approved_code_commit="d" * 40,
        approved_code_fingerprint="e" * 64, approval_reference="approved-single-failure-exit")


def test_ninth_seal_preserves_parent_parameters_policies_and_immutability():
    parent = pinned_parent()
    previous = deepcopy(parent.payload)
    envelope = compile_nine(parent)
    _verified_numbered_envelope(envelope)
    payload = envelope["payload"]
    assert parent.payload == previous
    assert payload["strategy"]["parameters"] == previous["strategy"]["parameters"]
    manifest = payload["strategy"]["numbered_release"]
    assert manifest["source_revision_id"] == PARENT_REVISION_ID
    assert manifest["source_payload_hash"] == PARENT_PAYLOAD_HASH
    for key in ("activation_policy", "session_policy", "add_policy", "trailing_policy", "target_policy", "entry_price_policy"):
        assert manifest[key] == previous["strategy"]["numbered_release"][key]
    assert manifest["followthrough_policy"] == FOLLOWTHROUGH_POLICY
    assert numbered_strategy_parent(9) == 8
    assert numbered_strategy(9).number == 9


@pytest.mark.parametrize("changes", [{"attempt_id": "00000000-0000-0000-0000-000000000008"}, {"payload_hash": "0" * 64}])
def test_different_eighth_parent_rejected(changes):
    with pytest.raises(ValueError, match="exact pinned"):
        compile_nine(replace(pinned_parent(), **changes))


@pytest.mark.parametrize("field,value", [("publication_mode", "live"), ("source_revision_id", "strategy-one-8:00000000-0000-0000-0000-000000000008"), ("source_payload_hash", "0" * 64), ("followthrough_policy", {})])
def test_resealed_policy_or_parent_override_rejected(field, value):
    strategy = compile_nine(pinned_parent())["payload"]["strategy"]
    manifest = strategy["numbered_release"]
    manifest[field] = value
    manifest["manifest_hash"] = sha256(canonical_json({k: v for k, v in manifest.items() if k != "manifest_hash"}).encode()).hexdigest()
    with pytest.raises(ValueError, match="sealed contract"):
        verify_strategy_nine_manifest(strategy)


def test_ninth_runtime_remains_backtest_only():
    release = numbered_strategy(9)
    registration = fixed_strategy_executor(release.executor_strategy_id, 9)
    selected = replace(assignment(), strategy_revision=9)
    assert registration.build([selected], mode="backtest").contract.strategy_number == 9
    with pytest.raises(ValueError, match="Backtest-only"):
        registration.build([selected], mode="live")


def test_ninth_certificate_binds_completed_rule_and_normalized_source_route():
    from src.backend.backtest_fixed_v4_certification import (
        certify_numbered_fixed_v4_projection, certify_followthrough_failure_v4_source,
    )
    assert len(certify_numbered_fixed_v4_projection(9)) == 64
    assert len(certify_followthrough_failure_v4_source()) == 64
