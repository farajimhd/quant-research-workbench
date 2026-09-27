"""Bounded in-process reuse of fully audited, immutable ARTE market plans.

Backtest never publishes a cache artifact. A hit requires the same Keeper proof
and the same table, column, and active-part inventory as the full prior audit.
Any change falls back to the exact cold audit; absent metadata fails closed.
"""
from __future__ import annotations

from hashlib import sha256
import json
import re
from threading import Lock
from typing import Any, Mapping

from src.trading_runtime.arte_market_day_certification import TABLES as CERTIFICATE_TABLES


_NAMES = tuple(sorted({table.name for table in CERTIFICATE_TABLES} |
                      {"bars_v1", "indicators_v1", "liquidity_100ms_v1"}))
_PRICE_NAMES = ("liquidity_execution_price_100ms_v1",
                "liquidity_execution_price_coverage_v1")
_QUERIES = (
    ("table", "name,uuid,storage_policy,metadata_modification_time",
     "system.tables", "name", "name"),
    ("column", "table,name,type,position", "system.columns",
     "table", "table,position,name"),
    ("part", "table,name,disk_name,rows,bytes_on_disk,hash_of_all_files", "system.parts",
     "table", "table,name"),
)


def _inventory_fingerprint(client: Any, names: tuple[str, ...], domain: bytes) -> str:
    """Hash schema and active parts; absent or off-policy metadata fails closed."""
    sql_names = ",".join(f"'{name}'" for name in names)
    digest = sha256(domain + b"\0")
    for label, columns, system_table, key, ordering in _QUERIES:
        condition = "AND active " if label == "part" else ""
        sql = (f"SELECT {columns} FROM {system_table} WHERE database='arte' "
               f"AND {key} IN ({sql_names}) {condition}"
               f"ORDER BY {ordering} FORMAT JSONEachRow")
        response = client.execute(sql)
        rows = [json.loads(line) for line in response.splitlines() if line.strip()]
        expected_columns = set(columns.split(","))
        if any(set(row) != expected_columns or row[key] not in names
               for row in rows):
            raise RuntimeError("Market inventory contains malformed metadata")
        if label == "table" and ({row["name"] for row in rows} != set(names)
                                 or any(row["storage_policy"] != "live_market_ssd"
                                        for row in rows)):
            raise RuntimeError("Market inventory omits a required table")
        if label == "column" and {row["table"] for row in rows} != set(names):
            raise RuntimeError("Market inventory omits a required column layout")
        if label == "part" and any(row["disk_name"] != "live_market_ssd"
                                   for row in rows):
            raise RuntimeError("Market inventory has active parts outside SSD")
        digest.update(label.encode() + b"\0")
        for row in rows:
            digest.update(json.dumps(row, sort_keys=True, separators=(",", ":"))
                          .encode() + b"\n")
    return digest.hexdigest()


def market_inventory_fingerprint(client: Any) -> str:
    """Hash every schema and active-part identity required by a cached plan."""
    return _inventory_fingerprint(client, _NAMES, b"arte-market-plan-inventory-v1")


def selected_market_inventory_fingerprint(
    client: Any, build_ids: tuple[str, ...], days: tuple[str, ...],
) -> str:
    """Fence only attested-build parts while still checking every table's SSD policy.

    A producer's unrelated build can append parts during a full cold audit.
    ClickHouse parts are immutable: an INSERT affecting the selected build/day
    adds a part, and a merge involving it replaces its part identity. Both
    change this fingerprint. Certificate/source families cover the full build;
    market products cover the sessions in this Backtest plan.
    """
    if (not build_ids or not days
            or any(not re.fullmatch(r"[0-9a-f]{64}(?:-[0-9a-f]{12})?", value)
                   for value in build_ids)
            or any(not re.fullmatch(r"\d{4}-\d{2}-\d{2}", value)
                   for value in days)):
        raise ValueError("Selected market inventory requires exact build and session keys")
    sql_names = ",".join(f"'{name}'" for name in _NAMES)
    digest = sha256(b"arte-selected-market-inventory-v1\0")
    for label, columns, system_table, key, ordering in _QUERIES[:2]:
        rows = [json.loads(line) for line in client.execute(
            f"SELECT {columns} FROM {system_table} WHERE database='arte' "
            f"AND {key} IN ({sql_names}) ORDER BY {ordering} FORMAT JSONEachRow"
        ).splitlines() if line.strip()]
        expected = set(columns.split(","))
        if (any(set(row) != expected or row[key] not in _NAMES for row in rows)
                or (label == "table" and (
                    {row["name"] for row in rows} != set(_NAMES)
                    or any(row["storage_policy"] != "live_market_ssd" for row in rows)))
                or (label == "column" and
                    {row["table"] for row in rows} != set(_NAMES))):
            raise RuntimeError("Selected market inventory schema or policy changed")
        digest.update(label.encode() + b"\0")
        for row in rows:
            digest.update(json.dumps(row, sort_keys=True, separators=(",", ":")).encode() + b"\n")
    parts = [json.loads(line) for line in client.execute(
        "SELECT table,name,disk_name,rows,bytes_on_disk,hash_of_all_files "
        "FROM system.parts WHERE database='arte' AND active "
        f"AND table IN ({sql_names}) ORDER BY table,name FORMAT JSONEachRow"
    ).splitlines() if line.strip()]
    expected_parts = {"table", "name", "disk_name", "rows", "bytes_on_disk",
                      "hash_of_all_files"}
    if any(set(row) != expected_parts or row["table"] not in _NAMES
           or row["disk_name"] != "live_market_ssd" for row in parts):
        raise RuntimeError("Selected market inventory has malformed or off-SSD parts")
    by_part = {(row["table"], row["name"]): row for row in parts}
    if len(by_part) != len(parts):
        raise RuntimeError("Selected market inventory repeats an active part")
    ids = ",".join(f"'{value}'" for value in build_ids)
    day_filter = ",".join(f"toDate('{value}')" for value in days)
    for name in _NAMES:
        scope = (f" AND session_date IN ({day_filter})" if name in {
            "bars_v1", "indicators_v1", "liquidity_100ms_v1"} else "")
        selected = client.execute(
            f"SELECT DISTINCT _part FROM arte.{name} WHERE build_id IN ({ids})"
            f"{scope} ORDER BY _part FORMAT TabSeparated"
        ).splitlines()
        if len(set(selected)) != len(selected) or any(
                not re.fullmatch(r"[A-Za-z0-9_]+", value) for value in selected):
            raise RuntimeError("Selected market inventory returned invalid part names")
        digest.update(name.encode() + b"\0")
        for part_name in selected:
            row = by_part.get((name, part_name))
            if row is None:
                raise RuntimeError("Selected market part merged during inventory read")
            digest.update(json.dumps(row, sort_keys=True,
                                     separators=(",", ":")).encode() + b"\n")
    return digest.hexdigest()


def price_inventory_fingerprint(client: Any) -> str:
    """Independently fence the two passive-fill price product tables."""
    return _inventory_fingerprint(client, _PRICE_NAMES,
                                  b"arte-price-plan-inventory-v1")


class MarketPlanCache:
    """One verified plan at a time; never a source of authority by itself."""

    def __init__(self) -> None:
        self._lock = Lock()
        self._entry: tuple[tuple, tuple, str, Any] | None = None

    def get(self, key: tuple, proofs: Mapping[str, Any], fingerprint: str) -> Any | None:
        identity = tuple(sorted(proofs.items()))
        with self._lock:
            if self._entry is None:
                return None
            saved_key, saved_proofs, saved_fingerprint, plan = self._entry
            return (plan if key == saved_key and identity == saved_proofs
                    and fingerprint == saved_fingerprint else None)

    def put(self, key: tuple, proofs: Mapping[str, Any],
            fingerprint: str, plan: Any) -> None:
        if not proofs or any(proof is None for proof in proofs.values()):
            raise RuntimeError("Market plan cache requires attested Keeper proofs")
        with self._lock:
            self._entry = (key, tuple(sorted(proofs.items())), fingerprint, plan)


MARKET_PLAN_CACHE = MarketPlanCache()


class FingerprintPlanCache:
    """One in-process plan; a verified active-part snapshot is mandatory per hit."""

    def __init__(self) -> None:
        self._lock = Lock()
        self._entry: tuple[str, str, Any] | None = None

    def get(self, token: str, fingerprint: str) -> Any | None:
        with self._lock:
            if self._entry is None:
                return None
            saved_token, saved_fingerprint, plan = self._entry
            return plan if (token, fingerprint) == (saved_token, saved_fingerprint) else None

    def put(self, token: str, fingerprint: str, plan: Any) -> None:
        if not token or not fingerprint:
            raise ValueError("Verified plan cache requires a token and inventory")
        with self._lock:
            self._entry = (token, fingerprint, plan)


PRICE_PLAN_CACHE = FingerprintPlanCache()
