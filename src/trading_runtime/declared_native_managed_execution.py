"""Mandatory management request composition over complete execution inputs.

The original execution@3 stays intact. This @4 envelope seals the management
request consumed by the own transport without changing inherited economics.
Configuration preparation alone never opens installed admission.
"""
from dataclasses import dataclass, replace
from hashlib import sha256

from . import declared_native_execution as execution
from .declared_native_fixed_capabilities import _json, _keys
from .declared_native_management_request import (
    DeclaredManagementRequestPolicy, parse_declared_management_request,
)
from .strategy_one_configuration_tree import encode_nodes, decode_nodes, node_hash

INPUT_CONTRACT = "declared-native-fixed-managed-execution-input@4"
RULE_CONTRACT = "declared-native-fixed-managed-execution-rule@4"


@dataclass(frozen=True, slots=True)
class DeclaredNativeManagedExecutionSpec:
    execution: execution.DeclaredNativeExecutionSpec
    management_request: DeclaredManagementRequestPolicy

    def __post_init__(self):
        if (type(self.execution) is not execution.DeclaredNativeExecutionSpec
                or type(self.management_request) is not DeclaredManagementRequestPolicy):
            raise ValueError("Managed execution needs exact complete declarations")
        self.execution.__post_init__()
        self.management_request.__post_init__()

    def payload(self):
        self.__post_init__()
        return dict(schema_version=4, execution=self.execution.payload(),
                    management_request=self.management_request.payload())

    def release(self):
        self.__post_init__()
        core = self.execution.release()
        additions = (INPUT_CONTRACT, self.management_request.policy_id)
        if any(value in core.input_contracts for value in additions) or RULE_CONTRACT in core.rule_set_contracts:
            raise ValueError("Managed execution contracts overlap inherited declarations")
        release = replace(core, input_contracts=(*core.input_contracts, *additions),
            rule_set_contracts=(*core.rule_set_contracts, RULE_CONTRACT), approved_digest="",
            behavior_specification=core.behavior_specification +
                " Management commands carry complete replayable producer inputs and prior state; "
                "the complete inherited exit/protection request is explicitly sealed. "
                "Installed source, durability and financial authority remain required.")
        release = replace(release, approved_digest=release.digest())
        release.verify()
        return release


def parse_declared_native_managed_execution(value):
    _keys(value, {"schema_version", "execution", "management_request"}, "managed execution")
    if type(value['schema_version']) is not int or value['schema_version'] != 4:
        raise ValueError("Unsupported managed execution schema")
    result = DeclaredNativeManagedExecutionSpec(
        execution.parse_declared_native_execution(value['execution']),
        parse_declared_management_request(value['management_request']))
    if _json(result.payload()) != _json(value):
        raise ValueError("Managed execution complete payload differs")
    result.release()
    return result


def prepare_declared_managed_configuration(client, spec, *, approval):
    if type(spec) is not DeclaredNativeManagedExecutionSpec:
        raise ValueError("Managed configuration requires its exact specification")
    spec = parse_declared_native_managed_execution(spec.payload())
    envelope = execution.prepare_declared_execution_configuration(client, spec.execution, approval=approval)
    payload = envelope['payload']
    manifest = payload['strategy']['numbered_release']
    manifest.pop('manifest_hash')
    release = spec.release()
    manifest.update(contract=release.canonical_payload(), approved_digest=release.approved_digest,
        native_fixed_managed_execution=spec.payload(),
        declared_native_management_request=spec.management_request.payload())
    manifest['manifest_hash'] = sha256(_json(manifest).encode()).hexdigest()
    payload['strategy_profile']['description'] = release.behavior_specification
    nodes = encode_nodes(payload)
    return dict(source_candidate_id='declared-native-managed-execution-from:' + spec.execution.candidate.base.parent.revision_id,
        source_candidate_hash=envelope['source_candidate_hash'],
        payload_hash=sha256(_json(payload).encode()).hexdigest(), node_hash=node_hash(nodes),
        node_count=len(nodes), nodes=list(nodes), payload=payload)


def verify_declared_managed_configuration(client, spec, envelope, *, approval):
    expected = prepare_declared_managed_configuration(client, spec, approval=approval)
    _keys(envelope, set(expected), 'managed execution envelope')
    _json(envelope)
    if (type(envelope['node_count']) is not int or type(envelope['nodes']) is not list
            or len(envelope['nodes']) != envelope['node_count']
            or node_hash(envelope['nodes']) != envelope['node_hash']
            or _json(decode_nodes(envelope['nodes'])) != _json(envelope['payload'])
            or sha256(_json(envelope['payload']).encode()).hexdigest() != envelope['payload_hash']
            or _json(envelope) != _json(expected)):
        raise ValueError("Managed execution differs from full independent inheritance")
    return spec.release()


def declared_managed_spec_from_configuration(configuration):
    """Pure duplicate-field extraction; full independent verification is separate."""
    if type(configuration) is not dict or type(configuration.get('strategy')) is not dict:
        raise ValueError("Managed execution configuration is malformed")
    strategy = configuration['strategy']
    manifest = strategy.get('numbered_release')
    if type(manifest) is not dict:
        raise ValueError("Managed execution manifest is missing")
    spec = parse_declared_native_managed_execution(manifest.get('native_fixed_managed_execution'))
    core = spec.execution
    duplicates = dict(native_fixed_candidate=core.candidate.payload(), native_fixed_execution=core.payload(),
        declared_native_entry_source=core.entry_source.payload(),
        declared_native_assignment_source=core.assignments.payload(),
        declared_native_entry_request=core.entry_request.payload(),
        declared_native_management_request=spec.management_request.payload(),
        contract=spec.release().canonical_payload(), approved_digest=spec.release().approved_digest)
    identity = core.candidate.base.identity
    if (any(type(strategy.get(key)) is not type(value) or strategy.get(key) != value
            for key, value in identity.payload().items())
            or any(_json(manifest.get(key)) != _json(value) for key, value in duplicates.items())
            or manifest.get('manifest_hash') != sha256(_json({k:v for k,v in manifest.items()
                                                            if k != 'manifest_hash'}).encode()).hexdigest()):
        raise ValueError("Managed execution identity or duplicated declarations differ")
    return spec
