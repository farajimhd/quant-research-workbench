"""Inactive, injected-client publisher for a sealed Backtest terminal suffix.

This is not connected to the controller or active writer. An operator must
provision and verify the staged V2 tables before any production use.
"""
from __future__ import annotations

from datetime import datetime, timezone
from hashlib import sha256
from typing import Any, Mapping
from uuid import UUID

from src.backend.backtest_terminal_v2_fence import (
    _ACCOUNT, _COMMIT, _POSITION, _verify_rows, _verify_v1_rows,
    load_terminal_v2_commit, project_terminal_v2_commit,
)
from src.trading_runtime.arte_journal_writer import (
    V2CommittedPrefix, _canonical_typed_content, _insert, _literal, _rows,
    load_committed_prefix, typed_row,
)
from src.trading_runtime.arte_backtest_snapshot_anchor import _stored as _stored_anchors
from src.trading_runtime.arte_portfolio_snapshot import (
    CapturedPortfolioSnapshot, load_portfolio_snapshot,
    prepare_captured_portfolio_snapshot, publish_prepared_portfolio_snapshot,
)
from src.trading_runtime.journal_contract import canonical_json


_FACTS = (
    ("trading_event_v1", "events"),
    ("trading_run_transition_v1", "transitions"),
    (_ACCOUNT, "accounts"),
    (_POSITION, "positions"),
)
_V2_TABLES = {_ACCOUNT, _POSITION, _COMMIT}


def _read_family(client: Any, table: str, *, run_id: str, batch_id: str) -> tuple[dict[str, Any], ...]:
    predicate = (f"run_id={_literal(run_id)}" if table in _V2_TABLES
                 else f"batch_id=toUUID({_literal(batch_id)})")
    return tuple(_rows(client, f"SELECT * FROM arte.{table} "
                               f"WHERE {predicate} FORMAT JSONEachRow"))


def _checked_rows(table: str, rows: tuple[Mapping[str, Any], ...]) -> tuple[dict[str, Any], ...]:
    if table in _V2_TABLES:
        return _verify_rows(table, rows)
    return _verify_v1_rows(table, rows)


def _insert_missing(
    client: Any, table: str, *, run_id: str, batch_id: str,
    expected: tuple[Mapping[str, Any], ...],
) -> None:
    """One family at a time; conflicting or duplicated prior rows halt writes."""
    expected_rows = _checked_rows(table, expected)
    expected_by_id = {str(UUID(str(row["record_id"]))): row for row in expected_rows}
    if len(expected_by_id) != len(expected_rows):
        raise ValueError(f"{table} repeats an expected record ID")
    existing = _read_family(client, table, run_id=run_id, batch_id=batch_id)
    actual_rows = _checked_rows(table, existing)
    actual_by_id = {str(UUID(str(row["record_id"]))): row for row in actual_rows}
    if (len(actual_by_id) != len(actual_rows)
            or any(expected_by_id.get(record_id) != row
                   for record_id, row in actual_by_id.items())):
        raise RuntimeError(f"{table} conflicts with the terminal suffix")
    missing = tuple(row for record_id, row in expected_by_id.items()
                    if record_id not in actual_by_id)
    if missing:
        columns = tuple(expected_rows[0])
        body = "\n".join(canonical_json(row) for row in missing)
        token = sha256(canonical_json(sorted(
            (str(row["record_id"]), str(row["content_hash"]))
            for row in missing)).encode("utf-8")).hexdigest()
        client.execute(
            f"INSERT INTO arte.{table} ({','.join(columns)}) "
            "SETTINGS async_insert=1,wait_for_async_insert=1,insert_deduplicate=1,"
            f"insert_deduplication_token={_literal('terminal-v2:' + batch_id + ':' + table + ':' + token)} "
            f"FORMAT JSONEachRow\n{body}"
        )
    readback = _checked_rows(table, _read_family(
        client, table, run_id=run_id, batch_id=batch_id))
    if (len(readback) != len(expected_rows)
            or {str(UUID(str(row["record_id"]))): row for row in readback}
            != expected_by_id):
        raise RuntimeError(f"{table} did not become durable exactly")


def publish_terminal_v2_suffix(
    client: Any, prefix: V2CommittedPrefix, *, account_ids: tuple[str, ...],
    attempt_id: str, batch_id: str, source_cursor: str, status: str,
    committed_at: datetime, events: tuple[Mapping[str, Any], ...],
    transitions: tuple[Mapping[str, Any], ...],
    accounts: tuple[Mapping[str, Any], ...],
    positions: tuple[Mapping[str, Any], ...],
    portfolio_captures: tuple[CapturedPortfolioSnapshot, ...] | None = None,
) -> dict[str, Any]:
    """Publish exact typed facts, then the V2 terminal commit last.

    The passed prefix must already be a whole-run verified V2 prefix. This
    module deliberately does not create tables or open a ClickHouse client.
    """
    if load_committed_prefix(client, prefix.run_id, journal_profile="backtest_v2") != prefix:
        raise RuntimeError("Terminal V2 publication lost its verified V2 prefix")
    expected = project_terminal_v2_commit(
        prefix, account_ids=account_ids, attempt_id=attempt_id,
        batch_id=batch_id, source_cursor=source_cursor, status=status,
        committed_at=committed_at, events=events, transitions=transitions,
        accounts=accounts, positions=positions,
    )
    families = dict(events=events, transitions=transitions,
                    accounts=accounts, positions=positions)
    for table, name in _FACTS:
        _insert_missing(client, table, run_id=prefix.run_id, batch_id=batch_id,
                        expected=families[name])
    if portfolio_captures is not None:
        _publish_portfolio_anchors(
            client, prefix=prefix, seal=expected, account_ids=account_ids,
            events=events, captures=portfolio_captures)
    existing = _read_family(client, _COMMIT, run_id=prefix.run_id, batch_id=batch_id)
    checked = _verify_rows(_COMMIT, existing)
    if checked and (len(checked) != 1 or checked[0] != expected):
        raise RuntimeError("Terminal V2 commit conflicts with an existing seal")
    if not checked:
        if load_committed_prefix(client, prefix.run_id, journal_profile="backtest_v2") != prefix:
            raise RuntimeError("Terminal V2 publication lost its verified V2 prefix")
        columns = tuple(expected)
        client.execute(
            f"INSERT INTO arte.{_COMMIT} ({','.join(columns)}) "
            "SETTINGS async_insert=1,wait_for_async_insert=1,insert_deduplicate=1,"
            f"insert_deduplication_token={_literal('terminal-v2:' + batch_id + ':commit')} "
            f"FORMAT JSONEachRow\n{canonical_json(expected)}"
        )
    return load_terminal_v2_commit(client, prefix, account_ids=account_ids)


def _publish_portfolio_anchors(
    client: Any, *, prefix: V2CommittedPrefix, seal: Mapping[str, Any],
    account_ids: tuple[str, ...], events: tuple[Mapping[str, Any], ...],
    captures: tuple[CapturedPortfolioSnapshot, ...],
) -> None:
    """Worker-only recovery publication; anchors precede the terminal seal."""
    if (not captures or any(type(row) is not CapturedPortfolioSnapshot for row in captures)
            or len({row.account_id for row in captures}) != len(captures)
            or {row.account_id for row in captures} != set(account_ids)):
        raise ValueError("Terminal V2 portfolio captures differ from pinned accounts")
    terminal = _verify_v1_rows("trading_event_v1", (events[-1],))[0]
    terminal_at = datetime.fromisoformat(str(terminal["event_time"]).replace("Z", "+00:00"))
    if terminal_at.tzinfo is None:
        terminal_at = terminal_at.replace(tzinfo=timezone.utc)
    terminal_at = terminal_at.astimezone(timezone.utc)
    prepared = []
    for capture in captures:
        if (capture.run_id != prefix.run_id
                or capture.state_revision != int(seal["last_sequence"])
                or capture.snapshot_at.astimezone(timezone.utc) != terminal_at):
            raise ValueError("Terminal V2 portfolio capture differs from lifecycle")
        prepared.append(prepare_captured_portfolio_snapshot(capture))
    # Check all prior anchors before making any portfolio writes.
    for capture in captures:
        existing = _stored_anchors(client, prefix.run_id, capture.account_id)
        if len(existing) > 1 or (existing and (
                existing[0]["batch_id"] != str(UUID(str(seal["batch_id"])))
                or int(existing[0]["last_sequence"]) != int(seal["last_sequence"])
                or int(existing[0]["state_revision"]) != int(seal["last_sequence"]))):
            raise RuntimeError("Terminal V2 portfolio anchor conflicts with suffix")
    for capture, image in sorted(zip(captures, prepared), key=lambda pair: pair[0].account_id):
        digest = publish_prepared_portfolio_snapshot(client, image)
        anchor = typed_row("trading_backtest_snapshot_anchor_v1", {
            "run_id": prefix.run_id, "anchor_month": seal["run_month"],
            "account_id": capture.account_id,
            "state_revision": int(seal["last_sequence"]),
            "batch_id": seal["batch_id"],
            "last_sequence": int(seal["last_sequence"]),
            "snapshot_hash": digest, "anchored_at": capture.snapshot_at,
        })
        expected_anchor = _canonical_typed_content(
            "trading_backtest_snapshot_anchor_v1",
            {key: value for key, value in anchor.items() if key != "content_hash"})
        existing = _stored_anchors(client, prefix.run_id, capture.account_id)
        if existing and existing != [expected_anchor]:
            raise RuntimeError("Terminal V2 portfolio anchor conflicts with snapshot")
        if not existing:
            _insert(client, "trading_backtest_snapshot_anchor_v1", (anchor,),
                    f"terminal-v2:{seal['batch_id']}:anchor:{capture.account_id}")
        if (_stored_anchors(client, prefix.run_id, capture.account_id)
                != [expected_anchor]):
            raise RuntimeError("Terminal V2 portfolio anchor did not become durable")
        loaded = load_portfolio_snapshot(
            client, run_id=prefix.run_id, account_id=capture.account_id,
            state_revision=int(seal["last_sequence"]))
        if loaded is None or loaded["state_hash"] != digest:
            raise RuntimeError("Terminal V2 portfolio snapshot did not become durable")


def audit_terminal_v2_run(
    client: Any, *, run_id: str, account_ids: tuple[str, ...],
) -> dict[str, Any]:
    """Cold whole-run V2 chain verification followed by the V2 terminal seal."""
    prefix = load_committed_prefix(client, run_id, journal_profile="backtest_v2")
    if prefix is None or prefix.status != "running":
        raise RuntimeError("Terminal V2 audit lacks a verified running V2 prefix")
    return load_terminal_v2_commit(client, prefix, account_ids=account_ids)
