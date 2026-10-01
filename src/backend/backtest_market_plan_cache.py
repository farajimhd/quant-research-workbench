"""Bounded in-process reuse of fully audited, immutable ARTE market plans.

Backtest never publishes a cache artifact. A hit requires the same Keeper proof
and the same table, column, and active-part inventory as the full prior audit.
Any change falls back to the exact cold audit; absent metadata fails closed.
"""
from __future__ import annotations

from collections import OrderedDict
from datetime import date
from hashlib import sha256
import json
import re
from threading import Lock
from typing import Any, Mapping

from src.trading_runtime.arte_market_day_certification import TABLES as CERTIFICATE_TABLES
from src.trading_runtime.arte_market_day_session_seal import SESSION_SEAL


_NAMES = tuple(sorted({table.name for table in CERTIFICATE_TABLES} |
                      {SESSION_SEAL.name, "bars_v1", "indicators_v1",
                       "liquidity_100ms_v1"}))
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
    # One ClickHouse request lets independent table scans run within the server
    # instead of paying a network round-trip for every certificate family.
    selections = []
    for name in _NAMES:
        scope = (f" AND session_date IN ({day_filter})" if name in {
            "bars_v1", "indicators_v1", "liquidity_100ms_v1"} else "")
        selections.append(
            f"SELECT DISTINCT '{name}' AS table_name, _part AS part_name "
            f"FROM arte.{name} WHERE build_id IN ({ids}){scope}")
    selected_rows = client.execute(
        "SELECT table_name,part_name FROM (" + " UNION ALL ".join(selections)
        + ") ORDER BY table_name,part_name FORMAT TabSeparated"
    ).splitlines()
    selected_by_table: dict[str, list[str]] = {name: [] for name in _NAMES}
    prior: tuple[str, str] | None = None
    for line in selected_rows:
        fields = line.split("\t")
        if (len(fields) != 2 or fields[0] not in selected_by_table
                or not re.fullmatch(r"[A-Za-z0-9_]+", fields[1])
                or (prior is not None and tuple(fields) <= prior)):
            raise RuntimeError("Selected market inventory returned invalid part names")
        prior = (fields[0], fields[1])
        selected_by_table[fields[0]].append(fields[1])
    for name in _NAMES:
        digest.update(name.encode() + b"\0")
        for part_name in selected_by_table[name]:
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


def product_inventory_fingerprint(client: Any, names: tuple[str, ...]) -> str:
    """Fence exact normalized derivative tables before reusing a cold audit."""
    if (not names or len(names) != len(set(names))
            or any(not re.fullmatch(r"[a-z][a-z0-9_]*", name)
                   for name in names)):
        raise ValueError("Product inventory requires distinct arte table names")
    return _inventory_fingerprint(
        client, tuple(sorted(names)), b"arte-product-plan-inventory-v1")


def selected_product_inventory_fingerprint(
    client: Any, names: tuple[str, ...], *, source_build_id: str,
    session_date: str, tickers: tuple[str, ...],
) -> str:
    """Fence selected immutable parts, retaining global schema and SSD checks.

    These derivative tables share source_build_id/session_date/ticker keys.
    Unrelated publications need not invalidate a verified plan. A merge or
    insertion involving selected rows still changes the physical fingerprint.
    Coverage and child hashes remain the caller's independent logical audit.
    """
    if (not names or len(set(names)) != len(names)
            or any(not re.fullmatch(r"[a-z][a-z0-9_]*", name) for name in names)
            or not re.fullmatch(r"[0-9a-f]{64}(?:-[0-9a-f]{12})?", source_build_id)
            or date.fromisoformat(session_date).isoformat() != session_date
            or not tickers or any(type(value) is not str or not value
                                  for value in tickers)
            or tuple(sorted(set(tickers))) != tickers):
        raise ValueError("Selected product inventory requires exact ordered scope")
    # Import at call time: market_data itself imports this cache module.
    from src.backend.backtest_market_data import _literal
    names = tuple(sorted(names))
    sql_names = ",".join(_literal(name) for name in names)
    digest = sha256(b"arte-selected-product-inventory-v1\0")
    digest.update(json.dumps((names, source_build_id, session_date, tickers),
                             separators=(",", ":")).encode() + b"\0")
    for label, columns, system_table, key, ordering in _QUERIES[:2]:
        rows = [json.loads(line) for line in client.execute(
            f"SELECT {columns} FROM {system_table} WHERE database='arte' "
            f"AND {key} IN ({sql_names}) ORDER BY {ordering} FORMAT JSONEachRow"
        ).splitlines() if line.strip()]
        if (any(set(row) != set(columns.split(",")) or row[key] not in names
                for row in rows)
                or (label == "table" and (
                    {row["name"] for row in rows} != set(names)
                    or len(rows) != len(names)
                    or any(row["storage_policy"] != "live_market_ssd" for row in rows)))
                or (label == "column" and {row["table"] for row in rows} != set(names))):
            raise RuntimeError("Selected product inventory schema or policy changed")
        digest.update(label.encode() + b"\0")
        for row in rows:
            digest.update(json.dumps(row, sort_keys=True, separators=(",", ":"))
                          .encode() + b"\n")
    parts = [json.loads(line) for line in client.execute(
        "SELECT table,name,disk_name,rows,bytes_on_disk,hash_of_all_files "
        "FROM system.parts WHERE database='arte' AND active "
        f"AND table IN ({sql_names}) ORDER BY table,name FORMAT JSONEachRow"
    ).splitlines() if line.strip()]
    expected = set(_QUERIES[2][1].split(","))
    if any(set(row) != expected or row["table"] not in names
           or row["disk_name"] != "live_market_ssd" for row in parts):
        raise RuntimeError("Selected product inventory has malformed or off-SSD parts")
    by_part = {(row["table"], row["name"]): row for row in parts}
    if len(by_part) != len(parts):
        raise RuntimeError("Selected product inventory repeats an active part")
    scope = (f"source_build_id={_literal(source_build_id)} "
             f"AND session_date=toDate({_literal(session_date)}) "
             "AND ticker IN (" + ",".join(_literal(value) for value in tickers) + ")")
    selections = [f"SELECT DISTINCT '{name}' AS table_name, _part AS part_name "
                  f"FROM arte.{name} WHERE {scope}" for name in names]
    selected_rows = client.execute(
        "SELECT table_name,part_name FROM (" + " UNION ALL ".join(selections)
        + ") ORDER BY table_name,part_name FORMAT TabSeparated"
    ).splitlines()
    prior: tuple[str, str] | None = None
    digest.update(b"part\0")
    for line in selected_rows:
        fields = line.split("\t")
        if (len(fields) != 2 or fields[0] not in names
                or not re.fullmatch(r"[A-Za-z0-9_]+", fields[1])
                or (prior is not None and tuple(fields) <= prior)):
            raise RuntimeError("Selected product inventory returned invalid part names")
        prior = (fields[0], fields[1])
        row = by_part.get(prior)
        if row is None:
            raise RuntimeError("Selected product part merged during inventory read")
        digest.update(json.dumps(row, sort_keys=True, separators=(",", ":"))
                      .encode() + b"\n")
    return digest.hexdigest()


class MarketPlanCache:
    """Bounded verified plans; never a source of authority by themselves.

    A saved chart alternates between the all-ticker run plan and its narrow
    historical context plan. Keep both without weakening the per-request
    Keeper and active-part checks performed by the caller.
    """

    def __init__(self, max_entries: int = 16) -> None:
        if type(max_entries) is not int or not 1 <= max_entries <= 32:
            raise ValueError("Market plan cache needs a bounded positive capacity")
        self._lock = Lock()
        self._max_entries = max_entries
        self._entries: OrderedDict[tuple, tuple[tuple, str, str | None, Any]] = OrderedDict()

    def get(self, key: tuple, proofs: Mapping[str, Any], fingerprint: str) -> Any | None:
        identity = tuple(sorted(proofs.items()))
        with self._lock:
            saved = self._entries.get(key)
            if saved is None:
                return None
            saved_proofs, saved_fingerprint, _, plan = saved
            if identity != saved_proofs or fingerprint != saved_fingerprint:
                return None
            self._entries.move_to_end(key)
            return plan

    def get_selected(self, key: tuple, proofs: Mapping[str, Any],
                     fingerprint: str) -> Any | None:
        """Reuse only the same previously audited selected-build parts."""
        identity = tuple(sorted(proofs.items()))
        with self._lock:
            saved = self._entries.get(key)
            if saved is None:
                return None
            saved_proofs, _, selected, plan = saved
            if identity != saved_proofs or selected is None or fingerprint != selected:
                return None
            self._entries.move_to_end(key)
            return plan

    def put(self, key: tuple, proofs: Mapping[str, Any],
            fingerprint: str, plan: Any, *,
            selected_fingerprint: str | None = None) -> None:
        if not proofs or any(proof is None for proof in proofs.values()):
            raise RuntimeError("Market plan cache requires attested Keeper proofs")
        with self._lock:
            self._entries[key] = (tuple(sorted(proofs.items())), fingerprint,
                                  selected_fingerprint, plan)
            self._entries.move_to_end(key)
            if len(self._entries) > self._max_entries:
                self._entries.popitem(last=False)


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
