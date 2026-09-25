"""Inactive v55 approved-release accounts section, with named typed rows only.

This section is not an approved-release proof. All other release sections and
the approval head must be recovered before live admission can use it.
"""
from __future__ import annotations

from hashlib import sha256
from typing import Any, Mapping, Sequence

from src.trading_runtime.arte_journal_schema import TableContract
from src.trading_runtime.journal_contract import canonical_json


PARENT = TableContract(
    "live_approved_accounts_section_typed_v1",
    (("schema_version", "UInt16"), ("configuration_revision_id", "String"),
     ("configuration_schema_version", "UInt16"), ("binding_count", "UInt32"),
     ("binding_hash", "FixedString(64)"), ("mode_count", "UInt32"),
     ("mode_hash", "FixedString(64)"), ("content_hash", "FixedString(64)")),
    "cityHash64(configuration_revision_id) % 16", "configuration_revision_id",
)
BINDING = TableContract(
    "live_approved_account_binding_typed_v1",
    (("schema_version", "UInt16"), ("configuration_revision_id", "String"),
     ("ordinal", "UInt32"), ("account_key", "String"), ("name", "String"),
     ("account_class", "String"), ("base_currency", "String"),
     ("session_key", "String"), ("portfolio_policy_id", "String"),
     ("enabled", "Bool"), ("source_account_id_present", "Bool"),
     ("source_account_id", "Nullable(String)"),
     ("source_account_env_present", "Bool"),
     ("source_account_env", "Nullable(String)"),
     ("system_managed_present", "Bool"),
     ("system_managed", "Nullable(Bool)"),
     ("content_hash", "FixedString(64)")),
    "cityHash64(configuration_revision_id) % 16", "configuration_revision_id, ordinal",
)
MODE = TableContract(
    "live_approved_account_mode_typed_v1",
    (("schema_version", "UInt16"), ("configuration_revision_id", "String"),
     ("binding_ordinal", "UInt32"), ("mode_ordinal", "UInt16"),
     ("mode", "String"), ("content_hash", "FixedString(64)")),
    "cityHash64(configuration_revision_id) % 16",
    "configuration_revision_id, binding_ordinal, mode_ordinal",
)
TABLES = (PARENT, BINDING, MODE)
COMMON = ("account_key", "name", "account_class", "base_currency",
          "session_key", "portfolio_policy_id", "enabled", "modes")
OPTIONAL = ("source_account_id", "source_account_env", "system_managed")


def _hash(value: Any) -> str:
    return sha256(canonical_json(value).encode()).hexdigest()


def _seal(row: dict[str, Any]) -> dict[str, Any]:
    return {**row, "content_hash": _hash(row)}


def project_accounts_section(
    section: Mapping[str, Any], *, configuration_revision_id: str,
) -> tuple[dict[str, Any], tuple[dict[str, Any], ...], tuple[dict[str, Any], ...]]:
    if (type(configuration_revision_id) is not str or not configuration_revision_id
            or any(char in configuration_revision_id for char in "\r\n\x00")
            or not isinstance(section, Mapping) or set(section) != {"bindings"}
            or type(section["bindings"]) is not list
            or not 1 <= len(section["bindings"]) <= 100_000):
        raise ValueError("approved accounts section scope or shape is invalid")
    bindings, modes = [], []
    seen = set()
    for ordinal, binding in enumerate(section["bindings"]):
        if (not isinstance(binding, Mapping)
                or not set(COMMON) <= set(binding)
                or set(binding) - set(COMMON) - set(OPTIONAL)):
            raise ValueError("approved account binding has unmodeled fields")
        for field in COMMON[:-2]:
            if type(binding[field]) is not str or not binding[field]:
                raise ValueError("approved account binding scalar is invalid")
        if type(binding["enabled"]) is not bool:
            raise ValueError("approved account enabled is invalid")
        key = binding["account_key"]
        if key in seen:
            raise ValueError("approved account binding is duplicate")
        seen.add(key)
        raw_modes = binding["modes"]
        if (type(raw_modes) is not list or not 1 <= len(raw_modes) <= 65535
                or any(type(mode) is not str or not mode for mode in raw_modes)
                or len(set(raw_modes)) != len(raw_modes)):
            raise ValueError("approved account modes are invalid")
        for field in OPTIONAL[:2]:
            if field in binding and type(binding[field]) is not str:
                raise ValueError("approved account optional scalar is invalid")
        if "system_managed" in binding and type(binding["system_managed"]) is not bool:
            raise ValueError("approved account managed flag is invalid")
        row = dict(schema_version=1, configuration_revision_id=configuration_revision_id,
                   ordinal=ordinal,
                   **{field: binding[field] for field in COMMON[:-2]},
                   enabled=binding["enabled"],
                   **{f"{field}_present": field in binding for field in OPTIONAL},
                   **{field: binding.get(field) for field in OPTIONAL})
        bindings.append(_seal(row))
        for mode_ordinal, mode in enumerate(raw_modes):
            modes.append(_seal(dict(
                schema_version=1, configuration_revision_id=configuration_revision_id,
                binding_ordinal=ordinal, mode_ordinal=mode_ordinal, mode=mode)))
    parent = _seal(dict(
        schema_version=1, configuration_revision_id=configuration_revision_id,
        configuration_schema_version=55, binding_count=len(bindings),
        binding_hash=_hash(bindings), mode_count=len(modes), mode_hash=_hash(modes)))
    return parent, tuple(bindings), tuple(modes)


def restore_accounts_section(
    parent: Mapping[str, Any], bindings: Sequence[Mapping[str, Any]],
    modes: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    if (set(parent) != {name for name, _ in PARENT.columns}
            or parent.get("schema_version") != 1
            or parent.get("configuration_schema_version") != 55
            or [row.get("ordinal") for row in bindings] != list(range(len(bindings)))
            or any(set(row) != {name for name, _ in BINDING.columns} for row in bindings)
            or any(set(row) != {name for name, _ in MODE.columns} for row in modes)):
        raise ValueError("approved accounts typed columns or order differ")
    reconstructed = []
    mode_index = 0
    for ordinal, row in enumerate(bindings):
        binding = {field: row[field] for field in COMMON[:-1] if field != "modes"}
        for field in OPTIONAL:
            present = row[f"{field}_present"]
            if (type(present) is not bool or (present and row[field] is None)
                    or (not present and row[field] is not None)):
                raise ValueError("approved account optional presence differs")
            if present:
                binding[field] = row[field]
        binding["modes"] = []
        while mode_index < len(modes) and modes[mode_index].get("binding_ordinal") == ordinal:
            mode = modes[mode_index]
            if mode.get("mode_ordinal") != len(binding["modes"]):
                raise ValueError("approved account mode order differs")
            binding["modes"].append(mode["mode"])
            mode_index += 1
        reconstructed.append(binding)
    if mode_index != len(modes):
        raise ValueError("approved account mode has orphan binding")
    expected = project_accounts_section(
        {"bindings": reconstructed},
        configuration_revision_id=parent["configuration_revision_id"])
    if (dict(parent), tuple(map(dict, bindings)), tuple(map(dict, modes))) != expected:
        raise ValueError("approved accounts typed content differs")
    return {"bindings": reconstructed}
