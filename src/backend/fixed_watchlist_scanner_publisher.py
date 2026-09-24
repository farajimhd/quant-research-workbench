"""Operator-only publication of certified QMD scanner sidecars.

No bootstrap, scheduling, DDL, or Backtest call site belongs here. An ambiguous
write consumes the persistent Keeper claim and requires operator reconciliation.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any, Mapping
import re

from src.backend.fixed_watchlist_scanner_sidecar import (
    load_scanner_boundary, operator_publication_plan,
)


_KEEPER_ROOT = "/trading/qmd_scanner_publication/v1"
_TABLES = ("qmd_scanner_symbol_v1", "qmd_scanner_boundary_v1")
_BATCH = 1000
_HEX = re.compile(r"[0-9a-f]{64}\Z")


def _rows(client: Any, sql: str) -> list[dict[str, Any]]:
    return list(client.iter_json_each_row(sql))


def scanner_sidecar_storage_preflight(client: Any) -> None:
    """Require installed, SSD-only tables and no misplaced active parts."""
    policies = _rows(client, "SELECT disks FROM system.storage_policies "
                     "WHERE policy_name='live_market_ssd' FORMAT JSONEachRow")
    if len(policies) != 1 or policies[0].get("disks") != ["live_market_ssd"]:
        raise RuntimeError("scanner sidecar requires an SSD-only live_market_ssd policy")
    names = "'qmd_scanner_symbol_v1','qmd_scanner_boundary_v1'"
    tables = _rows(client, "SELECT name,storage_policy,engine FROM system.tables "
                   f"WHERE database='arte' AND name IN ({names}) FORMAT JSONEachRow")
    if (len(tables) != len(_TABLES)
            or {row.get("name") for row in tables} != set(_TABLES)
            or any(row.get("storage_policy") != "live_market_ssd"
                   or row.get("engine") != "MergeTree" for row in tables)):
        raise RuntimeError("scanner sidecar tables are absent or not on live_market_ssd")
    parts = _rows(client, "SELECT table,disk_name FROM system.parts "
                  "WHERE active AND database='arte' "
                  f"AND table IN ({names}) AND disk_name!='live_market_ssd' "
                  "LIMIT 1 FORMAT JSONEachRow")
    if parts:
        raise RuntimeError("scanner sidecar has active parts outside live_market_ssd")


def _require_keeper(keeper: Any) -> None:
    state = getattr(keeper, "client_state", None)
    if (not getattr(keeper, "connected", False)
            or getattr(state, "name", str(state)) == "CONNECTED_RO"):
        raise RuntimeError("scanner publication Keeper is not writable")


def _claim_path(boundary_id: str) -> str:
    if _HEX.fullmatch(boundary_id) is None:
        raise ValueError("scanner boundary identity is invalid")
    return f"{_KEEPER_ROOT}/{boundary_id}"


def _claim(keeper: Any, boundary_id: str, content_hash: str) -> str:
    """Create a persistent single-use claim; never adopt or reopen one."""
    path = _claim_path(boundary_id)
    if _HEX.fullmatch(content_hash) is None:
        raise ValueError("scanner boundary content hash is invalid")
    _require_keeper(keeper)
    try:
        keeper.ensure_path(_KEEPER_ROOT)
        keeper.create(path, f"1\nstarted\n{content_hash}".encode("ascii"), ephemeral=False)
    except Exception as exc:
        # Even a timeout may mean the persistent create succeeded. No retry.
        raise RuntimeError("scanner publication claim exists or create is ambiguous") from exc
    _require_keeper(keeper)
    return path


def _assert_claim(keeper: Any, path: str, content_hash: str) -> Any:
    _require_keeper(keeper)
    try:
        value, stat = keeper.get(path)
    except Exception as exc:
        raise RuntimeError("scanner publication claim is unavailable") from exc
    if value != f"1\nstarted\n{content_hash}".encode("ascii"):
        raise RuntimeError("scanner publication claim changed")
    _require_keeper(keeper)
    return stat


def _assert_committed(keeper: Any, boundary_id: str, content_hash: str) -> None:
    if _HEX.fullmatch(content_hash) is None:
        raise ValueError("scanner boundary content hash is invalid")
    _require_keeper(keeper)
    try:
        value, _stat = keeper.get(_claim_path(boundary_id))
    except Exception as exc:
        raise RuntimeError("scanner publication commit proof is unavailable") from exc
    if value != f"1\ncommitted\n{content_hash}".encode("ascii"):
        raise RuntimeError("scanner publication Keeper proof is not committed or differs")
    _require_keeper(keeper)


def load_attested_scanner_boundary(
    client: Any, keeper: Any, boundary_id: str, *, market_plan_token: str,
    source_revision_token: str, boundary_at: datetime,
) -> tuple[dict[str, Any], tuple[dict[str, Any], ...]]:
    """Backtest-eligible cold read only when CH seal and Keeper commit agree."""
    scanner_sidecar_storage_preflight(client)
    boundary, rows = load_scanner_boundary(
        client, boundary_id, market_plan_token=market_plan_token,
        source_revision_token=source_revision_token, boundary_at=boundary_at,
    )
    _assert_committed(keeper, boundary_id, boundary["content_hash"])
    scanner_sidecar_storage_preflight(client)
    return boundary, rows


def publish_operator_scanner_boundary(
    client: Any, keeper: Any, snapshot: Mapping[str, Any], *,
    market_plan_token: str,
) -> dict[str, Any]:
    """Publish once after operator invocation; never called by Backtest.

    Every write is fenced by the persistent claim. Symbols precede the boundary
    seal. Failed/ambiguous writes are terminal for that identity; reconciliation
    is read-only and an operator must choose a new certified identity if needed.
    """
    plan = operator_publication_plan(snapshot, market_plan_token=market_plan_token)
    (symbol_table, symbols), (boundary_table, (boundary,)) = plan
    scanner_sidecar_storage_preflight(client)
    path = _claim(keeper, boundary["boundary_id"], boundary["content_hash"])
    for offset in range(0, len(symbols), _BATCH):
        _assert_claim(keeper, path, boundary["content_hash"])
        client.insert_json_each_row(symbol_table, list(symbols[offset:offset + _BATCH]))
    scanner_sidecar_storage_preflight(client)
    _assert_claim(keeper, path, boundary["content_hash"])
    client.insert_json_each_row(boundary_table, [boundary])
    scanner_sidecar_storage_preflight(client)
    stored, stored_symbols = load_scanner_boundary(
        client, boundary["boundary_id"], market_plan_token=market_plan_token,
        source_revision_token=boundary["source_revision_token"],
        boundary_at=datetime.fromisoformat(boundary["boundary_at"]),
    )
    if stored != boundary or stored_symbols != symbols:
        raise RuntimeError("scanner publication cold readback differs")
    stat = _assert_claim(keeper, path, boundary["content_hash"])
    try:
        keeper.set(path, f"1\ncommitted\n{boundary['content_hash']}".encode("ascii"),
                   version=stat.version)
    except Exception as exc:
        raise RuntimeError("scanner publication commit claim is ambiguous") from exc
    attested, attested_symbols = load_attested_scanner_boundary(
        client, keeper, boundary["boundary_id"], market_plan_token=market_plan_token,
        source_revision_token=boundary["source_revision_token"],
        boundary_at=datetime.fromisoformat(boundary["boundary_at"]),
    )
    if attested != boundary or attested_symbols != symbols:
        raise RuntimeError("scanner publication attested readback differs")
    return boundary
