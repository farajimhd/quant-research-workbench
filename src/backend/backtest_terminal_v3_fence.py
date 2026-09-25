"""Staged V3 terminal seal over an attested V3 running prefix.

V2 account/position facts retain their exact typed schema, but the terminal
fence has a distinct predecessor identity and hash. No DDL is run here.
"""
from __future__ import annotations

from datetime import datetime, timezone
from hashlib import sha256
import re
from typing import Any, Mapping
from uuid import UUID

from src.backend.backtest_squeeze_episode_v3 import V3CommittedPrefix
from src.backend.backtest_terminal_v2_fence import (
    _verify_rows, _verify_v1_rows, project_terminal_v2_commit,
)
from src.trading_runtime.arte_journal_schema import (
    BACKTEST_TERMINAL_SNAPSHOT_V2_TABLES, TableContract,
)
from src.trading_runtime.arte_journal_writer import V2CommittedPrefix, _literal, _rows
from src.trading_runtime.journal_contract import canonical_json


_V2 = next(t for t in BACKTEST_TERMINAL_SNAPSHOT_V2_TABLES
           if t.name == "trading_backtest_terminal_commit_v2")
TERMINAL_COMMIT_V3 = TableContract(
    "trading_backtest_terminal_commit_v3",
    tuple(("prior_v3_batch_id" if name == "prior_v2_batch_id" else
           "prior_v3_sequence" if name == "prior_v2_sequence" else name, kind)
          for name, kind in _V2.columns),
    _V2.partition, _V2.order,
)


def staged_terminal_v3_ddl() -> str:
    return TERMINAL_COMMIT_V3.ddl()


def project_terminal_v3_commit(
    prefix: V3CommittedPrefix, *, account_ids: tuple[str, ...],
    attempt_id: str, batch_id: str, source_cursor: str, status: str,
    committed_at: datetime, events: tuple[Mapping[str, Any], ...],
    transitions: tuple[Mapping[str, Any], ...],
    accounts: tuple[Mapping[str, Any], ...],
    positions: tuple[Mapping[str, Any], ...],
) -> dict[str, Any]:
    """Reuse strict suffix causality, then hash a distinct V3 predecessor."""
    if not isinstance(prefix, V3CommittedPrefix) or prefix.status != "running":
        raise ValueError("V3 terminal needs an attested running prefix")
    v2_shape = V2CommittedPrefix(
        prefix.run_id, prefix.last_sequence, prefix.last_batch_id,
        prefix.source_cursor, prefix.status, prefix.batch_ids)
    v2 = project_terminal_v2_commit(
        v2_shape, account_ids=account_ids, attempt_id=attempt_id,
        batch_id=batch_id, source_cursor=source_cursor, status=status,
        committed_at=committed_at, events=events, transitions=transitions,
        accounts=accounts, positions=positions)
    content = {key: value for key, value in v2.items() if key != "content_hash"}
    content["prior_v3_batch_id"] = content.pop("prior_v2_batch_id")
    content["prior_v3_sequence"] = content.pop("prior_v2_sequence")
    if set(content) != {name for name, _ in TERMINAL_COMMIT_V3.columns} - {"content_hash"}:
        raise RuntimeError("V3 terminal seal differs from its staged contract")
    return {**content, "content_hash": sha256(canonical_json(content).encode()).hexdigest()}


def load_terminal_v3_commit(
    client: Any, prefix: V3CommittedPrefix, *, account_ids: tuple[str, ...],
) -> dict[str, Any]:
    """Cold verify one V3 suffix and every unchanged typed fact family."""
    if not isinstance(prefix, V3CommittedPrefix) or prefix.status != "running":
        raise ValueError("V3 terminal readback needs a running prefix")
    rows = _rows(client,
        "SELECT * FROM arte.trading_backtest_terminal_commit_v3 "
        f"WHERE run_id={_literal(prefix.run_id)} FORMAT JSONEachRow")
    if len(rows) != 1:
        raise RuntimeError("V3 terminal suffix is missing or duplicated")
    stored = rows[0]
    if set(stored) != {name for name, _ in TERMINAL_COMMIT_V3.columns}:
        raise RuntimeError("V3 terminal seal has unknown columns")
    batch = str(UUID(str(stored["batch_id"])))
    facts = {}
    for table, name in (("trading_event_v1", "events"),
                        ("trading_run_transition_v1", "transitions"),
                        ("trading_backtest_account_snapshot_v2", "accounts"),
                        ("trading_backtest_position_snapshot_v2", "positions")):
        predicate = (f"run_id={_literal(prefix.run_id)}" if table.endswith("_v2")
                     else f"batch_id=toUUID({_literal(batch)})")
        facts[name] = tuple(_rows(client,
            f"SELECT * FROM arte.{table} WHERE {predicate} FORMAT JSONEachRow"))
    committed = str(stored["committed_at"])
    if re.fullmatch(r"\d{4}-\d\d-\d\d \d\d:\d\d:\d\d\.\d{6}", committed):
        committed_at = datetime.fromisoformat(committed).replace(tzinfo=timezone.utc)
    else:
        committed_at = datetime.fromisoformat(committed.replace("Z", "+00:00"))
        if committed_at.tzinfo is None:
            raise ValueError("V3 terminal commit time lacks UTC authority")
    calculated = project_terminal_v3_commit(
        prefix, account_ids=account_ids, attempt_id=stored["attempt_id"],
        batch_id=batch, source_cursor=stored["source_cursor"],
        status=stored["status"], committed_at=committed_at,
        **facts)
    normalized_stored = {**stored, "committed_at": committed_at.astimezone(
        timezone.utc).isoformat(timespec="microseconds")}
    if calculated != normalized_stored:
        raise RuntimeError("V3 terminal seal differs from cold account suffix")
    return calculated
