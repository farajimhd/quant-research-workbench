"""The eighth seal caps acquisition while preserving the fixed initial target."""
from copy import deepcopy
from dataclasses import replace
import pytest
import json
from test_strategy_six_configuration import prepared_six
from pipelines.strategy_one.strategy_eight_configuration import compile_strategy_eight_configuration
from pipelines.strategy_one.configuration_publisher import _verified_numbered_envelope
from src.backend.backtest_strategy_one_configuration import certify_numbered_configuration, selected_numbered_revision
from src.trading_runtime.strategy_eight_release import ACTIVATION_POLICY, ADD_POLICY, TRAILING_POLICY, TARGET_POLICY, verify_strategy_eight_manifest, verify_installed_strategy_eight_release
from src.trading_runtime.strategy_registry import numbered_strategy, fixed_strategy_executor
from test_fixed_numbered_registry import assignment


def prepared_eight():
    reader, _, _ = prepared_six()
    parent = certify_numbered_configuration(reader, 6)
    eighth = compile_strategy_eight_configuration(parent, approved_code_commit="d" * 40,
        approved_code_fingerprint="e" * 64, approval_reference="approved-eighth-reference-ask-cap")
    reader.payloads[8] = eighth["payload"]
    reader.sources[8] = (eighth["source_candidate_id"], eighth["source_candidate_hash"])
    return reader, parent, eighth


def test_eighth_roundtrip_preserves_sixth_seal_parameters_and_source():
    reader, parent, eighth = prepared_eight()
    previous = deepcopy(parent.payload)
    _verified_numbered_envelope(eighth)
    result = certify_numbered_configuration(reader, 8)
    assert result.payload == eighth["payload"]
    assert certify_numbered_configuration(reader, 6).token == parent.token
    assert parent.payload == previous
    assert result.payload["strategy"]["parameters"] == parent.payload["strategy"]["parameters"]
    manifest = result.payload["strategy"]["numbered_release"]
    assert manifest["source_revision_id"] == parent.revision()["revision_id"]
    assert manifest["source_payload_hash"] == parent.payload_hash
    assert manifest["session_policy"] == previous["strategy"]["numbered_release"]["session_policy"]
    assert manifest["activation_policy"] == ACTIVATION_POLICY
    assert manifest["add_policy"] == ADD_POLICY
    assert manifest["trailing_policy"] == TRAILING_POLICY
    assert manifest["target_policy"] == TARGET_POLICY
    assert TARGET_POLICY["target_escalation"] is False
    assert TRAILING_POLICY["completed_30s_low_trailing"] is False
    assert ADD_POLICY == {"allows_adds": False, "scope": "disable_adds_only"}
    assert selected_numbered_revision(client=reader, revision_id=result.revision()["revision_id"])["content_hash"] == result.payload_hash
    assert verify_installed_strategy_eight_release(manifest).number == 8


def test_hidden_eighth_override_and_forged_activation_rejected():
    reader, _, eighth = prepared_eight()
    reader.payloads[8]["strategy"]["parameters"]["sizing"]["budget"] += 1
    with pytest.raises(RuntimeError, match="certified inheritance"):
        certify_numbered_configuration(reader, 8)
    eighth["payload"]["strategy"]["numbered_release"]["activation_policy"]["afterhours_open_ms"] = 0
    with pytest.raises(ValueError, match="sealed contract"):
        verify_strategy_eight_manifest(eighth["payload"]["strategy"])


def test_eighth_registry_uses_real_backtest_only_executor():
    release = numbered_strategy(8)
    registration = fixed_strategy_executor(release.executor_strategy_id, 8)
    row = replace(assignment(), strategy_revision=8)
    runtime = registration.build([row], mode="backtest")
    assert runtime.revision == 8 and runtime.contract.strategy_number == 8
    with pytest.raises(ValueError, match="Backtest-only"):
        registration.build([row], mode="live")


def test_eighth_rejects_assignment_overrides_and_preserves_input_clocks():
    from src.backend.trading_configuration_service import merged_assignment_parameters
    from src.backend.backtest_market_data import compile_required_resolutions, ExecutionInterval
    _, _, eighth = prepared_eight()
    payload = eighth["payload"]
    assert compile_required_resolutions(payload, ExecutionInterval.parse("100ms")) == (100, 1000, 5000, 10000, 30000)
    with pytest.raises(ValueError, match="cannot override"):
        merged_assignment_parameters(payload, {"parameters": {"sizing": {"budget": 9999}}})


def test_eighth_publication_coverage_last_idempotent_and_preserves_sixth(monkeypatch):
    from pipelines.strategy_one import configuration_publisher as publisher
    from src.trading_runtime.strategy_one_configuration_tree import decode_nodes
    reader, source, envelope = prepared_eight()
    reader.payloads.pop(8)
    writes = []
    nodes = []
    release_rows = []
    original_execute = reader.execute

    def execute(query):
        if query.startswith("INSERT "):
            rows = [json.loads(line) for line in query.split("\n")[1:]]
            assert all(row["strategy_number"] == 8 for row in rows)
            writes.append("release" if "configuration_release" in query else "nodes")
            if writes[-1] == "release":
                assert nodes
                release_rows.extend(rows)
            else:
                assert not release_rows
                nodes.extend({key: value for key, value in row.items()
                              if key not in {"strategy_number", "release_attempt_id"}} for row in rows)
            return ""
        if "strategy_number=8" in query:
            rows = release_rows if "configuration_release" in query else nodes
            return "\n".join(json.dumps(row) for row in rows)
        return original_execute(query)

    class Keeper:
        connected = True
        def create(self, path, *_args, **_kwargs):
            assert path.endswith("/8")
        def delete(self, path):
            assert path.endswith("/8")

    monkeypatch.setattr(reader, "execute", execute)
    monkeypatch.setattr(publisher, "verify_tables", lambda _client: None)
    token = publisher.publish_configuration(reader, Keeper(), envelope)
    assert writes[-1] == "release" and writes.count("release") == 1
    assert decode_nodes(nodes) == envelope["payload"]
    count = len(writes)
    assert publisher.publish_configuration(reader, Keeper(), envelope) == token
    assert len(writes) == count
    assert certify_numbered_configuration(reader, 6).token == source.token


def test_eighth_rejects_reenabled_add_policy():
    _, _, eighth = prepared_eight()
    eighth["payload"]["strategy"]["numbered_release"]["add_policy"]["allows_adds"] = True
    with pytest.raises(ValueError, match="sealed contract"):
        verify_strategy_eight_manifest(eighth["payload"]["strategy"])


def test_eighth_rejects_reenabled_completed_30s_trailing():
    _, _, eighth = prepared_eight()
    eighth["payload"]["strategy"]["numbered_release"]["trailing_policy"]["completed_30s_low_trailing"] = True
    with pytest.raises(ValueError, match="sealed contract"):
        verify_strategy_eight_manifest(eighth["payload"]["strategy"])


def test_eighth_rejects_reenabled_target_escalation():
    _, _, eighth = prepared_eight()
    eighth["payload"]["strategy"]["numbered_release"]["target_policy"]["target_escalation"] = True
    with pytest.raises(ValueError, match="sealed contract"):
        verify_strategy_eight_manifest(eighth["payload"]["strategy"])


def test_eighth_explicitly_rejects_seventh_parent():
    from test_strategy_seven_configuration import prepared_seven
    reader, _, _ = prepared_seven()
    parent = certify_numbered_configuration(reader, 7)
    with pytest.raises(ValueError, match="certified Strategy 6"):
        compile_strategy_eight_configuration(parent, approved_code_commit="d"*40,
            approved_code_fingerprint="e"*64, approval_reference="wrong-parent")


@pytest.mark.parametrize("key,value", [("maximum_buy_price", "current_ask"),
    ("persist_until_cancelled", False), ("partial_fill_policy", "cancel_remainder")])
def test_eighth_rejects_changed_acquisition_contract(key, value):
    _, _, envelope = prepared_eight()
    envelope["payload"]["strategy"]["numbered_release"]["entry_price_policy"][key] = value
    with pytest.raises(ValueError, match="sealed contract"):
        verify_strategy_eight_manifest(envelope["payload"]["strategy"])


@pytest.mark.parametrize("number", [True, False, 1, 0, 10, "8", 8.0])
def test_numbered_parent_rejects_unadmitted_values(number):
    from src.trading_runtime.strategy_registry import numbered_strategy_parent
    with pytest.raises(ValueError, match="admitted parent"):
        numbered_strategy_parent(number)


def test_eighth_rejects_forged_seventh_parent_even_with_recomputed_manifest_seal():
    from hashlib import sha256
    from src.trading_runtime.journal_contract import canonical_json
    _, _, envelope = prepared_eight()
    manifest = envelope["payload"]["strategy"]["numbered_release"]
    manifest["source_revision_id"] = manifest["source_revision_id"].replace("strategy-one-6:", "strategy-one-7:")
    manifest["manifest_hash"] = sha256(canonical_json({k:v for k,v in manifest.items() if k != "manifest_hash"}).encode()).hexdigest()
    with pytest.raises(ValueError, match="sealed contract"):
        verify_strategy_eight_manifest(envelope["payload"]["strategy"])


def test_eighth_certificate_rejects_uncapped_factory(monkeypatch):
    from pathlib import Path
    from src.backend.backtest_fixed_v4_certification import certify_numbered_fixed_v4_projection
    original = Path.read_text
    def altered(path, *args, **kwargs):
        text = original(path, *args, **kwargs)
        if path.name == "strategy_one_intent.py":
            text = text.replace("maximum_buy_price=(proposal.reference_ask if numbered_fixed_strategy(\n                    proposal.strategy_number).caps_entry_at_reference_ask else None)", "maximum_buy_price=None")
        return text
    monkeypatch.setattr(Path, "read_text", altered)
    with pytest.raises(ValueError, match="cap acquisition"):
        certify_numbered_fixed_v4_projection(8)
