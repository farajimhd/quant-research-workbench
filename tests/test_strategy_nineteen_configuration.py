"""Strategy 19 derives exact18 and seals frozen first-setup strong momentum."""
from copy import deepcopy
from dataclasses import replace
from hashlib import sha256
import pytest
from pipelines.strategy_one.strategy_eighteen_configuration import compile_strategy_eighteen_configuration
from pipelines.strategy_one.strategy_nineteen_configuration import compile_strategy_nineteen_configuration
from src.backend.backtest_strategy_one_configuration import CertifiedStrategyOneConfiguration
from src.trading_runtime.journal_contract import canonical_json
from src.trading_runtime.strategy_nineteen_release import (
    PARENT_REVISION_ID, PARENT_PAYLOAD_HASH, RULES, STRONG_TEN_SECOND_MOMENTUM_POLICY, INITIAL_STRONG_MOMENTUM_POLICY,
    verify_strategy_nineteen_manifest, FIRST_SETUP_GROWTH_POLICY,
)
from test_strategy_eighteen_configuration import parent as seventeenth_parent


def parent():
    # Synthetic compiler metadata; actual release reads remain independently certified.
    value = compile_strategy_eighteen_configuration(seventeenth_parent(),
        approved_code_commit="d" * 40, approved_code_fingerprint="e" * 64,
        approval_reference="test-numbered-admission")
    return CertifiedStrategyOneConfiguration(PARENT_REVISION_ID.split(":")[1],
        PARENT_PAYLOAD_HASH, value["node_hash"], value["source_candidate_id"],
        value["source_candidate_hash"], "test-only", value["payload"])


def compile_nineteen(source):
    return compile_strategy_nineteen_configuration(source, approved_code_commit="d" * 40,
        approved_code_fingerprint="e" * 64, approval_reference="frozen-first-setup-strong-momentum")


def test_nineteenth_exact_eighteenth_parent_preserves_policies_parameters_and_parent():
    source = parent()
    before = deepcopy(source.payload)
    result = compile_nineteen(source)
    from pipelines.strategy_one.configuration_publisher import _verified_numbered_envelope
    _verified_numbered_envelope(result)
    manifest = verify_strategy_nineteen_manifest(result["payload"]["strategy"])
    assert source.payload == before
    assert result["payload"]["strategy"]["parameters"] == before["strategy"]["parameters"]
    previous = before["strategy"]["numbered_release"]
    for key in ("momentum_policy", "recent_bos_policy", "session_policy", "followthrough_policy",
                "activation_policy", "add_policy", "trailing_policy", "target_policy",
                "entry_price_policy", "entry_scope_policy", "strong_ten_second_momentum_policy",
                "initial_strong_momentum_policy"):
        assert manifest[key] == previous[key]
    assert manifest["source_revision_id"] == PARENT_REVISION_ID
    assert manifest["source_payload_hash"] == PARENT_PAYLOAD_HASH
    assert manifest["strong_ten_second_momentum_policy"] == STRONG_TEN_SECOND_MOMENTUM_POLICY
    assert manifest["followthrough_policy"]["early_failure_window_ms"] == 60_000
    from src.trading_runtime.strategy_eighteen_release import RULES as parent_rules
    assert RULES[:-1] == parent_rules
    assert RULES[-1] == "strategy-nineteen-premarket-first-setup-ten-second-histogram-growth-50pct-v1"
    assert "strategy-eleven-early-followthrough-failure-v1" in RULES
    assert "strategy-fifteen-unlimited-followthrough-failure-v1" not in RULES
    assert "strategy-sixteen-shared-numbered-session-reason-authority-v1" not in RULES
    from src.trading_runtime.strategy_registry import numbered_strategy_parent
    assert numbered_strategy_parent(19) == 18


@pytest.mark.parametrize("changes", [{"attempt_id": "00000000-0000-0000-0000-000000000001"},
                                     {"payload_hash": "0" * 64}])
def test_nineteenth_exact_published_parent_required(changes):
    with pytest.raises(ValueError, match="exact pinned"):
        compile_nineteen(replace(parent(), **changes))


def test_nineteenth_rejects_inherited_mutable_assignments():
    source = parent()
    payload = deepcopy(source.payload)
    payload["assignments"] = [{"mutable": True}]
    with pytest.raises(ValueError, match="mutable assignments"):
        compile_nineteen(replace(source, payload=payload))


@pytest.mark.parametrize("field,value", [
    ("publication_mode", "live"), ("strong_ten_second_momentum_policy", {}),
    ("source_payload_hash", "0" * 64),
    ("source_revision_id", "strategy-one-16:00000000-0000-0000-0000-000000000001"),
    ("followthrough_policy", {}), ("momentum_policy", {}), ("initial_strong_momentum_policy", {}), ("first_setup_growth_policy", {}),
])
def test_nineteenth_resealed_policy_or_parent_override_rejected(field, value):
    strategy = compile_nineteen(parent())["payload"]["strategy"]
    manifest = strategy["numbered_release"]
    manifest[field] = value
    manifest["manifest_hash"] = sha256(canonical_json({k: v for k, v in manifest.items()
                                                    if k != "manifest_hash"}).encode()).hexdigest()
    with pytest.raises(ValueError, match="installed sealed contract"):
        verify_strategy_nineteen_manifest(strategy)


@pytest.mark.parametrize("number", [True, 19.0, "19", 18])
def test_nineteenth_rejects_ambiguous_execution_identity(number):
    strategy = compile_nineteen(parent())["payload"]["strategy"]
    strategy["strategy_number"] = number
    with pytest.raises(ValueError, match="execution identity"):
        verify_strategy_nineteen_manifest(strategy)


def test_nineteenth_policy_uses_shared_rule_authority():
    from src.trading_runtime.strategy_initial_momentum_growth import POLICY_ID, first_setup_momentum_growth_policy_payload
    assert RULES[-1] == POLICY_ID
    assert FIRST_SETUP_GROWTH_POLICY == first_setup_momentum_growth_policy_payload()
    assert FIRST_SETUP_GROWTH_POLICY["session_scope"] == "premarket_only"
    assert FIRST_SETUP_GROWTH_POLICY["fraction"] == 0.5
    assert FIRST_SETUP_GROWTH_POLICY["current_entry_policy"] == "unchanged_strict_10pct"
    assert FIRST_SETUP_GROWTH_POLICY["afterhours_first_setup_policy"] == "unchanged_strict_10pct"
    assert INITIAL_STRONG_MOMENTUM_POLICY['eligibility'] == 'base_eligible and current_strong and first_setup_strong'


def test_nineteenth_runtime_remains_backtest_only_and_source_certified():
    from test_fixed_numbered_registry import assignment
    from src.trading_runtime.strategy_registry import numbered_strategy, fixed_strategy_executor
    from src.backend.backtest_fixed_v4_certification import certify_numbered_fixed_v4_projection
    release = numbered_strategy(19)
    registration = fixed_strategy_executor(release.executor_strategy_id, 19)
    assert registration.build([replace(assignment(), strategy_revision=19)], mode="backtest").contract.strategy_number == 19
    with pytest.raises(ValueError, match="Backtest-only"):
        registration.build([replace(assignment(), strategy_revision=19)], mode="live")
    assert len(certify_numbered_fixed_v4_projection(19)) == 64



def test_nineteenth_resealed_weaker_growth_fraction_rejected():
    strategy = compile_nineteen(parent())["payload"]["strategy"]
    manifest = strategy["numbered_release"]
    manifest["first_setup_growth_policy"]["fraction"] = 0.1
    manifest["manifest_hash"] = sha256(canonical_json({k: v for k, v in manifest.items()
                                                    if k != "manifest_hash"}).encode()).hexdigest()
    with pytest.raises(ValueError, match="installed sealed contract"):
        verify_strategy_nineteen_manifest(strategy)


def test_nineteenth_resealed_first_setup_freeze_override_rejected():
    strategy = compile_nineteen(parent())["payload"]["strategy"]
    manifest = strategy["numbered_release"]
    manifest["initial_strong_momentum_policy"]["freeze"] = "reset_after_close"
    manifest["manifest_hash"] = sha256(canonical_json({k: v for k, v in manifest.items()
                                                    if k != "manifest_hash"}).encode()).hexdigest()
    with pytest.raises(ValueError, match="installed sealed contract"):
        verify_strategy_nineteen_manifest(strategy)


def test_nineteenth_resealed_all_session_scope_rejected():
    strategy = compile_nineteen(parent())["payload"]["strategy"]
    manifest = strategy["numbered_release"]
    manifest["first_setup_growth_policy"]["session_scope"] = "all_sessions"
    manifest["manifest_hash"] = sha256(canonical_json({
        key: value for key, value in manifest.items() if key != "manifest_hash"
    }).encode()).hexdigest()
    with pytest.raises(ValueError, match="installed sealed contract"):
        verify_strategy_nineteen_manifest(strategy)
