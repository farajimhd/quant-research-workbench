"""Typed terminal Backtest link from the event prefix to account recovery.

A portfolio snapshot's own commit proves its contents, but not which market
boundary it represents. This anchor is written last and references both the
terminal event batch and the exact account state revision/hash. Review ignores
unanchored snapshots; interrupted publication is retryable by identity.
"""
from __future__ import annotations

from datetime import timezone
from hashlib import sha256
from typing import Any

from src.trading_runtime.arte_journal_writer import (
    CommittedPrefix, _canonical_typed_content, _insert, _literal, _rows,
    load_committed_prefix, load_typed_run_context, typed_row,
)
from src.trading_runtime.arte_portfolio_snapshot import (
    CapturedPortfolioSnapshot, load_portfolio_snapshot,
    prepare_captured_portfolio_snapshot, publish_prepared_portfolio_snapshot,
)
from src.trading_runtime.journal_contract import canonical_json
from src.trading_runtime.arte_journal_commit_v4 import (
    V4CommittedPrefix, load_verified_v4_prefix,
)


_TABLE = "trading_backtest_snapshot_anchor_v1"


def _stored(client: Any, run_id: str, account_id: str) -> list[dict[str, Any]]:
    rows = _rows(client,
        f"SELECT * FROM arte.{_TABLE} WHERE run_id={_literal(run_id)} "
        f"AND account_id={_literal(account_id)} FORMAT JSONEachRow")
    result = []
    for row in rows:
        content = {key: value for key, value in row.items() if key != "content_hash"}
        canonical = _canonical_typed_content(_TABLE, content, stored_utc=True)
        digest = sha256(canonical_json(canonical).encode("utf-8")).hexdigest()
        if digest != str(row["content_hash"]):
            raise RuntimeError("Backtest snapshot anchor differs from its hash")
        result.append(canonical)
    return result


def load_terminal_backtest_snapshot(
    client: Any, prefix: CommittedPrefix | V4CommittedPrefix, *, account_id: str,
) -> dict[str, Any]:
    """Require one anchor to this exact terminal prefix and verified snapshot."""
    if (not isinstance(prefix, (CommittedPrefix, V4CommittedPrefix)) or not account_id
            or prefix.status not in {"completed", "stopped", "failed"}
            or not prefix.batch_ids):
        raise ValueError("Backtest snapshot requires a terminal committed prefix")
    anchors = _stored(client, prefix.run_id, account_id)
    if len(anchors) != 1:
        raise RuntimeError("Backtest account lacks one terminal snapshot anchor")
    anchor = anchors[0]
    if (str(anchor["run_id"]) != prefix.run_id
            or str(anchor["account_id"]) != account_id
            or str(anchor["batch_id"]) != prefix.last_batch_id
            or int(anchor["last_sequence"]) != prefix.last_sequence):
        raise RuntimeError("Backtest snapshot anchor differs from terminal events")
    snapshot = load_portfolio_snapshot(
        client, run_id=prefix.run_id, account_id=account_id,
        state_revision=int(anchor["state_revision"]))
    if snapshot is None or snapshot["state_hash"] != anchor["snapshot_hash"]:
        raise RuntimeError("Backtest snapshot anchor differs from recovery state")
    return snapshot


def publish_terminal_backtest_snapshot(
    client: Any, prefix: CommittedPrefix | V4CommittedPrefix,
    captured: CapturedPortfolioSnapshot,
) -> str:
    """Worker-only, idempotent publication; the typed anchor commits last."""
    if not isinstance(captured, CapturedPortfolioSnapshot):
        raise TypeError("Terminal Backtest requires a typed portfolio capture")
    context = _verify_current_prefix(client, prefix)
    if captured.account_id not in context["account_ids"]:
        raise ValueError("Backtest snapshot account is not pinned to the run")
    return _publish_verified_snapshot(client, prefix, captured)


def publish_terminal_backtest_snapshots(
    client: Any, prefix: CommittedPrefix | V4CommittedPrefix,
    captures: tuple[CapturedPortfolioSnapshot, ...],
) -> tuple[str, ...]:
    """Verify the terminal prefix once, then anchor every pinned account."""
    if (not captures
            or any(not isinstance(row, CapturedPortfolioSnapshot) for row in captures)
            or len({row.account_id for row in captures}) != len(captures)):
        raise ValueError("Terminal Backtest snapshots require distinct accounts")
    context = _verify_current_prefix(client, prefix)
    if set(context["account_ids"]) != {row.account_id for row in captures}:
        raise ValueError("Terminal Backtest snapshots omit a pinned account")
    return tuple(_publish_verified_snapshot(client, prefix, row) for row in captures)


def _verify_current_prefix(
    client: Any, prefix: CommittedPrefix | V4CommittedPrefix,
) -> dict[str, Any]:
    if not isinstance(prefix, (CommittedPrefix, V4CommittedPrefix)):
        raise ValueError("Backtest snapshot requires a committed prefix")
    verified = (load_verified_v4_prefix(client, prefix.run_id)
                if isinstance(prefix, V4CommittedPrefix) else
                load_committed_prefix(client, prefix.run_id))
    if verified != prefix or verified.status not in {"completed", "stopped", "failed"}:
        raise RuntimeError("Backtest snapshot needs the current terminal prefix")
    context = load_typed_run_context(client, prefix.run_id)
    if context["mode"] != "backtest":
        raise ValueError("Terminal snapshot anchor accepts Backtest runs only")
    return context


def _publish_verified_snapshot(
    client: Any, prefix: CommittedPrefix | V4CommittedPrefix,
    captured: CapturedPortfolioSnapshot,
) -> str:
    if (not isinstance(prefix, (CommittedPrefix, V4CommittedPrefix))
            or not isinstance(captured, CapturedPortfolioSnapshot)
            or captured.run_id != prefix.run_id):
        raise ValueError("Backtest snapshot capture differs from its run")
    prepared = prepare_captured_portfolio_snapshot(captured)
    snapshot_hash = publish_prepared_portfolio_snapshot(client, prepared)
    row = typed_row(_TABLE, {
        "run_id": prefix.run_id,
        "anchor_month": captured.snapshot_at.astimezone(timezone.utc).date().replace(
            day=1).isoformat(),
        "account_id": captured.account_id,
        "state_revision": captured.state_revision,
        "batch_id": prefix.last_batch_id,
        "last_sequence": prefix.last_sequence,
        "snapshot_hash": snapshot_hash,
        "anchored_at": captured.snapshot_at.astimezone(timezone.utc).isoformat(),
    })
    expected = _canonical_typed_content(
        _TABLE, {key: value for key, value in row.items() if key != "content_hash"})
    existing = _stored(client, prefix.run_id, captured.account_id)
    if existing and existing != [expected]:
        raise RuntimeError("Backtest snapshot anchor conflicts with existing identity")
    if not existing:
        _insert(client, _TABLE, (row,),
                f"backtest-snapshot:{prefix.run_id}:{captured.account_id}:"
                f"{prefix.last_batch_id}",
                dispatch_batch_id=prefix.last_batch_id,
                dispatch_sequence=prefix.last_sequence,
                dispatch_terminal_account_id=captured.account_id)
    loaded = load_terminal_backtest_snapshot(
        client, prefix, account_id=captured.account_id)
    if loaded["state_hash"] != snapshot_hash:
        raise RuntimeError("Backtest snapshot anchor did not become durable")
    dispatch = getattr(client, "typed_insert_dispatch", None)
    if dispatch is not None:
        token = (f"backtest-snapshot:{prefix.run_id}:{captured.account_id}:"
                 f"{prefix.last_batch_id}")
        dispatch.seal_verified_operation(
            run_id=prefix.run_id, table=_TABLE, token=token, required=False,
            batch_id=prefix.last_batch_id,
            batch_last_sequence=prefix.last_sequence, terminal=True)
        dispatch.compact_verified_terminal_anchor(
            run_id=prefix.run_id, account_id=captured.account_id,
            batch_id=prefix.last_batch_id, last_sequence=prefix.last_sequence,
            anchor_hash=row["content_hash"], snapshot_hash=snapshot_hash,
            token=token)
    return snapshot_hash
