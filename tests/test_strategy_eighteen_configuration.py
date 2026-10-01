"""Strategy 18 derives exact17 and seals frozen first-setup strong momentum."""
from copy import deepcopy
from dataclasses import replace
from hashlib import sha256
import pytest
from pipelines.strategy_one.strategy_seventeen_configuration import compile_strategy_seventeen_configuration
from pipelines.strategy_one.strategy_eighteen_configuration import compile_strategy_eighteen_configuration
from src.backend.backtest_strategy_one_configuration import CertifiedStrategyOneConfiguration
from src.trading_runtime.journal_contract import canonical_json
from src.trading_runtime.strategy_eighteen_release import (
    PARENT_REVISION_ID, PARENT_PAYLOAD_HASH, RULES, STRONG_TEN_SECOND_MOMENTUM_POLICY, INITIAL_STRONG_MOMENTUM_POLICY,
    verify_strategy_eighteen_manifest,
)
from test_strategy_seventeen_configuration import parent as fourteenth_parent


def parent():
    # Synthetic compiler metadata; actual release reads remain independently certified.
    value = compile_strategy_seventeen_configuration(fourteenth_parent(),
        approved_code_commit="d" * 40, approved_code_fingerprint="e" * 64,
        approval_reference="test-numbered-admission")
    return CertifiedStrategyOneConfiguration(PARENT_REVISION_ID.split(":")[1],
        PARENT_PAYLOAD_HASH, value["node_hash"], value["source_candidate_id"],
        value["source_candidate_hash"], "test-only", value["payload"])


def compile_eighteen(source):
    return compile_strategy_eighteen_configuration(source, approved_code_commit="d" * 40,
        approved_code_fingerprint="e" * 64, approval_reference="frozen-first-setup-strong-momentum")


def test_eighteenth_exact_seventeenth_parent_preserves_policies_parameters_and_parent():
    source = parent()
    before = deepcopy(source.payload)
    result = compile_eighteen(source)
    from pipelines.strategy_one.configuration_publisher import _verified_numbered_envelope
    _verified_numbered_envelope(result)
    manifest = verify_strategy_eighteen_manifest(result["payload"]["strategy"])
    assert source.payload == before
    assert result["payload"]["strategy"]["parameters"] == before["strategy"]["parameters"]
    previous = before["strategy"]["numbered_release"]
    for key in ("momentum_policy", "recent_bos_policy", "session_policy", "followthrough_policy",
                "activation_policy", "add_policy", "trailing_policy", "target_policy",
                "entry_price_policy", "entry_scope_policy", "strong_ten_second_momentum_policy"):
        assert manifest[key] == previous[key]
    assert manifest["source_revision_id"] == PARENT_REVISION_ID
    assert manifest["source_payload_hash"] == PARENT_PAYLOAD_HASH
    assert manifest["strong_ten_second_momentum_policy"] == STRONG_TEN_SECOND_MOMENTUM_POLICY
    assert manifest["followthrough_policy"]["early_failure_window_ms"] == 60_000
    from src.trading_runtime.strategy_seventeen_release import RULES as parent_rules
    assert RULES[:-1] == parent_rules
    assert RULES[-1] == "strategy-eighteen-first-strong-momentum-setup-v1"
    assert "strategy-eleven-early-followthrough-failure-v1" in RULES
    assert "strategy-fifteen-unlimited-followthrough-failure-v1" not in RULES
    assert "strategy-sixteen-shared-numbered-session-reason-authority-v1" not in RULES
    from src.trading_runtime.strategy_registry import numbered_strategy_parent
    assert numbered_strategy_parent(18) == 17


@pytest.mark.parametrize("changes", [{"attempt_id": "00000000-0000-0000-0000-000000000001"},
                                     {"payload_hash": "0" * 64}])
def test_eighteenth_exact_published_parent_required(changes):
    with pytest.raises(ValueError, match="exact pinned"):
        compile_eighteen(replace(parent(), **changes))


def test_eighteenth_rejects_inherited_mutable_assignments():
    source = parent()
    payload = deepcopy(source.payload)
    payload["assignments"] = [{"mutable": True}]
    with pytest.raises(ValueError, match="mutable assignments"):
        compile_eighteen(replace(source, payload=payload))


@pytest.mark.parametrize("field,value", [
    ("publication_mode", "live"), ("strong_ten_second_momentum_policy", {}),
    ("source_payload_hash", "0" * 64),
    ("source_revision_id", "strategy-one-16:00000000-0000-0000-0000-000000000001"),
    ("followthrough_policy", {}), ("momentum_policy", {}), ("initial_strong_momentum_policy", {}),
])
def test_eighteenth_resealed_policy_or_parent_override_rejected(field, value):
    strategy = compile_eighteen(parent())["payload"]["strategy"]
    manifest = strategy["numbered_release"]
    manifest[field] = value
    manifest["manifest_hash"] = sha256(canonical_json({k: v for k, v in manifest.items()
                                                    if k != "manifest_hash"}).encode()).hexdigest()
    with pytest.raises(ValueError, match="installed sealed contract"):
        verify_strategy_eighteen_manifest(strategy)


@pytest.mark.parametrize("number", [True, 18.0, "18", 17])
def test_eighteenth_rejects_ambiguous_execution_identity(number):
    strategy = compile_eighteen(parent())["payload"]["strategy"]
    strategy["strategy_number"] = number
    with pytest.raises(ValueError, match="execution identity"):
        verify_strategy_eighteen_manifest(strategy)


def test_eighteenth_policy_uses_shared_rule_authority():
    from src.trading_runtime.strategy_initial_strong_momentum import POLICY_ID, initial_strong_momentum_policy_payload
    assert RULES[-1] == POLICY_ID
    assert INITIAL_STRONG_MOMENTUM_POLICY == initial_strong_momentum_policy_payload()
    assert INITIAL_STRONG_MOMENTUM_POLICY['eligibility'] == 'base_eligible and current_strong and first_setup_strong'


def test_eighteenth_runtime_remains_backtest_only_and_source_certified():
    from test_fixed_numbered_registry import assignment
    from src.trading_runtime.strategy_registry import numbered_strategy, fixed_strategy_executor
    from src.backend.backtest_fixed_v4_certification import certify_numbered_fixed_v4_projection
    release = numbered_strategy(18)
    registration = fixed_strategy_executor(release.executor_strategy_id, 18)
    assert registration.build([replace(assignment(), strategy_revision=18)], mode="backtest").contract.strategy_number == 18
    with pytest.raises(ValueError, match="Backtest-only"):
        registration.build([replace(assignment(), strategy_revision=18)], mode="live")
    assert len(certify_numbered_fixed_v4_projection(18)) == 64



def test_eighteenth_resealed_weaker_growth_fraction_rejected():
    strategy = compile_eighteen(parent())["payload"]["strategy"]
    manifest = strategy["numbered_release"]
    manifest["strong_ten_second_momentum_policy"]["fraction"] = 0.0
    manifest["manifest_hash"] = sha256(canonical_json({k: v for k, v in manifest.items()
                                                    if k != "manifest_hash"}).encode()).hexdigest()
    with pytest.raises(ValueError, match="installed sealed contract"):
        verify_strategy_eighteen_manifest(strategy)


def test_eighteenth_resealed_first_setup_freeze_override_rejected():
    strategy = compile_eighteen(parent())["payload"]["strategy"]
    manifest = strategy["numbered_release"]
    manifest["initial_strong_momentum_policy"]["freeze"] = "reset_after_close"
    manifest["manifest_hash"] = sha256(canonical_json({k: v for k, v in manifest.items()
                                                    if k != "manifest_hash"}).encode()).hexdigest()
    with pytest.raises(ValueError, match="installed sealed contract"):
        verify_strategy_eighteen_manifest(strategy)
