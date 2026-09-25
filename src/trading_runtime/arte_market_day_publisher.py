"""Staged market-day certificate publisher, not called by the active producer.

Only producer-owned certificate tables can be inserted. The row transport uses
JSONEachRow to populate normalized typed columns; no JSON column is stored.
"""
from __future__ import annotations

import json
from collections import Counter
from typing import Any, Mapping, Sequence

from research.mlops.clickhouse import insert_json_each_row

from src.trading_runtime.arte_market_day_certification import (
    TABLES, _COLUMNS, _typed_readback_row, family_hash, read_certificate_rows,
    verify_market_day_certificate,
)
from src.trading_runtime.arte_market_day_keeper import (
    BuildAttestation, BuildClaim, MarketDayKeeperAuthority, require_attested_inventory,
)
from src.trading_runtime.arte_market_day_source_plan import (
    TABLES as SOURCE_TABLES, recover_source_plan,
    verify_source_plan_storage,
)
from src.trading_runtime.journal_contract import canonical_json


class MarketDayCertificateClient:
    """Bounded INSERT transport with a closed, typed certificate-table target."""

    def __init__(self, http_client: Any, *, batch_size: int = 4096) -> None:
        if type(batch_size) is not int or not 0 < batch_size <= 10_000:
            raise ValueError("Market-day certificate batch size is invalid")
        self.http_client = http_client
        self.batch_size = batch_size

    def execute(self, sql: str) -> str:
        if not sql.lstrip().upper().startswith("SELECT "):
            raise ValueError("Market-day certificate read transport is SELECT-only")
        return self.http_client.execute(sql)

    def insert_typed_rows(self, name: str,
                          rows: Sequence[Mapping[str, Any]]) -> None:
        if name not in _COLUMNS or not isinstance(rows, (tuple, list)):
            raise ValueError("Market-day insert target or row batch is invalid")
        # Validate the complete caller batch before the first bounded request.
        # After an uncertain HTTP acknowledgement the publisher re-reads exact
        # rows and sends only missing ones; it never blindly repeats a batch.
        for row in rows:
            try:
                valid = isinstance(row, Mapping) and _typed_readback_row(name, row) == dict(row)
            except (RuntimeError, ValueError, TypeError) as exc:
                raise ValueError("Market-day insert row differs from typed contract") from exc
            if not valid:
                raise ValueError("Market-day insert row differs from typed contract")
        for start in range(0, len(rows), self.batch_size):
            chunk = [dict(row) for row in rows[start:start + self.batch_size]]
            insert_json_each_row(self.http_client, "arte", name,
                                 list(_COLUMNS[name]), chunk)


def _read(client: Any, name: str, build_id: str) -> list[dict[str, Any]]:
    return read_certificate_rows(client, name, build_id)


def _exact(client: Any, prepared: Mapping[str, tuple[Mapping[str, Any], ...]],
           build_id: str, names: tuple[str, ...]) -> None:
    for name in names:
        actual = _read(client, name, build_id)
        expected = list(prepared[name])
        if (len(actual) != len(expected) or family_hash(actual) != family_hash(expected)
                or any(set(row) != set(_COLUMNS[name]) or row["build_id"] != build_id
                       for row in actual)):
            raise RuntimeError(f"Market-day {name} differs from exact stored inventory")


def _missing_rows(actual: list[dict[str, Any]],
                  expected: tuple[Mapping[str, Any], ...],
                  name: str) -> tuple[Mapping[str, Any], ...]:
    """Allow only an exact subset after an interrupted acknowledged insert.

    MergeTree does not enforce uniqueness, so a duplicate or foreign row is a
    hard conflict. A retry may add only rows that are proven absent.
    """
    expected_keys = [canonical_json(dict(row)) for row in expected]
    actual_counts = Counter(canonical_json(row) for row in actual)
    expected_counts = Counter(expected_keys)
    if any(count > expected_counts.get(key, 0)
           for key, count in actual_counts.items()):
        raise RuntimeError(f"Market-day {name} differs from exact stored inventory")
    missing_counts = expected_counts - actual_counts
    missing: list[Mapping[str, Any]] = []
    for row, key in zip(expected, expected_keys):
        if missing_counts[key] > 0:
            missing.append(row)
            missing_counts[key] -= 1
    return tuple(missing)


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


def publish_market_day_certificate(client: Any, source_client: Any,
                                   keeper: MarketDayKeeperAuthority,
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
    source_rows = {table.name: tuple(prepared[table.name]) for table in SOURCE_TABLES}
    source_plan = recover_source_plan(
        source_rows, build_id,
        expected_hash=prepared["market_day_build_header_v1"][0]["source_plan_hash"])
    keeper.verify_source(claim, source_client, source_plan,
        expected_hash=prepared["market_day_build_header_v1"][0]["source_plan_hash"])
    _placement(client)
    verify_source_plan_storage(client)
    # Reject a conflicting later family or an orphan fence before writing any
    # new child rows. Recheck each family below to catch concurrent changes.
    for name in names:
        _missing_rows(_read(client, name, build_id), prepared[name], name)
    for name in names[:-1]:
        if not keeper.current(claim):
            raise RuntimeError("Market-day build claim changed before publication")
        existing = _read(client, name, build_id)
        missing = _missing_rows(existing, prepared[name], name)
        if missing:
            client.insert_typed_rows(name, missing)
            _exact(client, prepared, build_id, (name,))
        else:
            _exact(client, prepared, build_id, (name,))
    if not keeper.current(claim):
        raise RuntimeError("Market-day build claim changed before final fence")
    _exact(client, prepared, build_id, names[:-1])
    _placement(client)
    fence_name = names[-1]
    existing = _read(client, fence_name, build_id)
    missing = _missing_rows(existing, prepared[fence_name], fence_name)
    if missing:
        client.insert_typed_rows(fence_name, missing)
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
