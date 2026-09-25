"""Inactive market-day certificate publisher for injected, fake-tested clients.

The active market-day producer does not call this module. The data client must
offer synchronous acknowledged typed inserts and read-only SQL execution.
"""
from __future__ import annotations

import json
from typing import Any, Mapping

from src.trading_runtime.arte_market_day_certification import (
    TABLES, _COLUMNS, family_hash, verify_market_day_certificate,
)
from src.trading_runtime.arte_market_day_keeper import (
    BuildAttestation, BuildClaim, MarketDayKeeperAuthority, require_attested_inventory,
)
from src.trading_runtime.arte_market_day_source_plan import TABLES as SOURCE_TABLES


def _read(client: Any, name: str, build_id: str) -> list[dict[str, Any]]:
    columns = ",".join(_COLUMNS[name])
    sql = (f"SELECT {columns} FROM arte.{name} WHERE build_id='{build_id}' "
           "FORMAT JSONEachRow")
    return [json.loads(line) for line in client.execute(sql).splitlines() if line.strip()]


def _exact(client: Any, prepared: Mapping[str, tuple[Mapping[str, Any], ...]],
           build_id: str, names: tuple[str, ...]) -> None:
    for name in names:
        actual = _read(client, name, build_id)
        expected = list(prepared[name])
        if (len(actual) != len(expected) or family_hash(actual) != family_hash(expected)
                or any(set(row) != set(_COLUMNS[name]) or row["build_id"] != build_id
                       for row in actual)):
            raise RuntimeError(f"Market-day {name} differs from exact stored inventory")


def _placement(client: Any) -> None:
    names = tuple(table.name for table in TABLES)
    quoted = ",".join(f"'{name}'" for name in names)
    tables = [json.loads(line) for line in client.execute(
        "SELECT name,storage_policy FROM system.tables WHERE database='arte' "
        f"AND name IN ({quoted}) FORMAT JSONEachRow").splitlines() if line.strip()]
    if (len(tables) != len(names) or {row["name"] for row in tables} != set(names)
            or any(set(row) != {"name", "storage_policy"}
                   or row["storage_policy"] != "live_market_ssd" for row in tables)):
        raise RuntimeError("Market-day certificate table policy is not live_market_ssd")
    parts = [json.loads(line) for line in client.execute(
        "SELECT table,disk_name FROM system.parts WHERE active AND database='arte' "
        f"AND table IN ({quoted}) FORMAT JSONEachRow").splitlines() if line.strip()]
    if any(set(row) != {"table", "disk_name"} or row["table"] not in names
           or row["disk_name"] != "live_market_ssd" for row in parts):
        raise RuntimeError("Market-day certificate part is outside live_market_ssd")


def publish_market_day_certificate(client: Any, keeper: MarketDayKeeperAuthority,
                                   claim: BuildClaim,
                                   prepared: Mapping[str, tuple[Mapping[str, Any], ...]],
                                   *, sessions: tuple[str, ...]) -> BuildAttestation:
    """Publish child facts first, fence last, then CAS-attest exact readback.

    On any uncertainty, no receipt is returned. Existing partial rows require
    cold reconciliation; this routine never deletes or rewrites them.
    """
    names = tuple(table.name for table in TABLES)
    required = {names[0], names[1], names[2], names[3],
                SOURCE_TABLES[0].name, names[-1]}
    if set(prepared) != set(names) or any(not prepared[name] for name in required):
        raise ValueError("Market-day preparation lacks a complete typed inventory")
    build_id = claim.build_id
    if not keeper.current(claim):
        raise RuntimeError("Market-day build claim is no longer current")
    # Validate the prepared structure with the same cold contract, before any insert.
    class _PreparedReader:
        def execute(self, sql: str) -> str:
            name = sql.split("FROM arte.", 1)[1].split(" ", 1)[0]
            return "\n".join(json.dumps(row) for row in prepared[name])
    verify_market_day_certificate(_PreparedReader(), build_id, sessions=sessions)
    _placement(client)
    for name in names[:-1]:
        if not keeper.current(claim):
            raise RuntimeError("Market-day build claim changed before publication")
        existing = _read(client, name, build_id)
        if existing:
            _exact(client, prepared, build_id, (name,))
        elif prepared[name]:
            client.insert_typed_rows(name, prepared[name])
            _exact(client, prepared, build_id, (name,))
        else:
            _exact(client, prepared, build_id, (name,))
    if not keeper.current(claim):
        raise RuntimeError("Market-day build claim changed before final fence")
    _exact(client, prepared, build_id, names[:-1])
    _placement(client)
    fence_name = names[-1]
    existing = _read(client, fence_name, build_id)
    if existing:
        _exact(client, prepared, build_id, (fence_name,))
    else:
        client.insert_typed_rows(fence_name, prepared[fence_name])
    _exact(client, prepared, build_id, names)
    _placement(client)
    verified = verify_market_day_certificate(client, build_id, sessions=sessions)
    fence = prepared[fence_name][0]
    if verified.build_id != build_id or verified.definition_hash != fence["definition_hash"]:
        raise RuntimeError("Market-day cold readback differs from prepared identity")
    if not keeper.current(claim):
        raise RuntimeError("Market-day claim changed before CAS attestation")
    proof = keeper.attest(claim, definition_hash=fence["definition_hash"],
                          source_plan_hash=fence["source_plan_hash"],
                          source_inventory_hash=fence["source_inventory_hash"],
                          header_hash=fence["header_hash"],
                          scope_hash=fence["scope_hash"],
                          stage_hash=fence["stage_hash"],
                          seed_hash=fence["seed_hash"])
    require_attested_inventory(proof, fence)
    # A delayed conflicting row after CAS must not be admitted by cold recovery.
    _exact(client, prepared, build_id, names)
    _placement(client)
    return proof
