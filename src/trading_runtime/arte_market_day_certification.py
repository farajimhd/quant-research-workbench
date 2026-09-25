"""Inactive, read-only market-day certificate contract; no producer cutover."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from hashlib import sha256
import json
import re
from typing import Any, Mapping

from src.trading_runtime.arte_journal_schema import TableContract
from src.trading_runtime.journal_contract import canonical_json
from src.trading_runtime.arte_market_day_source_plan import (
    TABLES as SOURCE_TABLES, project_source_plan, recover_source_plan,
    source_inventory_hash,
)


STAGES = frozenset({"bars", "technical", "broker_100ms"})
_BASE_TABLES = (
    TableContract("market_day_build_header_v1", (
        ("build_id", "String"), ("definition_hash", "FixedString(64)"),
        ("version", "String"), ("calculation_source_hash", "FixedString(64)"),
        ("rules_hash", "FixedString(64)"), ("source_plan_hash", "FixedString(64)"),
        ("scope_count", "UInt32"), ("scope_hash", "FixedString(64)"),
    ), "tuple()", "build_id"),
    TableContract("market_day_planned_scope_v1", (
        ("build_id", "String"), ("session_date", "Date"), ("ticker", "String"),
        ("source_event_count", "UInt64"), ("first_ordinal", "UInt64"),
        ("last_ordinal", "UInt64"), ("population_snapshot_id", "String"),
        ("population_revision", "String"),
        ("population_available_at", "DateTime64(6, 'UTC')"),
        ("population_cutoff_at", "DateTime64(6, 'UTC')"),
        ("population_source_hash", "String"),
    ), "toYYYYMM(session_date)", "build_id,session_date,ticker"),
    TableContract("market_day_stage_certificate_v1", (
        ("build_id", "String"), ("session_date", "Date"), ("ticker", "String"),
        ("stage", "LowCardinality(String)"), ("attempt_id", "String"),
        ("source_hash", "String"), ("output_rows", "UInt64"),
        ("output_hash", "String"),
    ), "toYYYYMM(session_date)", "build_id,session_date,ticker,stage"),
    TableContract("market_day_seed_v1", (
        ("build_id", "String"), ("session_date", "Date"), ("ticker", "String"),
        ("attempt_id", "String"), ("mode", "UInt8"),
        ("predecessor_date", "String"), ("prior_build_id", "String"),
        ("prior_state_hash", "String"),
    ), "toYYYYMM(session_date)", "build_id,session_date,ticker"),
    TableContract("market_day_build_fence_v1", (
        ("build_id", "String"), ("definition_hash", "FixedString(64)"),
        ("source_plan_hash", "FixedString(64)"),
        ("source_inventory_hash", "FixedString(64)"),
        ("header_hash", "FixedString(64)"), ("scope_count", "UInt32"),
        ("scope_hash", "FixedString(64)"), ("stage_count", "UInt32"),
        ("stage_hash", "FixedString(64)"), ("seed_count", "UInt32"),
        ("seed_hash", "FixedString(64)"),
    ), "tuple()", "build_id"),
)
TABLES = (*_BASE_TABLES[:-1], *SOURCE_TABLES, _BASE_TABLES[-1])
_COLUMNS = {table.name: tuple(name for name, _ in table.columns) for table in TABLES}
_TABLE_BY_NAME = {table.name: table for table in TABLES}


def _typed_readback_row(name: str, row: Mapping[str, Any]) -> dict[str, Any]:
    """Canonicalize ClickHouse JSONEachRow wire values to certificate types."""
    table = _TABLE_BY_NAME[name]
    if set(row) != set(_COLUMNS[name]):
        raise RuntimeError(f"Invalid {name} certificate row")
    normalized: dict[str, Any] = {}
    for column, kind in table.columns:
        value = row[column]
        if kind.startswith("Nullable("):
            if value is None:
                normalized[column] = None
                continue
            kind = kind[len("Nullable("):-1]
        if kind.startswith("UInt"):
            bits = int(kind[4:])
            if (isinstance(value, bool) or not isinstance(value, (int, str))
                    or not re.fullmatch(r"[0-9]+", str(value))):
                raise RuntimeError(f"Invalid {name}.{column} unsigned integer")
            number = int(value)
            if number >= 1 << bits:
                raise RuntimeError(f"Invalid {name}.{column} unsigned integer")
            normalized[column] = number
        elif kind == "DateTime64(6, 'UTC')":
            if not isinstance(value, str):
                raise RuntimeError(f"Invalid {name}.{column} UTC timestamp")
            normalized[column] = _producer_utc_wire(value)
        elif isinstance(value, str):
            normalized[column] = value
        else:
            raise RuntimeError(f"Invalid {name}.{column} certificate value")
    return normalized


def read_certificate_rows(client: Any, name: str, build_id: str) -> list[dict[str, Any]]:
    if name not in _TABLE_BY_NAME or not re.fullmatch(r"[A-Za-z0-9_-]{1,96}", build_id):
        raise ValueError("Invalid market-day certificate read target")
    sql = (f"SELECT {','.join(_COLUMNS[name])} FROM arte.{name} "
           f"WHERE build_id='{build_id}' FORMAT JSONEachRow")
    return [_typed_readback_row(name, json.loads(line))
            for line in client.execute(sql).splitlines() if line.strip()]


def family_hash(rows: list[Mapping[str, Any]]) -> str:
    """Order-independent digest of exact named rows (duplicates remain visible)."""
    # The published digest is canonical_json(the rows sorted by their own
    # canonical_json). Serialize each row only once; this preserves the exact
    # existing wire hash, including duplicate rows, without a second full
    # JSON traversal of the large build certificate.
    items = sorted(canonical_json(dict(row)) for row in rows)
    return sha256(("[" + ",".join(items) + "]").encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class MarketDayCertificate:
    build_id: str
    definition_hash: str
    scopes: tuple[tuple[str, str], ...]
    stages: tuple[tuple[str, str, str, str, int, str], ...]


def verify_market_day_certificate(client: Any, build_id: str, *,
                                  sessions: tuple[str, ...]) -> MarketDayCertificate:
    """Read a complete late-fenced certificate; never infer empty units from market rows."""
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,96}", build_id):
        raise ValueError("Invalid market-day build identity")
    if not sessions or len(set(sessions)) != len(sessions):
        raise ValueError("Requested market-day sessions must be unique and nonempty")
    families: dict[str, list[dict[str, Any]]] = {}
    for table in TABLES:
        rows = read_certificate_rows(client, table.name, build_id)
        if any(set(row) != set(_COLUMNS[table.name]) or row["build_id"] != build_id
               for row in rows):
            raise RuntimeError(f"Invalid {table.name} certificate row")
        families[table.name] = rows
    header, scopes, stages, seeds = (families[table.name] for table in _BASE_TABLES[:-1])
    fences = families[_BASE_TABLES[-1].name]
    if len(header) != 1 or len(fences) != 1:
        raise RuntimeError("Market-day certification requires one header and one final fence")
    head, fence = header[0], fences[0]
    if head["version"] != "market-day-core-v5" or any(
        not re.fullmatch(r"[0-9a-f]{64}", str(head[key])) for key in (
            "definition_hash", "calculation_source_hash", "rules_hash", "source_plan_hash")
    ):
        raise RuntimeError("Market-day definition or source pin is invalid")
    scope_keys = [(row["session_date"], row["ticker"]) for row in scopes]
    stage_keys = [(row["session_date"], row["ticker"], row["stage"]) for row in stages]
    seed_keys = [(row["session_date"], row["ticker"]) for row in seeds]
    if (not scopes or len(set(scope_keys)) != len(scopes)
            or len(set(stage_keys)) != len(stages)
            or len(set(seed_keys)) != len(seeds)
            or set(seed_keys) != set(scope_keys)
            or set(stage_keys) != {(day, ticker, stage) for day, ticker in scope_keys
                                   for stage in STAGES}):
        raise RuntimeError("Market-day planned scope, stage, or seed inventory is incomplete or duplicate")
    if not set(sessions).issubset({day for day, _ in scope_keys}):
        raise RuntimeError("Requested session lacks planned market-day scope")
    source_rows = {table.name: tuple(families[table.name]) for table in SOURCE_TABLES}
    if (fence["source_plan_hash"] != head["source_plan_hash"]
            or fence["source_inventory_hash"] != source_inventory_hash(source_rows)):
        raise RuntimeError("Market-day source inventory differs from final fence")
    source_plan = recover_source_plan(source_rows, build_id,
                                      expected_hash=head["source_plan_hash"])
    expected_scopes = {(row["source_date"], row["ticker"])
                       for row in source_plan["units"]}
    source_units = {(row["source_date"], row["ticker"]): row
                    for row in source_plan["units"]}
    if (set(scope_keys) != expected_scopes
            or not set(sessions).issubset(source_plan["requested"])
            or any((int(scope["source_event_count"]) != int(source_units[key]["event_count"])
                    or int(scope["first_ordinal"]) != (
                        int(source_units[key]["next_ordinal"]) - int(source_units[key]["event_count"]))
                    or int(scope["last_ordinal"]) != int(source_units[key]["last_ordinal"]))
                   for scope in scopes for key in [(scope["session_date"], scope["ticker"])])):
        raise RuntimeError("Market-day planned scopes differ from typed source plan")
    for row in scopes:
        if (not row["ticker"] or not row["population_snapshot_id"]
                or not row["population_revision"] or not row["population_source_hash"]
                or _utc(row["population_available_at"]) >= _utc(row["population_cutoff_at"])
                or int(row["source_event_count"]) < 0
                or (int(row["source_event_count"]) == 0
                    and (int(row["first_ordinal"]), int(row["last_ordinal"])) != (0, 0))
                or (int(row["source_event_count"]) > 0
                    and int(row["first_ordinal"]) > int(row["last_ordinal"]))):
            raise RuntimeError("Market-day population or source scope is invalid")
    attempts = {(r["session_date"], r["ticker"]): r["attempt_id"]
                for r in stages if r["stage"] == "technical"}
    for row in stages:
        if (row["stage"] not in STAGES or not row["attempt_id"] or not row["source_hash"]
                or int(row["output_rows"]) < 0
                or (int(row["output_rows"]) == 0 and row["output_hash"] != "0")
                or (int(row["output_rows"]) > 0 and not row["output_hash"])):
            raise RuntimeError("Market-day stage certificate is invalid")
    for row in seeds:
        prior = (row["predecessor_date"], row["prior_build_id"], row["prior_state_hash"])
        if (row["attempt_id"] != attempts[(row["session_date"], row["ticker"])]
                or int(row["mode"]) not in (0, 1)
                or (int(row["mode"]) == 0 and any(prior[1:]))
                or (int(row["mode"]) == 1 and any(not value for value in prior))):
            raise RuntimeError("Market-day seed provenance conflicts with technical attempt")
    checks = (("header_hash", family_hash(header)), ("scope_hash", family_hash(scopes)),
              ("stage_hash", family_hash(stages)), ("seed_hash", family_hash(seeds)))
    if (fence["definition_hash"] != head["definition_hash"]
            or any(fence[key] != digest for key, digest in checks)
            or head["scope_hash"] != family_hash(scopes)
            or int(head["scope_count"]) != len(scopes)
            or any(int(fence[key]) != length for key, length in (
                ("scope_count", len(scopes)), ("stage_count", len(stages)),
                ("seed_count", len(seeds))))):
        raise RuntimeError("Market-day final fence does not seal complete typed inventory")
    return MarketDayCertificate(build_id, head["definition_hash"],
        tuple(sorted(scope_keys)), tuple(sorted((r["session_date"], r["ticker"],
            r["stage"], r["attempt_id"], int(r["output_rows"]), r["output_hash"])
            for r in stages)))


def _utc(value: str) -> datetime:
    parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise RuntimeError("Market-day population timestamp is not timezone-aware")
    return parsed.astimezone(timezone.utc)


def _producer_utc_wire(value: Any) -> str:
    """Decode a ClickHouse DateTime64(..., 'UTC') result at the producer edge.

    ClickHouse JSONEachRow renders this explicitly UTC-typed source column
    without an offset. The source plan keeps its original wire string and
    hash; only the typed DateTime64 certificate row gains an explicit offset.
    """
    parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    if parsed.utcoffset() != timezone.utc.utcoffset(parsed):
        raise ValueError("Market-day population UTC column has a non-UTC offset")
    return parsed.isoformat()


def prepare_market_day_certificate(definition: Mapping[str, Any], build_id: str,
                                   ledger: Any) -> dict[str, tuple[dict[str, Any], ...]]:
    """Pure producer-side preparation; intentionally does not publish a fence.

    The caller must hold the producer's immutable source plan. A future writer
    needs stale-owner fencing before any prepared row can become authoritative.
    """
    if not re.fullmatch(r"[0-9a-f]{64}(?:-[0-9a-f]{12})?", build_id):
        raise ValueError("Market-day build ID does not name a definition")
    if not isinstance(definition, Mapping) or definition.get("version") != "market-day-core-v5":
        raise ValueError("Market-day definition version is unsupported")
    plan = definition.get("plan")
    if not isinstance(plan, Mapping):
        raise ValueError("Market-day definition lacks a typed source plan")
    producer_digest = lambda value: sha256(json.dumps(
        value, sort_keys=True, separators=(",", ":"), default=str).encode()).hexdigest()
    definition_hash = producer_digest(definition)
    if build_id.split("-", 1)[0] != definition_hash:
        raise ValueError("Market-day build ID differs from its definition")
    populations = {str(row["session_date"]): row for row in plan.get("population", ())}
    if len(populations) != len(plan.get("population", ())):
        raise ValueError("Market-day population has duplicate sessions")
    units = plan.get("units", ())
    if not units:
        raise ValueError("Market-day definition has no planned scopes")
    scopes: list[dict[str, Any]] = []
    stages: list[dict[str, Any]] = []
    seeds: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    for unit in units:
        day, ticker = str(unit["source_date"]), str(unit["ticker"])
        key = (day, ticker)
        if key in seen or day not in populations or not ticker:
            raise ValueError("Market-day planned scope is duplicate or lacks dated population")
        seen.add(key)
        certificate = populations[day]["certificate"]
        scopes.append(dict(build_id=build_id, session_date=day, ticker=ticker,
            source_event_count=int(unit["event_count"]),
            first_ordinal=int(unit["next_ordinal"]) - int(unit["event_count"]),
            last_ordinal=int(unit["last_ordinal"]),
            population_snapshot_id=str(certificate["snapshot_id"]),
            population_revision=str(certificate["revision"]),
            population_available_at=_producer_utc_wire(certificate["available_at_utc"]),
            population_cutoff_at=_producer_utc_wire(certificate["cutoff_utc"]),
            population_source_hash=str(certificate["source_hash"])))
        for stage in sorted(STAGES):
            product = ledger.unit(build_id, day, ticker, stage)
            if not product or product["status"] != "complete":
                raise ValueError(f"Market-day planned scope lacks completed {stage}: {day} {ticker}")
            stages.append(dict(build_id=build_id, session_date=day, ticker=ticker,
                stage=stage, attempt_id=str(product["attempt_id"]),
                source_hash=str(product["source_hash"]),
                output_rows=int(product["output_rows"]),
                output_hash=str(product["output_hash"])))
        seed = ledger.seed(build_id, day, ticker)
        if not seed:
            raise ValueError(f"Market-day planned scope lacks seed: {day} {ticker}")
        seeds.append(dict(build_id=build_id, session_date=day, ticker=ticker,
            attempt_id=str(seed["attempt_id"]), mode=int(seed["mode"]),
            predecessor_date=str(seed["predecessor_date"]),
            prior_build_id=str(seed["prior_build_id"]),
            prior_state_hash=str(seed["prior_state_hash"])))
    header = [dict(build_id=build_id, definition_hash=definition_hash,
        version=str(definition["version"]),
        calculation_source_hash=str(definition["calculation_source"]),
        rules_hash=str(definition["rules_hash"]),
        source_plan_hash=producer_digest(plan), scope_count=len(scopes),
        scope_hash=family_hash(scopes))]
    source_rows = project_source_plan(plan, build_id)
    fence = [dict(build_id=build_id, definition_hash=definition_hash,
        source_plan_hash=source_rows["market_day_source_plan_v1"][0]["source_plan_hash"],
        source_inventory_hash=source_inventory_hash(source_rows),
        header_hash=family_hash(header), scope_count=len(scopes),
        scope_hash=family_hash(scopes), stage_count=len(stages),
        stage_hash=family_hash(stages), seed_count=len(seeds),
        seed_hash=family_hash(seeds))]
    prepared = {**dict(zip((table.name for table in _BASE_TABLES[:-1]),
                           (header, scopes, stages, seeds))),
                **source_rows, _BASE_TABLES[-1].name: fence}
    # Share all structural checks with the cold reader before returning rows.
    class _PreparedReader:
        def execute(self, sql: str) -> str:
            name = sql.split("FROM arte.", 1)[1].split(" ", 1)[0]
            return "\n".join(json.dumps(row) for row in prepared[name])
    verify_market_day_certificate(_PreparedReader(), build_id,
                                  sessions=tuple(str(day) for day in plan.get("requested", ())))
    return {name: tuple(rows) for name, rows in prepared.items()}
