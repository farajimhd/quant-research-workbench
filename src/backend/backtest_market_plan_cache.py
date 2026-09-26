"""Bounded in-process reuse of fully audited, immutable ARTE market plans.

Backtest never publishes a cache artifact. A hit requires the same Keeper proof
and the same table, column, and active-part inventory as the full prior audit.
Any change falls back to the exact cold audit; absent metadata fails closed.
"""
from __future__ import annotations

from hashlib import sha256
import json
from threading import Lock
from typing import Any, Mapping

from src.trading_runtime.arte_market_day_certification import TABLES as CERTIFICATE_TABLES


_NAMES = tuple(sorted({table.name for table in CERTIFICATE_TABLES} |
                      {"bars_v1", "indicators_v1", "liquidity_100ms_v1"}))
_SQL_NAMES = ",".join(f"'{name}'" for name in _NAMES)
_QUERIES = (
    ("table", "name,uuid,storage_policy,metadata_modification_time",
     "system.tables", "name", "name"),
    ("column", "table,name,type,position", "system.columns",
     "table", "table,position,name"),
    ("part", "table,name,disk_name,rows,bytes_on_disk,hash_of_all_files", "system.parts",
     "table", "table,name"),
)


def market_inventory_fingerprint(client: Any) -> str:
    """Hash every schema and active-part identity required by a cached plan."""
    digest = sha256(b"arte-market-plan-inventory-v1\0")
    for label, columns, system_table, key, ordering in _QUERIES:
        condition = "AND active " if label == "part" else ""
        sql = (f"SELECT {columns} FROM {system_table} WHERE database='arte' "
               f"AND {key} IN ({_SQL_NAMES}) {condition}"
               f"ORDER BY {ordering} FORMAT JSONEachRow")
        response = client.execute(sql)
        rows = [json.loads(line) for line in response.splitlines() if line.strip()]
        expected_columns = set(columns.split(","))
        if any(set(row) != expected_columns or row[key] not in _NAMES
               for row in rows):
            raise RuntimeError("Market inventory contains malformed metadata")
        if label == "table" and {row["name"] for row in rows} != set(_NAMES):
            raise RuntimeError("Market inventory omits a required table")
        if label == "column" and {row["table"] for row in rows} != set(_NAMES):
            raise RuntimeError("Market inventory omits a required column layout")
        if label == "part" and any(row["disk_name"] != "live_market_ssd"
                                   for row in rows):
            raise RuntimeError("Market inventory has active parts outside SSD")
        digest.update(label.encode() + b"\0")
        for row in rows:
            digest.update(json.dumps(row, sort_keys=True, separators=(",", ":"))
                          .encode() + b"\n")
    return digest.hexdigest()


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
