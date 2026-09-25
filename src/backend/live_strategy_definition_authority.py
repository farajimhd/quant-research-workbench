"""Inactive immutable installed definition plus append-only enablement chain.

Installed code is the config authority. Custom legacy config is rejected, not
serialized into a generic field. Tables are operator-only; this module has no
writer, DDL execution, or active route switch.
"""
from __future__ import annotations

from datetime import UTC, datetime
from hashlib import sha256
from typing import Any, Mapping, Sequence

from src.trading_runtime.arte_journal_schema import TableContract
from src.trading_runtime.journal_contract import canonical_json
from src.trading_runtime.strategy_registry import typed_persistence_executor
from src.trading_runtime.taxonomy import StrategyTaxonomy


DEFINITION = TableContract(
    "live_strategy_installed_definition_typed_v1",
    (("schema_version", "UInt16"), ("definition_month", "Date"),
     ("strategy_id", "String"), ("strategy_revision", "UInt32"),
     ("name", "String"), ("implementation", "String"),
     ("executor_schema_version", "UInt16"), ("automatic", "Bool"),
     ("created_at", "DateTime64(6, 'UTC')"),
     ("installed_definition_hash", "FixedString(64)"),
     ("content_hash", "FixedString(64)")),
    "toYYYYMM(definition_month)", "strategy_id, strategy_revision",
)
ENABLE_CHANGE = TableContract(
    "live_strategy_definition_enable_change_typed_v1",
    (("schema_version", "UInt16"), ("change_month", "Date"),
     ("strategy_id", "String"), ("strategy_revision", "UInt32"),
     ("change_sequence", "UInt64"), ("enabled", "Bool"),
     ("changed_at", "DateTime64(6, 'UTC')"),
     ("definition_content_hash", "FixedString(64)"),
     ("previous_change_hash", "FixedString(64)"),
     ("content_hash", "FixedString(64)")),
    "toYYYYMM(change_month)",
    "strategy_id, strategy_revision, change_sequence",
)
TABLES = (DEFINITION, ENABLE_CHANGE)


def _digest(value: Mapping[str, Any]) -> str:
    return sha256(canonical_json(value).encode()).hexdigest()


def _sealed(value: Mapping[str, Any]) -> dict[str, Any]:
    return {**value, "content_hash": _digest(value)}


def _time(value: Any) -> str:
    try:
        parsed = value if isinstance(value, datetime) else datetime.fromisoformat(
            str(value).replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError("definition time is invalid") from exc
    if parsed.tzinfo is None:
        raise ValueError("definition time must be timezone-aware")
    return parsed.astimezone(UTC).isoformat(timespec="microseconds")


def _installed(strategy_id: str, revision: int) -> dict[str, Any]:
    executor = typed_persistence_executor(strategy_id, revision)
    definition = executor.definition()
    config = dict(definition["config"])
    config["taxonomy"] = StrategyTaxonomy.from_payload(config["taxonomy"]).payload()
    return {**definition, "config": config}


def _installed_hash(definition: Mapping[str, Any]) -> str:
    return _digest({key: definition[key] for key in (
        "strategy_id", "revision", "name", "implementation", "automatic", "config",
    )})


def project_installed_definition(saved: Mapping[str, Any]) -> dict[str, Any]:
    """Validate actual route row against one immutable installed revision."""
    if not isinstance(saved, Mapping):
        raise ValueError("installed definition row is invalid")
    strategy_id, revision = saved.get("strategy_id"), saved.get("revision")
    if type(strategy_id) is not str or type(revision) is not int:
        raise ValueError("installed definition identity is invalid")
    canonical = _installed(strategy_id, revision)
    allowed = {"strategy_id", "revision", "name", "implementation", "automatic",
               "enabled", "config", "taxonomy", "executor", "created_at"}
    if set(saved) - allowed:
        raise ValueError("installed definition has unmodeled fields")
    for key in ("strategy_id", "revision", "name", "implementation", "automatic", "config"):
        if saved.get(key) != canonical[key]:
            raise ValueError(f"installed definition {key} differs from pinned executor")
    if "taxonomy" in saved and saved["taxonomy"] != canonical["config"]["taxonomy"]:
        raise ValueError("installed definition taxonomy differs from pinned executor")
    if "executor" in saved and saved["executor"] != canonical["executor"]:
        raise ValueError("installed definition executor differs from pinned executor")
    if type(saved.get("enabled")) is not bool:
        raise ValueError("installed definition enabled must be Boolean")
    created_at = _time(saved.get("created_at"))
    return _sealed(dict(
        schema_version=1, definition_month=created_at[:10],
        strategy_id=strategy_id, strategy_revision=revision,
        name=canonical["name"], implementation=canonical["implementation"],
        executor_schema_version=canonical["executor"]["schema_version"],
        automatic=canonical["automatic"], created_at=created_at,
        installed_definition_hash=_installed_hash(canonical),
    ))


def project_enable_change(
    definition: Mapping[str, Any], *, change_sequence: int,
    enabled: bool, changed_at: Any, previous_change_hash: str | None = None,
) -> dict[str, Any]:
    """Sequence 1 is the creation decision; later decisions chain by hash."""
    if (not isinstance(definition, Mapping)
            or definition.get("content_hash") != _digest({
                key: value for key, value in definition.items() if key != "content_hash"})
            or type(change_sequence) is not int or change_sequence < 1
            or type(enabled) is not bool):
        raise ValueError("definition enablement input is invalid")
    prior = definition["content_hash"] if change_sequence == 1 else previous_change_hash
    if (type(prior) is not str or len(prior) != 64
            or any(char not in "0123456789abcdef" for char in prior)
            or (change_sequence == 1 and previous_change_hash not in (None, prior))):
        raise ValueError("definition enablement prior hash is invalid")
    timestamp = _time(changed_at)
    if timestamp < definition["created_at"]:
        raise ValueError("definition enablement precedes creation")
    if change_sequence == 1 and timestamp != definition["created_at"]:
        raise ValueError("initial definition enablement must match creation time")
    return _sealed(dict(
        schema_version=1, change_month=timestamp[:10],
        strategy_id=definition["strategy_id"],
        strategy_revision=definition["strategy_revision"],
        change_sequence=change_sequence, enabled=enabled, changed_at=timestamp,
        definition_content_hash=definition["content_hash"],
        previous_change_hash=prior,
    ))


def recover_installed_definition(
    definition_rows: Sequence[Mapping[str, Any]],
    change_rows: Sequence[Mapping[str, Any]], *,
    expected_change_sequence: int, expected_change_hash: str,
) -> dict[str, Any]:
    """Cold exact read: caller must supply a separately attested chain head."""
    if len(definition_rows) != 1:
        raise ValueError("installed definition is missing or duplicated")
    definition = dict(definition_rows[0])
    if set(definition) != {name for name, _ in DEFINITION.columns}:
        raise ValueError("installed definition columns differ")
    canonical = _installed(definition["strategy_id"], definition["strategy_revision"])
    expected = project_installed_definition({**canonical,
        "enabled": True, "created_at": definition["created_at"]})
    if definition != expected:
        raise ValueError("installed definition differs from pinned executor")
    if (type(expected_change_sequence) is not int or expected_change_sequence < 1
            or len(change_rows) != expected_change_sequence):
        raise ValueError("definition enablement chain is incomplete")
    prior = definition["content_hash"]
    enabled = None
    last_time = definition["created_at"]
    for sequence, raw in enumerate(sorted(change_rows, key=lambda row: row["change_sequence"]), 1):
        row = dict(raw)
        if set(row) != {name for name, _ in ENABLE_CHANGE.columns}:
            raise ValueError("definition enablement columns differ")
        if row["change_sequence"] != sequence or row["previous_change_hash"] != prior:
            raise ValueError("definition enablement chain is duplicate or gapped")
        expected_row = project_enable_change(
            definition, change_sequence=sequence, enabled=row["enabled"],
            changed_at=row["changed_at"], previous_change_hash=prior)
        if row != expected_row or row["changed_at"] < last_time:
            raise ValueError("definition enablement row differs")
        prior, enabled, last_time = row["content_hash"], row["enabled"], row["changed_at"]
    if prior != expected_change_hash:
        raise ValueError("definition enablement head differs")
    return {**canonical, "enabled": enabled, "created_at": definition["created_at"],
            "taxonomy": canonical["config"]["taxonomy"]}
