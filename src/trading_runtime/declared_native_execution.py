"""Complete declared execution inputs over immutable candidate configuration.

This @3 composition adds explicit entry and assignment source declarations for
every consuming candidate, including a candidate without a spread rule. It
does not register a strategy, approve current code or replace historical V4
financial authority. The complete envelope must still be independently read
and bound to its installed source before native admission.
"""
from dataclasses import dataclass, replace
from hashlib import sha256

from . import declared_native_fixed_candidate as candidate_module
from .declared_native_fixed_capabilities import _json, _keys
from .declared_native_entry_source import (
    DeclaredNativeEntrySourcePolicy, parse_declared_native_entry_source,
    INPUT_CONTRACT as ENTRY_SOURCE_INPUT,
)
from .strategy_one_configuration_tree import encode_nodes, decode_nodes, node_hash
from src.backend.backtest_declared_native_fixed_assignments import DeclaredAssignmentPolicy
from .declared_native_entry_request import DeclaredEntryRequestPolicy, parse_declared_entry_request

INPUT_CONTRACT = "declared-native-fixed-execution-input@3"
RULE_CONTRACT = "declared-native-fixed-execution-rule@3"


def _parse_assignments(value):
    expected = DeclaredAssignmentPolicy()
    _keys(value, set(expected.payload()), "declared assignment source")
    if _json(value) != _json(expected.payload()):
        raise ValueError("Declared assignment source differs from supported complete policy")
    return expected


@dataclass(frozen=True, slots=True)
class DeclaredNativeExecutionSpec:
    candidate: candidate_module.NativeFixedCandidateSpec
    entry_source: DeclaredNativeEntrySourcePolicy
    assignments: DeclaredAssignmentPolicy
    entry_request: DeclaredEntryRequestPolicy

    def __post_init__(self):
        if (type(self.candidate) is not candidate_module.NativeFixedCandidateSpec
                or type(self.entry_source) is not DeclaredNativeEntrySourcePolicy
                or type(self.assignments) is not DeclaredAssignmentPolicy
                or type(self.entry_request) is not DeclaredEntryRequestPolicy):
            raise ValueError("Declared execution needs exact candidate and source policy types")
        self.candidate.__post_init__()
        self.entry_source.__post_init__()
        self.assignments.__post_init__()
        self.entry_request.__post_init__()

    def payload(self):
        self.__post_init__()
        return dict(schema_version=3, candidate=self.candidate.payload(),
                    entry_source=self.entry_source.payload(), assignments=self.assignments.payload(),
                    entry_request=self.entry_request.payload())

    def release(self):
        self.__post_init__()
        core = self.candidate.release()
        inputs = list(core.input_contracts)
        mandatory = (INPUT_CONTRACT, ENTRY_SOURCE_INPUT, self.assignments.policy_id, self.entry_request.policy_id)
        if any(contract in inputs for contract in mandatory) or RULE_CONTRACT in core.rule_set_contracts:
            raise ValueError("Declared execution input or rule overlaps an inherited release")
        inputs.extend(mandatory)
        # A spread delta already declares the same source contract. The common
        # source declaration must still be present in the full manifest for A.
        if self.entry_source.quote_source_contract not in inputs:
            inputs.append(self.entry_source.quote_source_contract)
        behavior = (core.behavior_specification
                    + " Complete declared execution inputs bind exact quote/price producer attempts, "
                      "full source membership and horizon, completed scheduler keys, and dated "
                      "assignment identity/account/permissions/parameters and the complete inherited "
                      "capital/execution/protection request. No installed approval "
                      "or financial acceptance follows from configuration preparation.")
        release = replace(core, input_contracts=tuple(inputs),
                          rule_set_contracts=(*core.rule_set_contracts, RULE_CONTRACT),
                          behavior_specification=behavior, approved_digest="")
        release = replace(release, approved_digest=release.digest())
        release.verify()
        return release


def parse_declared_native_execution(value):
    _keys(value, {"schema_version", "candidate", "entry_source", "assignments", "entry_request"}, "declared execution")
    if type(value["schema_version"]) is not int or value["schema_version"] != 3:
        raise ValueError("Unsupported declared execution schema")
    result = DeclaredNativeExecutionSpec(
        candidate_module.parse_candidate_spec(value["candidate"]),
        parse_declared_native_entry_source(value["entry_source"]),
        _parse_assignments(value["assignments"]), parse_declared_entry_request(value["entry_request"]))
    if _json(result.payload()) != _json(value):
        raise ValueError("Declared execution scalar types or complete payload differ")
    result.release()
    return result


def _compile_execution(parent, spec, approval):
    envelope = candidate_module._compile(parent, spec.candidate, approval)
    payload = envelope["payload"]
    manifest = payload["strategy"]["numbered_release"]
    manifest.pop("manifest_hash")
    release = spec.release()
    manifest.update(contract=release.canonical_payload(), approved_digest=release.approved_digest,
                    native_fixed_execution=spec.payload(),
                    declared_native_entry_source=spec.entry_source.payload(),
                    declared_native_assignment_source=spec.assignments.payload(),
                    declared_native_entry_request=spec.entry_request.payload())
    manifest["manifest_hash"] = sha256(_json(manifest).encode()).hexdigest()
    payload["strategy_profile"]["description"] = release.behavior_specification
    nodes = encode_nodes(payload)
    return dict(source_candidate_id="declared-native-fixed-execution-from:" + spec.candidate.base.parent.revision_id,
                source_candidate_hash=spec.candidate.base.parent.payload_hash,
                payload_hash=sha256(_json(payload).encode()).hexdigest(),
                node_hash=node_hash(nodes), node_count=len(nodes), nodes=list(nodes), payload=payload)


def prepare_declared_execution_configuration(client, spec, *, approval):
    """Re-read full normalized parent and its source proof; never caller payload."""
    if type(spec) is not DeclaredNativeExecutionSpec:
        raise ValueError("Declared execution preparation requires exact typed specification")
    spec = parse_declared_native_execution(spec.payload())
    parent, _ = candidate_module._verified_parent(client, spec.candidate)
    return _compile_execution(parent, spec, approval)


def verify_declared_execution_configuration(client, spec, envelope, *, approval):
    """Recompile every field, including inherited economics and launch controls."""
    expected = prepare_declared_execution_configuration(client, spec, approval=approval)
    _keys(envelope, set(expected), "declared execution envelope")
    _json(envelope)
    if (type(envelope["node_count"]) is not int or type(envelope["nodes"]) is not list
            or len(envelope["nodes"]) != envelope["node_count"]
            or node_hash(envelope["nodes"]) != envelope["node_hash"]
            or _json(decode_nodes(envelope["nodes"])) != _json(envelope["payload"])
            or sha256(_json(envelope["payload"]).encode()).hexdigest() != envelope["payload_hash"]
            or _json(envelope) != _json(expected)):
        raise ValueError("Declared execution differs from exact full parent/source inheritance")
    return spec.release()


def declared_execution_spec_from_configuration(configuration):
    """Pure exact manifest extraction, not an installed factory or approval."""
    if type(configuration) is not dict or type(configuration.get("strategy")) is not dict:
        raise ValueError("Declared execution needs a complete configuration")
    strategy = configuration["strategy"]
    manifest = strategy.get("numbered_release")
    if type(manifest) is not dict:
        raise ValueError("Declared execution manifest is absent")
    spec = parse_declared_native_execution(manifest.get("native_fixed_execution"))
    identity = spec.candidate.base.identity
    if (any(type(strategy.get(key)) is not type(value) or strategy.get(key) != value
            for key, value in identity.payload().items())
            or _json(manifest.get("native_fixed_candidate")) != _json(spec.candidate.payload())
            or _json(manifest.get("declared_native_entry_source")) != _json(spec.entry_source.payload())
            or _json(manifest.get("declared_native_assignment_source")) != _json(spec.assignments.payload())
            or _json(manifest.get("declared_native_entry_request")) != _json(spec.entry_request.payload())
            or _json(manifest.get("contract")) != _json(spec.release().canonical_payload())
            or manifest.get("approved_digest") != spec.release().approved_digest
            or manifest.get("manifest_hash") != sha256(_json({k: v for k, v in manifest.items()
                                                             if k != "manifest_hash"}).encode()).hexdigest()):
        raise ValueError("Declared execution duplicated manifest fields or identity differ")
    return spec
