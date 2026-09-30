"""Strategy 10 seals one exit rule and rejects any different parent authority."""
from copy import deepcopy
from dataclasses import replace
from hashlib import sha256
import pytest

from test_strategy_eight_configuration import prepared_eight
from pipelines.strategy_one.configuration_publisher import _verified_numbered_envelope
from pipelines.strategy_one.strategy_ten_configuration import compile_strategy_ten_configuration
from src.trading_runtime.journal_contract import canonical_json
from src.trading_runtime.strategy_ten_release import (
    PARENT_REVISION_ID, PARENT_PAYLOAD_HASH, FOLLOWTHROUGH_POLICY, ENTRY_SCOPE_POLICY,
    verify_strategy_ten_manifest,
)
from src.trading_runtime.strategy_registry import numbered_strategy_parent, numbered_strategy, fixed_strategy_executor
from test_fixed_numbered_registry import assignment


def pinned_parent():
    # Synthetic metadata for compiler contract tests, not DB certification.
    from test_strategy_nine_configuration import pinned_parent as eighth_parent, compile_nine
    from src.backend.backtest_strategy_one_configuration import CertifiedStrategyOneConfiguration
    envelope = compile_nine(eighth_parent())
    return CertifiedStrategyOneConfiguration(PARENT_REVISION_ID.split(":")[1],
        PARENT_PAYLOAD_HASH, envelope["node_hash"], envelope["source_candidate_id"],
        envelope["source_candidate_hash"], "test-only", envelope["payload"])


def compile_ten(parent):
    return compile_strategy_ten_configuration(parent, approved_code_commit="d" * 40,
        approved_code_fingerprint="e" * 64, approval_reference="approved-single-failure-exit")


def test_tenth_seal_preserves_parent_parameters_policies_and_immutability():
    parent = pinned_parent()
    previous = deepcopy(parent.payload)
    envelope = compile_ten(parent)
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
    assert manifest["entry_scope_policy"] == ENTRY_SCOPE_POLICY
    assert "entry_scope_policy" not in previous["strategy"]["numbered_release"]
    assert numbered_strategy_parent(10) == 9
    assert numbered_strategy(10).number == 10


@pytest.mark.parametrize("changes", [{"attempt_id": "00000000-0000-0000-0000-000000000008"}, {"payload_hash": "0" * 64}])
def test_different_ninth_parent_rejected(changes):
    with pytest.raises(ValueError, match="exact pinned"):
        compile_ten(replace(pinned_parent(), **changes))


@pytest.mark.parametrize("field,value", [("publication_mode", "live"), ("source_revision_id", "strategy-one-8:00000000-0000-0000-0000-000000000008"), ("source_payload_hash", "0" * 64), ("followthrough_policy", {}), ("entry_scope_policy", {})])
def test_resealed_policy_or_parent_override_rejected(field, value):
    strategy = compile_ten(pinned_parent())["payload"]["strategy"]
    manifest = strategy["numbered_release"]
    manifest[field] = value
    manifest["manifest_hash"] = sha256(canonical_json({k: v for k, v in manifest.items() if k != "manifest_hash"}).encode()).hexdigest()
    with pytest.raises(ValueError, match="sealed contract"):
        verify_strategy_ten_manifest(strategy)


def test_tenth_runtime_remains_backtest_only():
    release = numbered_strategy(10)
    registration = fixed_strategy_executor(release.executor_strategy_id, 10)
    selected = replace(assignment(), strategy_revision=10)
    assert registration.build([selected], mode="backtest").contract.strategy_number == 10
    with pytest.raises(ValueError, match="Backtest-only"):
        registration.build([selected], mode="live")


def test_tenth_certificate_binds_completed_rule_and_normalized_source_route():
    from src.backend.backtest_fixed_v4_certification import (
        certify_numbered_fixed_v4_projection, certify_followthrough_failure_v4_source, certify_empty_exclusion_entry_scope_source,
    )
    assert len(certify_numbered_fixed_v4_projection(10)) == 64
    assert len(certify_followthrough_failure_v4_source()) == 64
    assert len(certify_empty_exclusion_entry_scope_source()) == 64


@pytest.mark.parametrize('before,after', [
    ('row.candidate_count != 0', 'row.candidate_count < 0'),
    ('original_activations.rows != activations.rows', 'False'),
    ('full = certify_candidate_plan(', 'full = unavailable_candidate_plan('),
])
def test_tenth_scope_source_guard_mutation_rejected(tmp_path, before, after):
    from pathlib import Path
    from src.backend.backtest_fixed_v4_certification import certify_empty_exclusion_entry_scope_source
    path = Path(__file__).parents[1] / 'src/backend/backtest_strategy_one_entry_store.py'
    altered = tmp_path / 'altered_entry_store.py'
    altered.write_text(path.read_text().replace(before, after))
    with pytest.raises(ValueError, match='Strategy 10 scope'):
        certify_empty_exclusion_entry_scope_source(source_path=altered)
