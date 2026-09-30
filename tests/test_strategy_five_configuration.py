"""The fifth seal disables only subsequent completed-30s-low trailing."""
from copy import deepcopy
from dataclasses import replace
import pytest
import json
from test_strategy_four_configuration import prepared_four
from pipelines.strategy_one.strategy_five_configuration import compile_strategy_five_configuration
from pipelines.strategy_one.configuration_publisher import _verified_numbered_envelope
from src.backend.backtest_strategy_one_configuration import certify_numbered_configuration, selected_numbered_revision
from src.trading_runtime.strategy_five_release import ACTIVATION_POLICY, ADD_POLICY, TRAILING_POLICY, verify_strategy_five_manifest, verify_installed_strategy_five_release
from src.trading_runtime.strategy_registry import numbered_strategy, fixed_strategy_executor
from test_fixed_numbered_registry import assignment


def prepared_five():
    reader, _, _ = prepared_four()
    parent = certify_numbered_configuration(reader, 4)
    fifth = compile_strategy_five_configuration(parent, approved_code_commit="d" * 40,
        approved_code_fingerprint="e" * 64, approval_reference="approved-fifth-no-30s-trailing")
    reader.payloads[5] = fifth["payload"]
    reader.sources[5] = (fifth["source_candidate_id"], fifth["source_candidate_hash"])
    return reader, parent, fifth


def test_fifth_roundtrip_preserves_fourth_seal_parameters_and_source():
    reader, parent, fifth = prepared_five()
    previous = deepcopy(parent.payload)
    _verified_numbered_envelope(fifth)
    result = certify_numbered_configuration(reader, 5)
    assert result.payload == fifth["payload"]
    assert certify_numbered_configuration(reader, 4).token == parent.token
    assert parent.payload == previous
    assert result.payload["strategy"]["parameters"] == parent.payload["strategy"]["parameters"]
    manifest = result.payload["strategy"]["numbered_release"]
    assert manifest["source_revision_id"] == parent.revision()["revision_id"]
    assert manifest["source_payload_hash"] == parent.payload_hash
    assert manifest["session_policy"] == previous["strategy"]["numbered_release"]["session_policy"]
    assert manifest["activation_policy"] == ACTIVATION_POLICY
    assert manifest["add_policy"] == ADD_POLICY
    assert manifest["trailing_policy"] == TRAILING_POLICY
    assert TRAILING_POLICY["completed_30s_low_trailing"] is False
    assert ADD_POLICY == {"allows_adds": False, "scope": "disable_adds_only"}
    assert selected_numbered_revision(client=reader, revision_id=result.revision()["revision_id"])["content_hash"] == result.payload_hash
    assert verify_installed_strategy_five_release(manifest).number == 5


def test_hidden_fifth_override_and_forged_activation_rejected():
    reader, _, fifth = prepared_five()
    reader.payloads[5]["strategy"]["parameters"]["sizing"]["budget"] += 1
    with pytest.raises(RuntimeError, match="certified inheritance"):
        certify_numbered_configuration(reader, 5)
    fifth["payload"]["strategy"]["numbered_release"]["activation_policy"]["afterhours_open_ms"] = 0
    with pytest.raises(ValueError, match="sealed contract"):
        verify_strategy_five_manifest(fifth["payload"]["strategy"])


def test_fifth_registry_uses_real_backtest_only_executor():
    release = numbered_strategy(5)
    registration = fixed_strategy_executor(release.executor_strategy_id, 5)
    row = replace(assignment(), strategy_revision=5)
    runtime = registration.build([row], mode="backtest")
    assert runtime.revision == 5 and runtime.contract.strategy_number == 5
    with pytest.raises(ValueError, match="Backtest-only"):
        registration.build([row], mode="live")


def test_fifth_rejects_assignment_overrides_and_preserves_input_clocks():
    from src.backend.trading_configuration_service import merged_assignment_parameters
    from src.backend.backtest_market_data import compile_required_resolutions, ExecutionInterval
    _, _, fifth = prepared_five()
    payload = fifth["payload"]
    assert compile_required_resolutions(payload, ExecutionInterval.parse("100ms")) == (100, 1000, 5000, 10000, 30000)
    with pytest.raises(ValueError, match="cannot override"):
        merged_assignment_parameters(payload, {"parameters": {"sizing": {"budget": 9999}}})


def test_fifth_publication_coverage_last_idempotent_and_preserves_fourth(monkeypatch):
    from pipelines.strategy_one import configuration_publisher as publisher
    from src.trading_runtime.strategy_one_configuration_tree import decode_nodes
    reader, source, envelope = prepared_five()
    reader.payloads.pop(5)
    writes = []
    nodes = []
    release_rows = []
    original_execute = reader.execute

    def execute(query):
        if query.startswith("INSERT "):
            rows = [json.loads(line) for line in query.split("\n")[1:]]
            assert all(row["strategy_number"] == 5 for row in rows)
            writes.append("release" if "configuration_release" in query else "nodes")
            if writes[-1] == "release":
                assert nodes
                release_rows.extend(rows)
            else:
                assert not release_rows
                nodes.extend({key: value for key, value in row.items()
                              if key not in {"strategy_number", "release_attempt_id"}} for row in rows)
            return ""
        if "strategy_number=5" in query:
            rows = release_rows if "configuration_release" in query else nodes
            return "\n".join(json.dumps(row) for row in rows)
        return original_execute(query)

    class Keeper:
        connected = True
        def create(self, path, *_args, **_kwargs):
            assert path.endswith("/5")
        def delete(self, path):
            assert path.endswith("/5")

    monkeypatch.setattr(reader, "execute", execute)
    monkeypatch.setattr(publisher, "verify_tables", lambda _client: None)
    token = publisher.publish_configuration(reader, Keeper(), envelope)
    assert writes[-1] == "release" and writes.count("release") == 1
    assert decode_nodes(nodes) == envelope["payload"]
    count = len(writes)
    assert publisher.publish_configuration(reader, Keeper(), envelope) == token
    assert len(writes) == count
    assert certify_numbered_configuration(reader, 4).token == source.token


def test_fifth_rejects_reenabled_add_policy():
    _, _, fifth = prepared_five()
    fifth["payload"]["strategy"]["numbered_release"]["add_policy"]["allows_adds"] = True
    with pytest.raises(ValueError, match="sealed contract"):
        verify_strategy_five_manifest(fifth["payload"]["strategy"])


def test_fifth_rejects_reenabled_completed_30s_trailing():
    _, _, fifth = prepared_five()
    fifth["payload"]["strategy"]["numbered_release"]["trailing_policy"]["completed_30s_low_trailing"] = True
    with pytest.raises(ValueError, match="sealed contract"):
        verify_strategy_five_manifest(fifth["payload"]["strategy"])
