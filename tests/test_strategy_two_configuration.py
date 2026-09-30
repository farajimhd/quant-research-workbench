"""Numbered publication isolation, complete seal, and inherited behavior checks."""
from copy import deepcopy
from hashlib import sha256
import json
import re

import pytest

from pipelines.strategy_one.configuration_publisher import _verified_numbered_envelope
from pipelines.strategy_one.strategy_two_configuration import compile_strategy_two_configuration
from src.backend.backtest_strategy_one_configuration import (
    certify_numbered_configuration, certify_strategy_one_configuration,
    is_numbered_fixed_configuration, selected_numbered_revision,
)
from src.trading_runtime.journal_contract import canonical_json
from src.trading_runtime.strategy_one_configuration_tree import encode_nodes, node_hash
from src.trading_runtime.strategy_two_release import verify_strategy_two_manifest


ONE = {"strategy": {"strategy_id": "early-squeeze-strategy", "strategy_number": 1,
                    "revision": 1, "execution_interval": "100ms",
                    "parameters": {"execution": {"tick_size": 0.01}, "sizing": {"budget": 1000}}},
       "strategy_profile": {"lifecycle": {"trading_behavior": {"eligible_sessions": ["premarket"]}}},
       "accounts": {"bindings": [{"modes": ["backtest", "live"]}]},
       "run_plan": {"run_plan_id": "balanced-replay"}, "assignments": []}


class Reader:
    def __init__(self):
        self.payloads = {1: deepcopy(ONE)}
        self.sources = {1: ("candidate-350", "a" * 64)}
        self.queries = []

    def execute(self, query):
        assert query.startswith("SELECT ")
        self.queries.append(query)
        number = int(re.search(r"strategy_number=(\d+)", query)[1])
        payload = self.payloads[number]
        nodes = encode_nodes(payload)
        source, digest = self.sources[number]
        rows = [{"release_attempt_id": f"00000000-0000-0000-0000-{number:012d}",
                 "strategy_id": "early-squeeze-strategy", "source_candidate_id": source,
                 "source_candidate_hash": digest,
                 "payload_hash": sha256(canonical_json(payload).encode()).hexdigest(),
                 "node_count": len(nodes), "node_hash": node_hash(nodes)}] if "configuration_release" in query else nodes
        return "\n".join(json.dumps(row) for row in rows)


def prepared():
    reader = Reader()
    source = certify_strategy_one_configuration(reader)
    envelope = compile_strategy_two_configuration(source, approved_code_commit="b" * 40,
        approved_code_fingerprint="c" * 64, approval_reference="user-approved-strategy-2-research")
    reader.payloads[2] = envelope["payload"]
    reader.sources[2] = (envelope["source_candidate_id"], envelope["source_candidate_hash"])
    return reader, source, envelope


def test_second_release_preserves_first_and_roundtrips_sealed_typed_tree():
    reader, source, envelope = prepared()
    _verified_numbered_envelope(envelope)
    result = certify_numbered_configuration(reader, 2)
    assert result.payload == envelope["payload"]
    assert result.revision()["revision"] == 2
    assert source.payload == ONE
    assert certify_strategy_one_configuration(reader).token == source.token
    assert result.source_candidate_id != source.source_candidate_id
    assert selected_numbered_revision(client=reader, revision_id=result.revision()["revision_id"])["content_hash"] == result.payload_hash
    assert is_numbered_fixed_configuration({"strategy": result.payload["strategy"]})
    assert result.payload["accounts"]["bindings"][0]["modes"] == ["backtest"]


@pytest.mark.parametrize("field,value", [("publication_mode", "live"), ("approved_digest", "0" * 64),
                                       ("approved_code_commit", "uncommitted"), ("approval_reference", "")])
def test_forged_manifest_rejected(field, value):
    _, _, envelope = prepared()
    strategy = envelope["payload"]["strategy"]
    strategy["numbered_release"][field] = value
    with pytest.raises(ValueError):
        verify_strategy_two_manifest(strategy)


def test_resealed_hidden_inherited_parameter_override_is_rejected_by_reader():
    reader, _, _ = prepared()
    reader.payloads[2]["strategy"]["parameters"]["sizing"]["budget"] = 9999
    # Reader fake recomputes valid node/payload hashes; inheritance still rejects it.
    with pytest.raises(RuntimeError, match="certified inheritance"):
        certify_numbered_configuration(reader, 2)


def test_unknown_unsealed_and_wrong_selected_identity_rejected():
    reader, _, _ = prepared()
    for number in (0, 4, True, "2"):
        with pytest.raises(ValueError):
            is_numbered_fixed_configuration({"strategy": {"strategy_number": number}})
    with pytest.raises(ValueError):
        is_numbered_fixed_configuration({"strategy": {"strategy_number": 2}})
    with pytest.raises(ValueError):
        selected_numbered_revision(client=reader, revision_id="strategy-one-2:00000000-0000-0000-0000-000000000099")
    assert not is_numbered_fixed_configuration({"strategy": {"strategy_id": "legacy"}})


def test_second_release_pins_completed_input_clocks_and_rejects_assignment_overrides():
    from src.backend.backtest_market_data import compile_required_resolutions, ExecutionInterval
    from src.backend.trading_configuration_service import merged_assignment_parameters
    _, _, envelope = prepared()
    payload = envelope["payload"]
    assert compile_required_resolutions(payload, ExecutionInterval.parse("100ms")) == (100, 1000, 5000, 10000, 30000)
    assert merged_assignment_parameters(payload, {}) == payload["strategy"]["parameters"]
    with pytest.raises(ValueError, match="cannot override"):
        merged_assignment_parameters(payload, {"parameters": {"sizing": {"budget": 9999}}})


def test_publication_is_coverage_last_and_idempotent_without_touching_number_one(monkeypatch):
    from pipelines.strategy_one import configuration_publisher as publisher
    from src.trading_runtime.strategy_one_configuration_tree import decode_nodes
    reader, source, envelope = prepared()
    reader.payloads.pop(2)
    writes = []
    nodes = []
    release_rows = []
    original_execute = reader.execute

    def execute(query):
        if query.startswith("INSERT "):
            rows = [json.loads(line) for line in query.split("\n")[1:]]
            assert all(row["strategy_number"] == 2 for row in rows)
            writes.append("release" if "configuration_release" in query else "nodes")
            if writes[-1] == "release":
                assert nodes
                release_rows.extend(rows)
            else:
                assert not release_rows
                nodes.extend({key: value for key, value in row.items()
                              if key not in {"strategy_number", "release_attempt_id"}} for row in rows)
            return ""
        if "strategy_number=2" in query:
            rows = release_rows if "configuration_release" in query else nodes
            return "\n".join(json.dumps(row) for row in rows)
        return original_execute(query)

    class Keeper:
        connected = True
        def create(self, path, *_args, **_kwargs):
            assert path.endswith("/2")
        def delete(self, path):
            assert path.endswith("/2")

    monkeypatch.setattr(reader, "execute", execute)
    monkeypatch.setattr(publisher, "verify_tables", lambda _client: None)
    token = publisher.publish_configuration(reader, Keeper(), envelope)
    assert writes[-1] == "release" and writes.count("release") == 1
    assert decode_nodes(nodes) == envelope["payload"]
    count = len(writes)
    assert publisher.publish_configuration(reader, Keeper(), envelope) == token
    assert len(writes) == count
    assert certify_strategy_one_configuration(reader).token == source.token
