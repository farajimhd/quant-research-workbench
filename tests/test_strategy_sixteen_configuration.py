"""Immutable successor repairs session-reason authority and preserves Strategy15."""
from copy import deepcopy
from dataclasses import replace
from hashlib import sha256
import pytest
from pipelines.strategy_one.strategy_sixteen_configuration import compile_strategy_sixteen_configuration
from src.backend.backtest_strategy_one_configuration import CertifiedStrategyOneConfiguration
from src.trading_runtime.journal_contract import canonical_json
from src.trading_runtime.strategy_sixteen_release import (
    PARENT_REVISION_ID, PARENT_PAYLOAD_HASH, RULES, SESSION_EXIT_AUTHORITY_POLICY,
    verify_strategy_sixteen_manifest,
)


def parent():
    # Synthetic compiler metadata, not a claim of DB certification.
    from test_strategy_fifteen_configuration import parent as fourteenth_parent, compile_fifteen
    value = compile_fifteen(fourteenth_parent())
    return CertifiedStrategyOneConfiguration(PARENT_REVISION_ID.split(":")[1],
        PARENT_PAYLOAD_HASH, value["node_hash"], value["source_candidate_id"],
        value["source_candidate_hash"], "test-only", value["payload"])


def compile_sixteen(source):
    return compile_strategy_sixteen_configuration(source, approved_code_commit="d" * 40,
        approved_code_fingerprint="e" * 64, approval_reference="shared-numbered-session-authority")


def test_sixteenth_operational_successor_preserves_every_fifteenth_policy_and_rule():
    original = parent()
    before = deepcopy(original.payload)
    result = compile_sixteen(original)
    from pipelines.strategy_one.configuration_publisher import _verified_numbered_envelope
    _verified_numbered_envelope(result)
    manifest = verify_strategy_sixteen_manifest(result["payload"]["strategy"])
    assert original.payload == before
    assert result["payload"]["strategy"]["parameters"] == before["strategy"]["parameters"]
    previous = before["strategy"]["numbered_release"]
    for key in ("momentum_policy", "recent_bos_policy", "session_policy", "followthrough_policy",
                "activation_policy", "add_policy", "trailing_policy", "target_policy",
                "entry_price_policy", "entry_scope_policy"):
        assert manifest[key] == previous[key]
    assert manifest["source_revision_id"] == PARENT_REVISION_ID
    assert manifest["source_payload_hash"] == PARENT_PAYLOAD_HASH
    assert manifest["session_exit_authority_policy"] == SESSION_EXIT_AUTHORITY_POLICY
    assert "early_failure_window_ms" not in manifest["followthrough_policy"]
    from src.trading_runtime.strategy_fifteen_release import RULES as parent_rules
    assert RULES[:-1] == parent_rules
    assert RULES[-1] == "strategy-sixteen-shared-numbered-session-reason-authority-v1"
    assert "strategy-fifteen-unlimited-followthrough-failure-v1" in RULES
    assert "strategy-eleven-early-followthrough-failure-v1" not in RULES
    from src.trading_runtime.strategy_registry import numbered_strategy_parent
    assert numbered_strategy_parent(16) == 15


@pytest.mark.parametrize("changes", [{"attempt_id": "00000000-0000-0000-0000-000000000001"},
                                     {"payload_hash": "0" * 64}])
def test_sixteenth_exact_published_parent_authority_required(changes):
    with pytest.raises(ValueError, match="exact pinned"):
        compile_sixteen(replace(parent(), **changes))


def test_sixteenth_rejects_inherited_mutable_assignments():
    source = parent()
    payload = deepcopy(source.payload)
    payload["assignments"] = [{"mutable": True}]
    with pytest.raises(ValueError, match="mutable assignments"):
        compile_sixteen(replace(source, payload=payload))


@pytest.mark.parametrize("field,value", [
    ("publication_mode", "live"), ("session_exit_authority_policy", {}),
    ("source_payload_hash", "0" * 64), ("source_revision_id", "strategy-one-14:00000000-0000-0000-0000-000000000001"),
    ("followthrough_policy", {"early_failure_window_ms": 60_000}),
])
def test_sixteenth_resealed_authority_or_behavior_override_rejected(field, value):
    strategy = compile_sixteen(parent())["payload"]["strategy"]
    manifest = strategy["numbered_release"]
    manifest[field] = value
    manifest["manifest_hash"] = sha256(canonical_json({k: v for k, v in manifest.items()
                                                    if k != "manifest_hash"}).encode()).hexdigest()
    with pytest.raises(ValueError, match="installed sealed contract"):
        verify_strategy_sixteen_manifest(strategy)


@pytest.mark.parametrize("number", [True, 16.0, "16", 15])
def test_sixteenth_rejects_ambiguous_execution_identity(number):
    strategy = compile_sixteen(parent())["payload"]["strategy"]
    strategy["strategy_number"] = number
    with pytest.raises(ValueError, match="execution identity"):
        verify_strategy_sixteen_manifest(strategy)


def test_sixteenth_runtime_remains_backtest_only_and_source_certified():
    from test_fixed_numbered_registry import assignment
    from src.trading_runtime.strategy_registry import numbered_strategy, fixed_strategy_executor
    from src.backend.backtest_fixed_v4_certification import certify_numbered_fixed_v4_projection
    release = numbered_strategy(16)
    registration = fixed_strategy_executor(release.executor_strategy_id, 16)
    assert registration.build([replace(assignment(), strategy_revision=16)], mode="backtest").contract.strategy_number == 16
    with pytest.raises(ValueError, match="Backtest-only"):
        registration.build([replace(assignment(), strategy_revision=16)], mode="live")
    assert len(certify_numbered_fixed_v4_projection(16)) == 64


def test_sixteenth_authority_policy_names_exact_shared_helper_and_consumers():
    from src.trading_runtime.numbered_fixed_strategy import numbered_session_exit_reason
    assert SESSION_EXIT_AUTHORITY_POLICY["reason_authority"] == "numbered_session_exit_reason(strategy_number)"
    assert SESSION_EXIT_AUTHORITY_POLICY["consumers"] == "session_exit_factory_runtime_admission_and_memory_journal"
    assert numbered_session_exit_reason(16) == "strategy_sixteen_session_exit"
    assert numbered_session_exit_reason(15) == "strategy_fifteen_session_exit"
