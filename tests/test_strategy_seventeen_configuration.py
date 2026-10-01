"""Strategy 17 branches from exact14 and seals one stronger completed10s gate."""
from copy import deepcopy
from dataclasses import replace
from hashlib import sha256
import pytest
from pipelines.strategy_one.strategy_fourteen_configuration import compile_strategy_fourteen_configuration
from pipelines.strategy_one.strategy_seventeen_configuration import compile_strategy_seventeen_configuration
from src.backend.backtest_strategy_one_configuration import CertifiedStrategyOneConfiguration
from src.trading_runtime.journal_contract import canonical_json
from src.trading_runtime.strategy_seventeen_release import (
    PARENT_REVISION_ID, PARENT_PAYLOAD_HASH, RULES, STRONG_TEN_SECOND_MOMENTUM_POLICY,
    verify_strategy_seventeen_manifest,
)
from test_strategy_fourteen_configuration import parent as thirteenth_parent


def parent():
    # Synthetic compiler metadata; actual release reads remain independently certified.
    value = compile_strategy_fourteen_configuration(thirteenth_parent(),
        approved_code_commit="d" * 40, approved_code_fingerprint="e" * 64,
        approval_reference="test-numbered-admission")
    return CertifiedStrategyOneConfiguration(PARENT_REVISION_ID.split(":")[1],
        PARENT_PAYLOAD_HASH, value["node_hash"], value["source_candidate_id"],
        value["source_candidate_hash"], "test-only", value["payload"])


def compile_seventeen(source):
    return compile_strategy_seventeen_configuration(source, approved_code_commit="d" * 40,
        approved_code_fingerprint="e" * 64, approval_reference="positive-completed10s-growth10pct")


def test_seventeenth_direct_fourteenth_branch_preserves_policies_parameters_and_parent():
    source = parent()
    before = deepcopy(source.payload)
    result = compile_seventeen(source)
    from pipelines.strategy_one.configuration_publisher import _verified_numbered_envelope
    _verified_numbered_envelope(result)
    manifest = verify_strategy_seventeen_manifest(result["payload"]["strategy"])
    assert source.payload == before
    assert result["payload"]["strategy"]["parameters"] == before["strategy"]["parameters"]
    previous = before["strategy"]["numbered_release"]
    for key in ("momentum_policy", "recent_bos_policy", "session_policy", "followthrough_policy",
                "activation_policy", "add_policy", "trailing_policy", "target_policy",
                "entry_price_policy", "entry_scope_policy"):
        assert manifest[key] == previous[key]
    assert manifest["source_revision_id"] == PARENT_REVISION_ID
    assert manifest["source_payload_hash"] == PARENT_PAYLOAD_HASH
    assert manifest["strong_ten_second_momentum_policy"] == STRONG_TEN_SECOND_MOMENTUM_POLICY
    assert manifest["followthrough_policy"]["early_failure_window_ms"] == 60_000
    from src.trading_runtime.strategy_fourteen_release import RULES as parent_rules
    assert RULES[:-1] == parent_rules
    assert RULES[-1] == "strategy-seventeen-positive-ten-second-histogram-growth-10pct-v1"
    assert "strategy-eleven-early-followthrough-failure-v1" in RULES
    assert "strategy-fifteen-unlimited-followthrough-failure-v1" not in RULES
    assert "strategy-sixteen-shared-numbered-session-reason-authority-v1" not in RULES
    from src.trading_runtime.strategy_registry import numbered_strategy_parent
    assert numbered_strategy_parent(17) == 14


@pytest.mark.parametrize("changes", [{"attempt_id": "00000000-0000-0000-0000-000000000001"},
                                     {"payload_hash": "0" * 64}])
def test_seventeenth_exact_published_parent_required(changes):
    with pytest.raises(ValueError, match="exact pinned"):
        compile_seventeen(replace(parent(), **changes))


def test_seventeenth_rejects_inherited_mutable_assignments():
    source = parent()
    payload = deepcopy(source.payload)
    payload["assignments"] = [{"mutable": True}]
    with pytest.raises(ValueError, match="mutable assignments"):
        compile_seventeen(replace(source, payload=payload))


@pytest.mark.parametrize("field,value", [
    ("publication_mode", "live"), ("strong_ten_second_momentum_policy", {}),
    ("source_payload_hash", "0" * 64),
    ("source_revision_id", "strategy-one-16:00000000-0000-0000-0000-000000000001"),
    ("followthrough_policy", {}), ("momentum_policy", {}),
])
def test_seventeenth_resealed_policy_or_parent_override_rejected(field, value):
    strategy = compile_seventeen(parent())["payload"]["strategy"]
    manifest = strategy["numbered_release"]
    manifest[field] = value
    manifest["manifest_hash"] = sha256(canonical_json({k: v for k, v in manifest.items()
                                                    if k != "manifest_hash"}).encode()).hexdigest()
    with pytest.raises(ValueError, match="installed sealed contract"):
        verify_strategy_seventeen_manifest(strategy)


@pytest.mark.parametrize("number", [True, 17.0, "17", 14])
def test_seventeenth_rejects_ambiguous_execution_identity(number):
    strategy = compile_seventeen(parent())["payload"]["strategy"]
    strategy["strategy_number"] = number
    with pytest.raises(ValueError, match="execution identity"):
        verify_strategy_seventeen_manifest(strategy)


def test_seventeenth_policy_uses_shared_rule_authority():
    from src.trading_runtime.strategy_strong_ten_second_momentum import (
        POLICY_ID, strong_ten_second_momentum_policy_payload,
    )
    assert RULES[-1] == POLICY_ID
    assert STRONG_TEN_SECOND_MOMENTUM_POLICY == strong_ten_second_momentum_policy_payload()
    assert STRONG_TEN_SECOND_MOMENTUM_POLICY["policy_id"] == RULES[-1]
    assert STRONG_TEN_SECOND_MOMENTUM_POLICY["fraction"] == .10
    assert STRONG_TEN_SECOND_MOMENTUM_POLICY["resolution_ms"] == 10_000
    assert STRONG_TEN_SECOND_MOMENTUM_POLICY["comparison"] == (
        "current_histogram > 0 and current_histogram > prior_histogram + 0.10 * abs(prior_histogram)")
    assert STRONG_TEN_SECOND_MOMENTUM_POLICY["scope"] == "entry_and_reentry_only"


def test_seventeenth_runtime_remains_backtest_only_and_source_certified():
    from test_fixed_numbered_registry import assignment
    from src.trading_runtime.strategy_registry import numbered_strategy, fixed_strategy_executor
    from src.backend.backtest_fixed_v4_certification import certify_numbered_fixed_v4_projection
    release = numbered_strategy(17)
    registration = fixed_strategy_executor(release.executor_strategy_id, 17)
    assert registration.build([replace(assignment(), strategy_revision=17)], mode="backtest").contract.strategy_number == 17
    with pytest.raises(ValueError, match="Backtest-only"):
        registration.build([replace(assignment(), strategy_revision=17)], mode="live")
    assert len(certify_numbered_fixed_v4_projection(17)) == 64



def test_seventeenth_resealed_weaker_growth_fraction_rejected():
    strategy = compile_seventeen(parent())["payload"]["strategy"]
    manifest = strategy["numbered_release"]
    manifest["strong_ten_second_momentum_policy"]["fraction"] = 0.0
    manifest["manifest_hash"] = sha256(canonical_json({k: v for k, v in manifest.items()
                                                    if k != "manifest_hash"}).encode()).hexdigest()
    with pytest.raises(ValueError, match="installed sealed contract"):
        verify_strategy_seventeen_manifest(strategy)
