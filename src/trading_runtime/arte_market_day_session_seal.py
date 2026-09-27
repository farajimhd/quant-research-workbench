"""Producer-owned, normalized per-session market certificate seal.

This is not a Backtest shortcut by itself. A producer must derive the row from
an already verified global V5 certificate, publish it to the SSD table, check
exact readback, and only then create the matching Keeper receipt. Backtest may
use it only with a stable active-part inventory and full selected-day readback.
"""
from __future__ import annotations

from datetime import date
from hashlib import sha256
import json
import re
from typing import Any, Mapping, Sequence

from research.mlops.clickhouse import insert_json_each_row

from src.trading_runtime.arte_journal_schema import TableContract, storage_preflight
from src.trading_runtime.arte_market_day_certification import (
    MarketDayCertificate, _COLUMNS, _TABLE_BY_NAME, _typed_readback_row, family_hash,
)
from src.trading_runtime.arte_market_day_keeper import (
    BuildAttestation, MarketDayKeeperAuthority, MarketDayKeeperReader, _base,
    require_attested_inventory,
)
from src.trading_runtime.arte_market_day_source_plan import _digest
from src.trading_runtime.journal_contract import canonical_json
from src.trading_runtime.keeper_ownership import KeeperUnavailable


SESSION_SEAL = TableContract(
    "market_day_session_seal_v1",
    (
        ("build_id", "String"), ("session_date", "Date"),
        ("definition_hash", "FixedString(64)"),
        ("source_plan_hash", "FixedString(64)"),
        ("root_proof_hash", "FixedString(64)"),
        ("scope_count", "UInt32"), ("scope_hash", "FixedString(64)"),
        ("stage_count", "UInt32"), ("stage_hash", "FixedString(64)"),
        ("seed_count", "UInt32"), ("seed_hash", "FixedString(64)"),
        ("source_unit_count", "UInt32"),
        ("source_unit_hash", "FixedString(64)"),
        ("content_hash", "FixedString(64)"),
    ),
    "toYYYYMM(session_date)", "build_id,session_date",
)
_FAMILIES = (
    "market_day_planned_scope_v1", "market_day_stage_certificate_v1",
    "market_day_seed_v1", "market_day_source_unit_v1",
)
_HEX = re.compile(r"[0-9a-f]{64}\Z")


def session_seal_receipt(row: Mapping[str, Any]) -> bytes:
    """Small Keeper receipt; tabular facts remain in ClickHouse."""
    return ("1\n" + "\n".join(str(row[key]) for key in (
        "build_id", "session_date", "content_hash", "root_proof_hash",
    ))).encode("ascii")


def _validated_family(
    rows: Sequence[Mapping[str, Any]], *, build_id: str, session: str,
    date_key: str,
) -> list[dict[str, Any]]:
    result = [dict(row) for row in rows]
    if any(row.get("build_id") != build_id or row.get(date_key) != session
           for row in result):
        raise ValueError("Session seal family differs from requested build/day")
    return result


def prepare_session_seal(
    certificate: MarketDayCertificate, proof: BuildAttestation, *,
    session_date: date,
    families: Mapping[str, Sequence[Mapping[str, Any]]] | None = None,
) -> dict[str, Any]:
    """Derive one day from a fully verified global certificate, never raw events."""
    day = session_date.isoformat()
    if families is None and isinstance(certificate, MarketDayCertificate):
        families = (certificate.session_families or {}).get(day)
    if (not isinstance(certificate, MarketDayCertificate)
            or not isinstance(proof, BuildAttestation)
            or certificate.build_id != proof.build_id
            or certificate.definition_hash != proof.definition_hash
            or _digest(certificate.source_plan) != proof.source_plan_hash
            or certificate.session_hashes is None
            or families is None
            or set(families) != set(_FAMILIES)):
        raise ValueError("Session seal lacks a verified global V5 root")
    require_attested_inventory(proof, dict(certificate.fence))
    scoped = {
        name: _validated_family(families[name], build_id=proof.build_id,
                                session=day,
                                date_key="source_date" if name.endswith("source_unit_v1")
                                else "session_date")
        for name in _FAMILIES
    }
    scopes, stages, seeds, units = (scoped[name] for name in _FAMILIES)
    expected_hashes = certificate.session_hashes.get(day)
    if (expected_hashes is None
            or {name: (len(rows), family_hash(rows)) for name, rows in scoped.items()}
            != expected_hashes):
        raise ValueError("Session seal facts differ from globally verified rows")
    expected_scopes = {key for key in certificate.scopes if key[0] == day}
    expected_stages = {key for key in certificate.stages if key[0] == day}
    scope_keys = [(row["session_date"], row["ticker"]) for row in scopes]
    stage_keys = [(row["session_date"], row["ticker"], row["stage"],
                   row["attempt_id"], int(row["output_rows"]), row["output_hash"])
                  for row in stages]
    seed_keys = [(row["session_date"], row["ticker"]) for row in seeds]
    planned_units = {
        (row["source_date"], row["ticker"]): (index, row)
        for index, row in enumerate(certificate.source_plan["units"])
        if row["source_date"] == day
    }
    observed_units = {(row["source_date"], row["ticker"]): row for row in units}
    if (not expected_scopes or set(scope_keys) != expected_scopes
            or len(scope_keys) != len(expected_scopes)
            or set(stage_keys) != expected_stages
            or len(stage_keys) != len(expected_stages)
            or set(seed_keys) != expected_scopes
            or len(seed_keys) != len(expected_scopes)
            or set(observed_units) != expected_scopes
            or len(units) != len(expected_scopes)
            or set(planned_units) != expected_scopes):
        raise ValueError("Session seal scope differs from global certificate")
    for key, row in observed_units.items():
        ordinal, expected = planned_units[key]
        if (int(row["ordinal"]) != ordinal
                or {name: row[name] for name in expected} != expected):
            raise ValueError("Session source unit differs from global source plan")
    row: dict[str, Any] = {
        "build_id": proof.build_id, "session_date": day,
        "definition_hash": proof.definition_hash,
        "source_plan_hash": proof.source_plan_hash,
        "root_proof_hash": sha256(proof.wire()).hexdigest(),
    }
    for name, family in zip(("scope", "stage", "seed", "source_unit"),
                            (scopes, stages, seeds, units)):
        row[f"{name}_count"] = len(family)
        row[f"{name}_hash"] = family_hash(family)
    row["content_hash"] = sha256(canonical_json(row).encode()).hexdigest()
    return row


def verify_session_seal_row(
    row: Mapping[str, Any], proof: BuildAttestation, receipt: bytes,
) -> None:
    """Verify exact one-row CH/Keeper/root identity before selected-day reads."""
    expected = {column for column, _ in SESSION_SEAL.columns}
    content = {key: value for key, value in row.items() if key != "content_hash"}
    if (set(row) != expected or row["build_id"] != proof.build_id
            or row["definition_hash"] != proof.definition_hash
            or row["source_plan_hash"] != proof.source_plan_hash
            or row["root_proof_hash"] != sha256(proof.wire()).hexdigest()
            or not isinstance(row["session_date"], str)
            or date.fromisoformat(row["session_date"]).isoformat() != row["session_date"]
            or any(type(row[f"{name}_count"]) is not int
                   or row[f"{name}_count"] < 1
                   or not _HEX.fullmatch(str(row[f"{name}_hash"]))
                   for name in ("scope", "stage", "seed", "source_unit"))
            or not _HEX.fullmatch(str(row["content_hash"]))
            or row["content_hash"] != sha256(canonical_json(content).encode()).hexdigest()
            or receipt != session_seal_receipt(row)):
        raise RuntimeError("Session seal lacks exact typed ClickHouse/Keeper proof")


def _read_one(client: Any, *, build_id: str, day: str) -> dict[str, Any] | None:
    if (not re.fullmatch(r"[0-9a-f]{64}(?:-[0-9a-f]{12})?", build_id)
            or date.fromisoformat(day).isoformat() != day):
        raise ValueError("Session seal read identity is invalid")
    columns = ",".join(name for name, _ in SESSION_SEAL.columns)
    rows = [json.loads(line) for line in client.execute(
        f"SELECT {columns} FROM arte.{SESSION_SEAL.name} "
        f"WHERE build_id='{build_id}' AND session_date=toDate('{day}') "
        "LIMIT 2 FORMAT JSONEachRow"
    ).splitlines() if line.strip()]
    if len(rows) > 1:
        raise RuntimeError("Session seal has duplicate ClickHouse rows")
    return rows[0] if rows else None


def _receipt_path(build_id: str, day: str) -> str:
    return f"{_base(build_id)}/session-seal/{day}"


class MarketDaySessionSealClient:
    """Producer transport allowed to INSERT only the typed session-seal row."""

    def __init__(self, http_client: Any) -> None:
        self.http_client = http_client

    def close(self) -> None:
        self.http_client.close()

    def execute(self, sql: str) -> str:
        if not sql.lstrip().upper().startswith("SELECT "):
            raise ValueError("Session-seal read transport is SELECT-only")
        return self.http_client.execute(sql)

    def insert_row(self, row: Mapping[str, Any]) -> None:
        if set(row) != {name for name, _ in SESSION_SEAL.columns}:
            raise ValueError("Session-seal INSERT row differs from typed contract")
        insert_json_each_row(
            self.http_client, "arte", SESSION_SEAL.name,
            [name for name, _ in SESSION_SEAL.columns], [dict(row)],
        )


def inspect_session_seal(
    client: Any, keeper: MarketDayKeeperReader,
    proof: BuildAttestation, row: Mapping[str, Any],
) -> str:
    """Return absent, row_unsealed, or committed; conflicting state fails."""
    receipt = session_seal_receipt(row)
    verify_session_seal_row(row, proof, receipt)
    stored = _read_one(client, build_id=proof.build_id, day=row["session_date"])
    try:
        observed, _ = keeper.client.get(_receipt_path(proof.build_id, row["session_date"]))
    except Exception as exc:
        if type(exc).__name__ == "NoNodeError":
            observed = None
        else:
            raise KeeperUnavailable("Session-seal Keeper status is uncertain") from exc
    if stored is None:
        if observed is not None:
            raise RuntimeError("Session seal has Keeper receipt without ClickHouse row")
        return "absent"
    if stored != dict(row):
        raise RuntimeError("Session seal has a conflicting ClickHouse row")
    if observed is None:
        return "row_unsealed"
    if observed != receipt:
        raise KeeperUnavailable("Session seal has a conflicting Keeper receipt")
    return "committed"


def publish_session_seal(
    client: MarketDaySessionSealClient, keeper: MarketDayKeeperAuthority,
    proof: BuildAttestation, row: Mapping[str, Any],
) -> None:
    """Publish one exact row, read it back, then seal via Keeper; reruns are safe."""
    if (not isinstance(client, MarketDaySessionSealClient)
            or not isinstance(keeper, MarketDayKeeperAuthority)
            or keeper.load(proof.build_id) != proof):
        raise TypeError("Session-seal publisher requires restricted, attested authorities")
    receipt = session_seal_receipt(row)
    verify_session_seal_row(row, proof, receipt)
    storage_preflight(client, tables=(SESSION_SEAL,))
    stored = _read_one(client, build_id=proof.build_id, day=row["session_date"])
    if stored is None:
        client.insert_row(row)
        stored = _read_one(client, build_id=proof.build_id, day=row["session_date"])
    if stored != dict(row):
        raise RuntimeError("Session-seal ClickHouse readback differs from prepared row")
    path = _receipt_path(proof.build_id, row["session_date"])
    keeper.client.ensure_path(path.rsplit("/", 1)[0])
    try:
        keeper.client.create(path, receipt)
    except Exception as exc:
        if type(exc).__name__ != "NodeExistsError":
            # An ambiguous create is resolved by exact readback below. Never
            # submit another ClickHouse row to repair a Keeper uncertainty.
            pass
    try:
        observed, _ = keeper.client.get(path)
    except Exception as exc:
        raise KeeperUnavailable("Session-seal Keeper receipt is not readable") from exc
    if observed != receipt:
        raise KeeperUnavailable("Session-seal Keeper receipt conflicts with typed row")


def load_session_seal(
    client: Any, keeper: MarketDayKeeperReader, proof: BuildAttestation,
    session_date: date,
) -> dict[str, Any]:
    """Read one exact sealed row; selected-day family checks follow separately."""
    storage_preflight(client, tables=(SESSION_SEAL,))
    day = session_date.isoformat()
    row = _read_one(client, build_id=proof.build_id, day=day)
    if row is None:
        raise RuntimeError("Session seal is not published")
    try:
        receipt, _ = keeper.client.get(_receipt_path(proof.build_id, day))
    except Exception as exc:
        raise KeeperUnavailable("Session-seal Keeper receipt is absent") from exc
    verify_session_seal_row(row, proof, receipt)
    return row


def read_sealed_session_families(
    client: Any, proof: BuildAttestation, seal: Mapping[str, Any],
    receipt: bytes,
) -> dict[str, tuple[dict[str, Any], ...]]:
    """Read only one sealed session's normalized source/certificate facts."""
    verify_session_seal_row(seal, proof, receipt)
    storage_preflight(client, tables=tuple(_TABLE_BY_NAME[name] for name in _FAMILIES))
    day = str(seal["session_date"])
    result: dict[str, tuple[dict[str, Any], ...]] = {}
    for name, label in zip(_FAMILIES,
                           ("scope", "stage", "seed", "source_unit")):
        date_key = "source_date" if label == "source_unit" else "session_date"
        columns = ",".join(_COLUMNS[name])
        rows = tuple(_typed_readback_row(name, json.loads(line)) for line in
                     client.execute(
                         f"SELECT {columns} FROM arte.{name} "
                         f"WHERE build_id='{proof.build_id}' "
                         f"AND {date_key}=toDate('{day}') FORMAT JSONEachRow"
                     ).splitlines() if line.strip())
        _validated_family(rows, build_id=proof.build_id, session=day,
                          date_key=date_key)
        if (len(rows) != int(seal[f"{label}_count"])
                or family_hash(rows) != seal[f"{label}_hash"]):
            raise RuntimeError(f"Sealed session {label} rows differ from producer proof")
        result[name] = rows
    return result
