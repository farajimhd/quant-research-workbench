"""Inherit-only native fixed capability declarations; no execution admission.

The installed factory reads the parent through the normalized configuration
reader. Pure parsing is useful for preparation, but is not publication or a
native source certificate. No existing release selects this declaration.
"""
from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
import json
import math
import re
from uuid import UUID

from . import strategy_registry

INPUT_CONTRACT = "declared-native-fixed-capabilities-input@1"
RULE_CONTRACT = "declared-native-fixed-capabilities-rule@1"
_PREFIX = "declared-native-fixed-capabilities-"
_MANIFEST_METADATA = frozenset({
    "contract", "approved_digest", "approved_code_commit",
    "approved_code_fingerprint", "approval_reference", "publication_mode",
    "source_revision_id", "source_payload_hash", "manifest_hash",
})
_FLAGS = (
    "allows_session_exit", "allows_adds", "allows_completed_30s_trailing",
    "allows_target_escalation", "caps_entry_at_reference_ask",
    "allows_followthrough_failure_exit",
)
_OPTIONAL_POLICIES = (
    "entry_momentum_growth_policy", "entry_spread_risk_policy",
    "early_original_risk_policy", "armed_profit_floor_policy",
    "drawdown_measure_policy",
)


def _json(value):
    """Canonical built-in JSON without coercion or nonfinite observations."""
    def check(item, depth=0):
        if depth > 64:
            raise ValueError("Capability JSON nesting exceeds the declared bound")
        kind = type(item)
        if item is None or kind in (str, bool):
            return
        if kind is int:
            if not -(2**63) <= item < 2**63:
                raise ValueError("Capability integer is outside Int64")
            return
        if kind is float and math.isfinite(item):
            return
        if kind is list:
            for child in item:
                check(child, depth + 1)
            return
        if kind is dict and all(type(key) is str for key in item):
            for child in item.values():
                check(child, depth + 1)
            return
        raise ValueError("Capability JSON requires exact finite built-in values")
    check(value)
    return json.dumps(value, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=True, allow_nan=False)


def _keys(value, expected, name):
    if type(value) is not dict or set(value) != set(expected):
        raise ValueError(f"Capability {name} keyset differs")


def _positive_integer(value, name):
    if type(value) is not int or not 0 < value < 2**63:
        raise ValueError(f"Capability {name} requires a positive built-in integer")


def _text(value, name):
    if type(value) is not str or not value or value.strip() != value:
        raise ValueError(f"Capability {name} requires exact nonempty text")


def _digest(value, name):
    if type(value) is not str or re.fullmatch("[0-9a-f]{64}", value) is None:
        raise ValueError(f"Capability {name} requires a canonical SHA256")


def _contracts(value, name):
    if (type(value) is not list or not value
            or any(type(item) is not str or not item or item.strip() != item
                   for item in value)
            or len(set(value)) != len(value)):
        raise ValueError(f"Capability {name} requires unique exact contracts")


@dataclass(frozen=True, slots=True)
class NativeFixedIdentity:
    strategy_number: int
    strategy_id: str
    revision: int
    execution_interval: str

    def __post_init__(self):
        _positive_integer(self.strategy_number, "strategy_number")
        _positive_integer(self.revision, "revision")
        _text(self.strategy_id, "strategy_id")
        if (type(self.execution_interval) is not str
                or re.fullmatch(r"[1-9]\d*ms", self.execution_interval) is None
                or int(self.execution_interval[:-2]) % 100):
            raise ValueError("Capability execution clock requires exact fixed 100ms multiples")

    def payload(self):
        return {name: getattr(self, name) for name in self.__dataclass_fields__}


@dataclass(frozen=True, slots=True)
class NativeFixedParentIdentity:
    strategy_number: int
    strategy_id: str
    revision_id: str
    payload_hash: str
    node_hash: str
    configuration_token: str
    approved_code_fingerprint: str
    release_digest: str

    def __post_init__(self):
        _positive_integer(self.strategy_number, "parent number")
        _text(self.strategy_id, "parent strategy id")
        _text(self.revision_id, "parent revision")
        prefix, separator, attempt = self.revision_id.partition(":")
        try:
            canonical = str(UUID(attempt))
        except (ValueError, AttributeError) as error:
            raise ValueError("Capability parent revision requires a canonical release UUID") from error
        if (not separator or prefix != f"strategy-one-{self.strategy_number}"
                or attempt != canonical):
            raise ValueError("Capability parent revision differs from its typed identity")
        for name in ("payload_hash", "node_hash", "configuration_token",
                     "approved_code_fingerprint", "release_digest"):
            _digest(getattr(self, name), "parent " + name)

    def payload(self):
        return {name: getattr(self, name) for name in self.__dataclass_fields__}


@dataclass(frozen=True, slots=True)
class DeclaredNativeFixedCapabilities:
    """Deep immutable schema value; construction alone grants no authority."""
    identity: NativeFixedIdentity
    parent: NativeFixedParentIdentity
    inherited_json: str

    def __post_init__(self):
        if type(self.identity) is not NativeFixedIdentity or type(self.parent) is not NativeFixedParentIdentity:
            raise ValueError("Capability requires exact typed own and parent identities")
        if (self.identity.strategy_number == self.parent.strategy_number
                or self.identity.strategy_id != self.parent.strategy_id):
            raise ValueError("Capability must retain a distinct own release and inherited executor family")
        if type(self.inherited_json) is not str:
            raise ValueError("Capability inheritance must be immutable canonical JSON")
        inherited = json.loads(self.inherited_json)
        if _json(inherited) != self.inherited_json:
            raise ValueError("Capability inheritance is not canonical")
        _validate_inherited(inherited, self.identity.execution_interval)
        if inherited["configuration_payload_hash"] != self.parent.payload_hash:
            raise ValueError("Capability inheritance and parent configuration hashes disagree")

    @property
    def strategy_id(self):
        return self.identity.strategy_id

    @property
    def strategy_number(self):
        return self.identity.strategy_number

    @property
    def execution_interval(self):
        return self.identity.execution_interval

    def payload(self):
        return {"schema_version": 1, "identity": self.identity.payload(),
                "parent": self.parent.payload(),
                "inherited": json.loads(self.inherited_json)}



@dataclass(frozen=True, slots=True)
class InstalledNativeFixedCapabilityAuthority:
    """Factory-produced read context; no financial execution acceptance."""
    capabilities: DeclaredNativeFixedCapabilities
    parent_configuration_json: str
    parent_source_certificate: str

    def __post_init__(self):
        if type(self.capabilities) is not DeclaredNativeFixedCapabilities or type(self.parent_configuration_json) is not str:
            raise ValueError("Capability authority requires deep immutable typed context")
        parent = json.loads(self.parent_configuration_json)
        if (_json(parent) != self.parent_configuration_json
                or sha256(self.parent_configuration_json.encode()).hexdigest() != self.capabilities.parent.payload_hash):
            raise ValueError("Capability authority parent payload differs")
        _digest(self.parent_source_certificate, "parent source certificate")

    def parent_configuration(self):
        """Return a detached copy; own identity remains on capabilities.identity."""
        return json.loads(self.parent_configuration_json)


def _validate_inherited(value, interval):
    _keys(value, {"input_contracts", "rule_set_contracts", "flags", "policies",
                  "optional_policies", "source_binding", "configuration_payload_hash"}, "inheritance")
    _contracts(value["input_contracts"], "producer inputs")
    _contracts(value["rule_set_contracts"], "rules")
    _keys(value["flags"], _FLAGS, "flags")
    if any(type(item) is not bool for item in value["flags"].values()):
        raise ValueError("Capability flags require built-in booleans")
    if type(value["policies"]) is not dict or not value["policies"]:
        raise ValueError("Capability requires complete inherited clock/missing-data/management policies")
    if set(value["policies"]) & (_MANIFEST_METADATA | {"native_fixed_capabilities"}):
        raise ValueError("Capability policy name collides with reserved manifest identity")
    _keys(value["optional_policies"], _OPTIONAL_POLICIES, "optional policies")
    if any(item is not None and type(item) is not dict for item in value["optional_policies"].values()):
        raise ValueError("Capability optional policy requires its typed JSON payload")
    _keys(value["source_binding"], {"execution_interval", "quote_source_contract",
                                 "intent_recovery_contract"}, "source binding")
    if value["source_binding"]["execution_interval"] != interval:
        raise ValueError("Capability source and executor clocks differ")
    for name in ("quote_source_contract", "intent_recovery_contract"):
        declaration = value["source_binding"][name]
        if declaration is not None:
            _text(declaration, name)
            if declaration not in value["input_contracts"]:
                raise ValueError("Capability source/recovery identity is absent from producer contracts")
    _digest(value["configuration_payload_hash"], "complete inherited configuration")
    _json(value)


def parse_native_fixed_capabilities(payload, *, expected_parent=None):
    """Strict pure parsing; optional exact inheritance comparison, no I/O."""
    _keys(payload, {"schema_version", "identity", "parent", "inherited"}, "declaration")
    if type(payload["schema_version"]) is not int or payload["schema_version"] != 1:
        raise ValueError("Unsupported native fixed capability schema")
    _keys(payload["identity"], NativeFixedIdentity.__dataclass_fields__, "identity")
    _keys(payload["parent"], NativeFixedParentIdentity.__dataclass_fields__, "parent")
    result = DeclaredNativeFixedCapabilities(
        NativeFixedIdentity(**payload["identity"]),
        NativeFixedParentIdentity(**payload["parent"]), _json(payload["inherited"]))
    if expected_parent is not None:
        if type(expected_parent) is not DeclaredNativeFixedCapabilities:
            raise ValueError("Capability parent comparison requires an exact schema value")
        if (result.parent != expected_parent.parent
                or result.inherited_json != expected_parent.inherited_json):
            raise ValueError("Capability inherited field or parent lineage drifted")
    return result


def capability_declaration_for_parent(identity, parent_configuration):
    """Prepare inherit-only data from a checked parent; not install/publication."""
    from src.backend.backtest_strategy_one_configuration import (
        CertifiedStrategyOneConfiguration, is_numbered_fixed_configuration,
    )
    from .strategy_one_configuration_tree import encode_nodes, node_hash
    if type(identity) is not NativeFixedIdentity or type(parent_configuration) is not CertifiedStrategyOneConfiguration:
        raise ValueError("Capability preparation requires exact typed identity/configuration")
    parent = parent_configuration
    if (sha256(_json(parent.payload).encode()).hexdigest() != parent.payload_hash
            or node_hash(encode_nodes(parent.payload)) != parent.node_hash
            or not is_numbered_fixed_configuration(parent.payload)):
        raise ValueError("Capability parent configuration differs from its normalized seal")
    release = strategy_registry.numbered_strategy(parent.strategy_number)
    release.verify()
    registration = strategy_registry.fixed_strategy_executor(release.executor_strategy_id, release.executor_revision)
    registration.verify()
    contract = registration.contract_factory()
    manifest = parent.payload["strategy"]["numbered_release"]
    inherited = {
        "input_contracts": list(release.input_contracts),
        "rule_set_contracts": list(release.rule_set_contracts),
        "flags": {name: getattr(contract, name, None) for name in _FLAGS},
        "policies": {key: value for key, value in manifest.items() if key not in _MANIFEST_METADATA},
        "optional_policies": {name: None if getattr(contract, name, None) is None
                              else getattr(contract, name).payload() for name in _OPTIONAL_POLICIES},
        "source_binding": {"execution_interval": release.evaluation_interval,
                           "quote_source_contract": getattr(contract, "entry_spread_risk_quote_source_contract", None),
                           "intent_recovery_contract": getattr(contract, "entry_spread_risk_intent_recovery_contract", None)},
        "configuration_payload_hash": parent.payload_hash,
    }
    # Existing policy projections may contain tuples; normalize their already
    # installed JSON projection, then strict-validate the resulting schema.
    inherited = json.loads(json.dumps(inherited, allow_nan=False))
    lineage = NativeFixedParentIdentity(
        parent.strategy_number, release.executor_strategy_id,
        parent.revision()["revision_id"], parent.payload_hash, parent.node_hash,
        parent.token, manifest["approved_code_fingerprint"], release.approved_digest)
    return DeclaredNativeFixedCapabilities(identity, lineage, _json(inherited))


def installed_declared_native_fixed_capabilities(strategy, *, configuration_client=None):
    """Resolve installed source declarations and independently read exact parent.

    This API requires a selected NumberedStrategyRelease. Original unnumbered
    Strategy1 must remain on its existing authority route; unknown/unregistered
    releases reject rather than becoming an absent-declaration fallback.
    It verifies only the strategy subtree. A future full-configuration reader
    must additionally prove consumer economics/run-plan inheritance. This does
    not register an executor, verify a future complete native source closure,
    or permit a Backtest. No caller-provided parent/verifier is used.
    """
    if type(strategy) is not dict:
        raise ValueError("Capability selected strategy must be an exact mapping")
    number = strategy.get("strategy_number")
    _positive_integer(number, "selected number")
    release = strategy_registry.numbered_strategy(number)
    release.verify()
    inputs = tuple(item for item in release.input_contracts if item.startswith(_PREFIX))
    rules = tuple(item for item in release.rule_set_contracts if item.startswith(_PREFIX))
    manifest = strategy.get("numbered_release")
    claims = type(manifest) is dict and "native_fixed_capabilities" in manifest
    if not inputs and not rules:
        if claims:
            raise ValueError("Legacy release cannot select native fixed capabilities")
        return None
    if inputs != (INPUT_CONTRACT,) or rules != (RULE_CONTRACT,):
        raise ValueError("Capability requires exact paired input and rule declarations")
    NativeFixedIdentity(number, strategy.get("strategy_id"), strategy.get("revision"), strategy.get("execution_interval"))
    if (strategy.get("strategy_id") != release.executor_strategy_id
            or strategy["revision"] != release.executor_revision
            or strategy.get("execution_interval") != release.evaluation_interval):
        raise ValueError("Capability selected identity differs from installed release")
    registration = strategy_registry.fixed_strategy_executor(release.executor_strategy_id, release.executor_revision)
    registration.verify()
    installed = registration.contract_factory()
    if type(installed) is not DeclaredNativeFixedCapabilities:
        raise ValueError("Capability needs its exact installed typed contract factory")
    if installed.identity != NativeFixedIdentity(number, release.executor_strategy_id, release.executor_revision, release.evaluation_interval):
        raise ValueError("Capability installed own identity differs")
    expected_inputs = tuple(installed.payload()["inherited"]["input_contracts"]) + (INPUT_CONTRACT,)
    expected_rules = tuple(installed.payload()["inherited"]["rule_set_contracts"]) + (RULE_CONTRACT,)
    if release.input_contracts != expected_inputs or release.rule_set_contracts != expected_rules:
        raise ValueError("Capability release changed an undeclared inherited source/rule")
    inherited_policies = installed.payload()["inherited"]["policies"]
    _keys(manifest, _MANIFEST_METADATA | set(inherited_policies) | {"native_fixed_capabilities"}, "installed manifest")
    if (manifest["contract"] != release.canonical_payload()
            or manifest["approved_digest"] != release.approved_digest
            or _json(manifest["native_fixed_capabilities"]) != _json(installed.payload())
            or any(_json(manifest[key]) != _json(value) for key, value in inherited_policies.items())
            or manifest["source_revision_id"] != installed.parent.revision_id
            or manifest["source_payload_hash"] != installed.parent.payload_hash
            or manifest["publication_mode"] != "backtest_only"):
        raise ValueError("Capability manifest differs from installed inherit-only declaration")
    _digest(manifest["approved_code_fingerprint"], "source approval fingerprint")
    if (type(manifest["approved_code_commit"]) is not str
            or re.fullmatch("[0-9a-f]{40}", manifest["approved_code_commit"]) is None
            or type(manifest["approval_reference"]) is not str
            or not 1 <= len(manifest["approval_reference"].strip()) <= 512):
        raise ValueError("Capability lacks complete source approval identity")
    if manifest["manifest_hash"] != sha256(_json({key: value for key, value in manifest.items() if key != "manifest_hash"}).encode()).hexdigest():
        raise ValueError("Capability manifest seal differs")
    if configuration_client is None:
        raise ValueError("Capability requires normalized parent configuration read authority")
    from src.backend.backtest_strategy_one_configuration import certify_numbered_configuration
    parent = certify_numbered_configuration(configuration_client, installed.parent.strategy_number)
    expected = capability_declaration_for_parent(installed.identity, parent)
    parse_native_fixed_capabilities(installed.payload(), expected_parent=expected)
    from src.backend.backtest_fixed_v4_certification import certify_numbered_fixed_v4_projection
    parent_source_certificate = certify_numbered_fixed_v4_projection(parent.strategy_number)
    return InstalledNativeFixedCapabilityAuthority(installed, _json(parent.payload), parent_source_certificate)
