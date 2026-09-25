"""Staged, read-only V2 terminal seal over a verified running V1 prefix.

No writer or DDL path is enabled. V2 owns terminal event sequences after V1;
existing V1 commits and their canonical hashes are never rewritten.
"""
from __future__ import annotations

from datetime import date, datetime, timezone
from hashlib import sha256
import math
import re
from typing import Any, Mapping
from uuid import UUID

from src.backend.backtest_terminal_snapshot_v2 import recover_snapshot_group
from src.trading_runtime.arte_journal_schema import BACKTEST_TERMINAL_SNAPSHOT_V2_TABLES
from src.trading_runtime.arte_journal_writer import (
    CommittedPrefix, _canonical_typed_content, _literal, _rows,
)
from src.trading_runtime.journal_contract import canonical_json


_CONTRACTS = {table.name: dict(table.columns)
              for table in BACKTEST_TERMINAL_SNAPSHOT_V2_TABLES}
_ACCOUNT = "trading_backtest_account_snapshot_v2"
_POSITION = "trading_backtest_position_snapshot_v2"
_COMMIT = "trading_backtest_terminal_commit_v2"
_HEX = re.compile(r"[0-9a-f]{64}\Z")


def _digest(value: Any) -> str:
    return sha256(canonical_json(value).encode("utf-8")).hexdigest()


def _v2_content(table: str, row: Mapping[str, Any]) -> dict[str, Any]:
    columns = _CONTRACTS[table]
    if set(row) != set(columns) - {"content_hash"}:
        raise ValueError(f"{table} has missing or extra typed columns")
    result = dict(row)
    for key, kind in columns.items():
        if key == "content_hash":
            continue
        value = result[key]
        if kind == "UUID":
            result[key] = str(UUID(str(value)))
        elif kind == "Float64":
            if type(value) is not float or not math.isfinite(value):
                raise ValueError(f"{table}.{key} is not finite Float64")
        elif kind.startswith("UInt"):
            if type(value) is not int or not 0 <= value < 1 << int(kind[4:]):
                raise ValueError(f"{table}.{key} is outside its unsigned width")
        elif kind == "FixedString(64)":
            if not isinstance(value, str) or not _HEX.fullmatch(value):
                raise ValueError(f"{table}.{key} is not a SHA-256 digest")
        elif kind == "Date":
            result[key] = date.fromisoformat(str(value)).isoformat()
        elif kind == "DateTime64(6, 'UTC')":
            source = str(value).replace("Z", "+00:00")
            at = datetime.fromisoformat(source)
            if at.tzinfo is None:
                if not re.fullmatch(r"\d{4}-\d\d-\d\d \d\d:\d\d:\d\d\.\d{6}", source):
                    raise ValueError(f"{table}.{key} is not timezone aware")
                at = at.replace(tzinfo=timezone.utc)
            result[key] = at.astimezone(timezone.utc).isoformat(timespec="microseconds")
        elif kind in {"String", "LowCardinality(String)"}:
            if not isinstance(value, str) or not value:
                raise ValueError(f"{table}.{key} is empty or not text")
        else:
            raise ValueError(f"{table}.{key} has unsupported typed kind")
    return result


def seal_v2_row(table: str, row: Mapping[str, Any]) -> dict[str, Any]:
    """Hash exact staged scalar columns; callers cannot supply a digest."""
    canonical = _v2_content(table, row)
    return {**canonical, "content_hash": _digest(canonical)}


def _verify_rows(table: str, rows: tuple[Mapping[str, Any], ...]) -> tuple[dict[str, Any], ...]:
    result = []
    for row in rows:
        content = {key: value for key, value in row.items() if key != "content_hash"}
        sealed = seal_v2_row(table, content)
        if row.get("content_hash") != sealed["content_hash"]:
            raise ValueError(f"{table} row hash differs")
        result.append(sealed)
    return tuple(result)


def _verify_v1_rows(
    table: str, rows: tuple[Mapping[str, Any], ...],
) -> tuple[dict[str, Any], ...]:
    result = []
    for row in rows:
        content = {key: value for key, value in row.items() if key != "content_hash"}
        probe = content.get("event_time", content.get("source_event_time", ""))
        stored_utc = bool(re.fullmatch(
            r"\d{4}-\d\d-\d\d \d\d:\d\d:\d\d\.\d{6}(?:\d{3})?", str(probe)))
        canonical = _canonical_typed_content(table, content, stored_utc=stored_utc)
        if row.get("content_hash") != _digest(canonical):
            raise ValueError(f"{table} row hash differs")
        result.append({**canonical, "content_hash": row["content_hash"]})
    return tuple(result)


def _family_hash(rows: tuple[Mapping[str, Any], ...]) -> str:
    identities = sorted((str(UUID(str(row["record_id"]))),
                         str(row["content_hash"])) for row in rows)
    if len({identity for identity, _ in identities}) != len(identities):
        raise ValueError("Terminal V2 family repeats a record identity")
    return _digest(identities)


def project_terminal_v2_commit(
    prefix: CommittedPrefix, *, attempt_id: str, batch_id: str,
    account_ids: tuple[str, ...],
    source_cursor: str, status: str, committed_at: datetime,
    events: tuple[Mapping[str, Any], ...],
    transitions: tuple[Mapping[str, Any], ...],
    accounts: tuple[Mapping[str, Any], ...],
    positions: tuple[Mapping[str, Any], ...],
) -> dict[str, Any]:
    """Seal one complete terminal suffix without modifying a V1 commit."""
    if (not isinstance(prefix, CommittedPrefix) or prefix.status != "running"
            or not prefix.batch_ids or prefix.last_sequence < 1
            or status not in {"completed", "stopped", "failed"}
            or not events or not accounts or len(transitions) != 1
            or not account_ids or len(set(account_ids)) != len(account_ids)
            or not source_cursor or source_cursor.lstrip().startswith(("{", "["))
            or committed_at.tzinfo is None):
        raise ValueError("Terminal V2 seal needs a running prefix and complete suffix")
    attempt = str(UUID(attempt_id))
    batch = str(UUID(batch_id))
    run_month = date.fromisoformat(str(events[0]["event_month"])).replace(day=1).isoformat()
    if (tuple(int(row["sequence"]) for row in events)
            != tuple(range(prefix.last_sequence + 1,
                           prefix.last_sequence + len(events) + 1))
            or any(row["run_id"] != prefix.run_id or str(row["batch_id"]) != batch
                   or str(row["attempt_id"]) != attempt
                   or row["event_month"] != run_month
                   for row in events)):
        raise ValueError("Terminal V2 events do not extend the V1 sequence")
    last = events[-1]
    if ((last["category"], last["entity_type"], last["entity_id"])
            != ("lifecycle", "run", prefix.run_id)
            or transitions[0]["record_id"] != last["record_id"]
            or transitions[0]["status"] != status):
        raise ValueError("Terminal V2 lifecycle is not last")
    _verify_v1_rows("trading_event_v1", events)
    _verify_v1_rows("trading_run_transition_v1", transitions)
    accounts = _verify_rows(_ACCOUNT, accounts)
    positions = _verify_rows(_POSITION, positions)
    event_ids = {str(UUID(str(row["record_id"]))) for row in events}
    detail_ids = {str(UUID(str(row["record_id"]))) for row in (*accounts, *positions,
                                                               *transitions)}
    if (len(event_ids) != len(events) or event_ids != detail_ids
            or any(row["run_id"] != prefix.run_id or str(row["batch_id"]) != batch
                   or row["event_month"] != run_month
                   for row in (*accounts, *positions, *transitions))):
        raise ValueError("Terminal V2 details do not cover exactly the event suffix")
    account_by_record = {row["record_id"]: row for row in accounts}
    position_by_record = {row["record_id"]: row for row in positions}
    for event in events[:-1]:
        record_id = event["record_id"]
        if (event["category"], event["entity_type"]) == ("snapshot", "portfolio"):
            detail = account_by_record.get(record_id)
            if (detail is None or detail["account_id"] != event["account_id"]
                    or event["entity_id"] != event["account_id"]):
                raise ValueError("Terminal V2 account event differs from its detail")
        elif (event["category"], event["entity_type"]) == ("snapshot", "position"):
            detail = position_by_record.get(record_id)
            if (detail is None or detail["account_id"] != event["account_id"]
                    or event["entity_id"] != str(detail["conid"])):
                raise ValueError("Terminal V2 position event differs from its detail")
        else:
            raise ValueError("Terminal V2 suffix contains an unsupported event")
    snapshots = {row["snapshot_id"]: row for row in accounts}
    if len(snapshots) != len(accounts):
        raise ValueError("Terminal V2 repeats an account snapshot identity")
    if {row["account_id"] for row in accounts} != set(account_ids):
        raise ValueError("Terminal V2 account population differs from pinned run")
    for account in accounts:
        children = tuple(row for row in positions
                         if row["parent_snapshot_id"] == account["snapshot_id"])
        recover_snapshot_group(account, children)
    if any(row["parent_snapshot_id"] not in snapshots for row in positions):
        raise ValueError("Terminal V2 position has no account parent")
    content = {
        "run_id": prefix.run_id,
        "run_month": run_month,
        "attempt_id": attempt, "batch_id": batch,
        "prior_v1_batch_id": prefix.last_batch_id,
        "prior_v1_sequence": prefix.last_sequence,
        "first_sequence": prefix.last_sequence + 1,
        "last_sequence": int(last["sequence"]),
        "source_cursor": source_cursor, "status": status,
        "event_count": len(events), "event_hash": _family_hash(events),
        "run_transition_count": 1,
        "run_transition_hash": _family_hash(transitions),
        "account_count": len(accounts), "account_hash": _family_hash(accounts),
        "position_count": len(positions), "position_hash": _family_hash(positions),
        "committed_at": committed_at.astimezone(timezone.utc).isoformat(timespec="microseconds"),
    }
    return seal_v2_row(_COMMIT, content)


def load_terminal_v2_commit(
    client: Any, prefix: CommittedPrefix, *, account_ids: tuple[str, ...],
) -> dict[str, Any]:
    """Cold readback: require one exact suffix seal and rehash every fact."""
    if not isinstance(prefix, CommittedPrefix) or prefix.status != "running":
        raise ValueError("Terminal V2 readback requires a verified running V1 prefix")
    commits = _rows(client, "SELECT * FROM arte.trading_backtest_terminal_commit_v2 "
                    f"WHERE run_id={_literal(prefix.run_id)} FORMAT JSONEachRow")
    if len(commits) != 1:
        raise ValueError("Terminal V2 requires exactly one committed suffix")
    stored = commits[0]
    batch = str(UUID(str(stored["batch_id"])))
    rows = {}
    for table, key in (("trading_event_v1", "events"),
                       ("trading_run_transition_v1", "transitions"),
                       (_ACCOUNT, "accounts"), (_POSITION, "positions")):
        predicate = (f"run_id={_literal(prefix.run_id)}" if table in {_ACCOUNT, _POSITION}
                     else f"batch_id=toUUID({_literal(batch)})")
        rows[key] = tuple(_rows(client, f"SELECT * FROM arte.{table} "
                                 f"WHERE {predicate} FORMAT JSONEachRow"))
    calculated = project_terminal_v2_commit(
        prefix, attempt_id=stored["attempt_id"], batch_id=batch,
        account_ids=account_ids,
        source_cursor=stored["source_cursor"], status=stored["status"],
        committed_at=datetime.fromisoformat(str(stored["committed_at"]).replace("Z", "+00:00")),
        **rows,
    )
    if calculated != _verify_rows(_COMMIT, (stored,))[0]:
        raise ValueError("Terminal V2 commit differs from the verified suffix")
    return calculated
