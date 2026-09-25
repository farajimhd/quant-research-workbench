"""Inactive named-column serialization of the V5 market-day source plan.

No catchall payload, EAV key/value column, or disk manifest is used here.
Unknown producer fields fail before any publication can be attempted.
"""
from __future__ import annotations

from hashlib import sha256
from datetime import date
import json
from types import SimpleNamespace
from typing import Any, Mapping

from src.trading_runtime.arte_journal_schema import TableContract
from src.trading_runtime.journal_contract import canonical_json


def verify_canonical_source_plan_at_publication(source_client: Any,
                                                 pinned: Mapping[str, Any]) -> None:
    """Producer-only canonical SELECT replay before a Keeper publication proof.

    Fixed Backtest consumes the resulting attestation; it never runs this
    expensive source-event query itself.
    """
    from scripts.build_market_day import source_plan

    sessions = list(pinned["sessions"])
    excluded = list(pinned["excluded_calendar_dates"])
    days = [date.fromisoformat(value) for value in (*sessions, *excluded)]
    if not days:
        raise RuntimeError("Market-day source plan has no dated range")
    populations = list(pinned["population"])
    explicit = any(row["excluded_canonical_tickers"] is None for row in populations)
    if explicit != all(row["excluded_canonical_tickers"] is None for row in populations):
        raise RuntimeError("Market-day source population mixes explicit and full-universe scope")
    symbols = tuple(sorted({row["ticker"] for row in pinned["units"]})) if explicit else ()
    args = SimpleNamespace(start=min(days), end=max(days), symbols=symbols,
        allow_carried_forward_universe=any(
            row["certificate"]["status"] == "carried_forward" for row in populations),
        max_plan_units=max(1, len(pinned["units"])))
    if source_plan(source_client, args) != pinned:
        raise RuntimeError("Canonical market-day source plan differs before publication")


_FIELDS: dict[str, tuple[tuple[str, str], ...]] = {
    "session": (("session_date", "Date"), ("requested", "UInt8"),
                ("predecessor_date", "Nullable(Date)")),
    "stat": (("source_date", "Date"), ("stats_version", "UInt32"),
             ("source_filter_key", "String"),
             ("total_event_rows_after_filters", "UInt64"),
             ("updated_at", "String")),
    "population": (("session_date", "Date"), ("authority", "String"),
        ("tradable_tickers", "UInt64"), ("snapshot_rows", "UInt64"),
        ("snapshot_hash", "String"), ("selected_ticker_days", "UInt64"),
        ("excluded_canonical_tickers", "Nullable(UInt64)"),
        ("tradable_without_canonical_events", "Nullable(UInt64)"),
        ("snapshot_id", "String"), ("source_universe_date", "Date"),
        ("captured_at_utc", "String"), ("available_at_utc", "String"),
        ("cutoff_utc", "String"), ("row_count", "UInt64"),
        ("tradable_count", "UInt64"), ("source_hash", "UInt64"),
        ("revision", "String"), ("status", "String"),
        ("has_source_session_date", "UInt8"),
        ("source_session_date", "Nullable(Date)")),
    "unit": (("source_date", "Date"), ("ticker", "String"),
        ("event_count", "UInt64"), ("next_ordinal", "UInt64"),
        ("last_ordinal", "UInt64"), ("first_sip_timestamp_us", "UInt64"),
        ("last_sip_timestamp_us", "UInt64"), ("build_step", "UInt64"),
        ("updated_at", "String")),
    "rule": (("token_id", "UInt64"), ("modifier_int", "UInt64"),
        ("update_high_low", "UInt8"), ("update_last", "UInt8"),
        ("update_volume", "UInt8")),
    "split": (("provider_ticker", "String"), ("execution_date", "Date"),
        ("split_from", "UInt64"), ("split_to", "UInt64"),
        ("inserted_at", "String")),
    "excluded_date": (("session_date", "Date"),),
}
_NAMES = {kind: f"market_day_source_{kind}_v1" for kind in _FIELDS}
_HEAD = "market_day_source_plan_v1"
TABLES = (TableContract(_HEAD, (
    ("build_id", "String"), ("plan_month", "Date"),
    ("source_plan_hash", "FixedString(64)"),
    *((f"{kind}_count", "UInt32") for kind in _FIELDS),
    *((f"{kind}_hash", "FixedString(64)") for kind in _FIELDS),
), "toYYYYMM(plan_month)", "build_id"), *(
    TableContract(_NAMES[kind], (("build_id", "String"), ("plan_month", "Date"),
                              ("ordinal", "UInt32"), *fields),
                  "toYYYYMM(plan_month)", "build_id,ordinal")
    for kind, fields in _FIELDS.items()
))


def verify_source_plan_storage(client: Any) -> None:
    """Require the exact named schema and SSD-only active placement before use."""
    def rows(sql: str) -> list[dict[str, Any]]:
        return [json.loads(line) for line in client.execute(sql).splitlines() if line.strip()]

    policies = rows("SELECT disks FROM system.storage_policies "
                    "WHERE policy_name='live_market_ssd' FORMAT JSONEachRow")
    if policies != [{"disks": ["live_market_ssd"]}]:
        raise RuntimeError("Source-plan policy is not SSD-only")
    names = ",".join(f"'{table.name}'" for table in TABLES)
    actual = rows("SELECT name,engine,storage_policy,partition_key,sorting_key "
                  "FROM system.tables WHERE database='arte' "
                  f"AND name IN ({names}) FORMAT JSONEachRow")
    by_name = {row.get("name"): row for row in actual}
    if len(actual) != len(TABLES) or set(by_name) != {table.name for table in TABLES}:
        raise RuntimeError("Source-plan tables are missing or duplicate")
    for table in TABLES:
        row = by_name[table.name]
        actual_order = tuple(field.strip() for field in str(row.get("sorting_key", "")).split(","))
        expected_order = tuple(field.strip() for field in table.order.split(","))
        if (row.get("engine"), row.get("storage_policy"),
            row.get("partition_key"), actual_order) != (
                "MergeTree", "live_market_ssd", table.partition, expected_order):
            raise RuntimeError(f"Source-plan table layout differs: {table.name}")
    actual_columns = rows("SELECT table,name,type FROM system.columns "
                          "WHERE database='arte' "
                          f"AND table IN ({names}) ORDER BY table,position FORMAT JSONEachRow")
    for table in TABLES:
        columns = tuple((row.get("name"), row.get("type")) for row in actual_columns
                        if row.get("table") == table.name)
        if columns != table.columns:
            raise RuntimeError(f"Source-plan table columns differ: {table.name}")
    if len(actual_columns) != sum(len(table.columns) for table in TABLES):
        raise RuntimeError("Source-plan column inventory has unexpected rows")
    misplaced = rows("SELECT table,disk_name FROM system.parts "
                     "WHERE database='arte' AND active "
                     f"AND table IN ({names}) AND disk_name!='live_market_ssd' "
                     "LIMIT 1 FORMAT JSONEachRow")
    if misplaced:
        raise RuntimeError("Source-plan active part is outside live_market_ssd")


def _digest(value: Any) -> str:
    # Exactly the producer's `scripts.build_market_day.digest` wire contract.
    return sha256(json.dumps(value, sort_keys=True, separators=(",", ":"),
                             default=str).encode()).hexdigest()


def _family_hash(rows: list[dict[str, Any]]) -> str:
    if rows and "ordinal" in rows[0]:
        rows = sorted(rows, key=lambda row: int(row["ordinal"]))
    return sha256(canonical_json(rows).encode()).hexdigest()


def source_inventory_hash(rows: Mapping[str, tuple[Mapping[str, Any], ...]]) -> str:
    if set(rows) != {table.name for table in TABLES}:
        raise ValueError("Market-day source inventory has missing or extra typed tables")
    return sha256(canonical_json([(table.name, _family_hash(
        [dict(row) for row in rows[table.name]])) for table in TABLES]).encode()).hexdigest()


def _exact(row: Mapping[str, Any], fields: tuple[tuple[str, str], ...]) -> dict[str, Any]:
    if not isinstance(row, Mapping) or set(row) != {name for name, _ in fields}:
        raise ValueError("Market-day source plan has missing or unmodeled named fields")
    result = dict(row)
    for name, kind in fields:
        value = result[name]
        if kind.startswith("Nullable(") and value is None:
            continue
        base = kind[9:-1] if kind.startswith("Nullable(") else kind
        if base == "String" or base == "Date":
            if type(value) is not str or not value:
                raise ValueError(f"Market-day source {name} requires source string")
        elif base.startswith("UInt"):
            if type(value) is not int or value < 0 or value >= 1 << int(base[4:]):
                raise ValueError(f"Market-day source {name} requires bounded integer")
        else:
            raise ValueError(f"Unsupported market-day source column {name}")
    return result


def project_source_plan(plan: Mapping[str, Any], build_id: str) -> dict[str, tuple[dict[str, Any], ...]]:
    """Flatten the exact producer V5 plan; reject unknown or lossy values."""
    expected = {"sessions", "requested", "predecessors", "stats", "population",
                "units", "rules", "splits", "excluded_calendar_dates"}
    if not isinstance(plan, Mapping) or set(plan) != expected:
        raise ValueError("Market-day source plan has missing or unmodeled families")
    sessions = plan["sessions"]
    if (not isinstance(sessions, list) or not sessions
            or len(set(sessions)) != len(sessions)
            or set(plan["requested"]) != set(sessions)
            or set(plan["predecessors"]) != set(sessions)):
        raise ValueError("Market-day source sessions or predecessors are incomplete")
    month = min(sessions)[:7] + "-01"
    source: dict[str, list[dict[str, Any]]] = {
        "session": [dict(session_date=day, requested=int(day in plan["requested"]),
                         predecessor_date=plan["predecessors"][day]) for day in sessions],
        "stat": list(plan["stats"]), "population": [], "unit": list(plan["units"]),
        "rule": list(plan["rules"]), "split": list(plan["splits"]),
        "excluded_date": [dict(session_date=day) for day in plan["excluded_calendar_dates"]],
    }
    certificate_fields = {"snapshot_id", "source_universe_date", "captured_at_utc",
        "available_at_utc", "cutoff_utc", "row_count", "tradable_count",
        "source_hash", "revision", "status"}
    population_fields = {name for name, _ in _FIELDS["population"]} - certificate_fields - {
        "has_source_session_date", "source_session_date"}
    for row in plan["population"]:
        if set(row) != population_fields | {"certificate"}:
            raise ValueError("Market-day population has unmodeled named fields")
        certificate = row["certificate"]
        if set(certificate) not in (certificate_fields,
                                    certificate_fields | {"source_session_date"}):
            raise ValueError("Market-day population certificate has unmodeled fields")
        source_date = certificate.get("source_session_date")
        source["population"].append({**{key: row[key] for key in population_fields},
            **{key: certificate[key] for key in certificate_fields},
            "has_source_session_date": int("source_session_date" in certificate),
            "source_session_date": source_date})
    output: dict[str, tuple[dict[str, Any], ...]] = {}
    for kind, values in source.items():
        output[_NAMES[kind]] = tuple({"build_id": build_id, "plan_month": month,
            "ordinal": ordinal, **_exact(value, _FIELDS[kind])}
            for ordinal, value in enumerate(values))
    head = {"build_id": build_id, "plan_month": month,
            "source_plan_hash": _digest(plan)}
    for kind in _FIELDS:
        rows = list(output[_NAMES[kind]])
        head[f"{kind}_count"] = len(rows)
        head[f"{kind}_hash"] = _family_hash(rows)
    output[_HEAD] = (head,)
    recover_source_plan(output, build_id, expected_hash=head["source_plan_hash"])
    return output


def recover_source_plan(rows: Mapping[str, tuple[Mapping[str, Any], ...]],
                        build_id: str, *, expected_hash: str) -> dict[str, Any]:
    """Require exact typed families, ordinals, digests and source-plan parity."""
    if set(rows) != {_HEAD, *_NAMES.values()} or len(rows[_HEAD]) != 1:
        raise ValueError("Market-day source plan typed inventory is incomplete")
    head = dict(rows[_HEAD][0])
    if head["build_id"] != build_id or head["source_plan_hash"] != expected_hash:
        raise ValueError("Market-day source plan identity or hash differs")
    restored: dict[str, Any] = {}
    families: dict[str, list[dict[str, Any]]] = {}
    for kind, name in _NAMES.items():
        family = [dict(row) for row in rows[name]]
        if (len(family) != int(head[f"{kind}_count"])
                or _family_hash(family) != head[f"{kind}_hash"]
                or sorted(int(row["ordinal"]) for row in family) != list(range(len(family)))
                or any(row["build_id"] != build_id or row["plan_month"] != head["plan_month"]
                       or set(row) != {"build_id", "plan_month", "ordinal"} |
                          {field for field, _ in _FIELDS[kind]} for row in family)):
            raise ValueError(f"Market-day source {kind} inventory differs from fence")
        families[kind] = [{field: row[field] for field, _ in _FIELDS[kind]}
                          for row in sorted(family, key=lambda row: row["ordinal"])]
    sessions = families["session"]
    restored["sessions"] = [row["session_date"] for row in sessions]
    restored["requested"] = [row["session_date"] for row in sessions if row["requested"]]
    restored["predecessors"] = {row["session_date"]: row["predecessor_date"] for row in sessions}
    restored["stats"] = families["stat"]
    population = []
    certificate_fields = {"snapshot_id", "source_universe_date", "captured_at_utc",
        "available_at_utc", "cutoff_utc", "row_count", "tradable_count",
        "source_hash", "revision", "status"}
    for row in families["population"]:
        cert = {key: row[key] for key in certificate_fields}
        if row["has_source_session_date"]:
            cert["source_session_date"] = row["source_session_date"]
        population.append({key: value for key, value in row.items()
                           if key not in certificate_fields | {
                               "has_source_session_date", "source_session_date"}} |
                          {"certificate": cert})
    restored["population"] = population
    restored["units"] = families["unit"]
    restored["rules"] = families["rule"]
    restored["splits"] = families["split"]
    restored["excluded_calendar_dates"] = [row["session_date"] for row in families["excluded_date"]]
    if _digest(restored) != expected_hash:
        raise ValueError("Market-day source plan reconstruction differs from producer hash")
    return restored
