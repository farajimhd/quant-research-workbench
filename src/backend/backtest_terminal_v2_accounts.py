"""Cold portfolio recovery anchored to a committed Backtest V2 terminal suffix.

This reader reuses the existing typed portfolio snapshot commit and V1 anchor
table. It does not publish snapshots, anchors, or journal rows.
"""
from __future__ import annotations

from datetime import datetime, timezone
from hashlib import sha256
import re
from typing import Any
from uuid import UUID

from src.backend.backtest_terminal_v2_fence import (
    _verify_v1_rows, load_terminal_v2_commit,
)
from src.trading_runtime.arte_journal_writer import (
    _canonical_typed_content, _literal, _rows,
    load_committed_prefix, load_typed_run_context,
)
from src.trading_runtime.arte_portfolio_snapshot import load_portfolio_snapshot
from src.trading_runtime.journal_contract import canonical_json


_ANCHOR = "trading_backtest_snapshot_anchor_v1"


def _instant(value: Any) -> datetime:
    text = str(value).replace("Z", "+00:00")
    if re.fullmatch(r"\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}\.\d{6}(?:\d{3})?", text):
        text += "+00:00"
    at = datetime.fromisoformat(text)
    if at.tzinfo is None:
        raise ValueError("Terminal V2 snapshot clock is naive")
    return at.astimezone(timezone.utc)


def load_terminal_v2_portfolio_accounts(
    client: Any, *, run_id: str,
) -> dict[str, dict[str, Any]]:
    """Verify full V1/V2 authority and every pinned account's anchored state."""
    context = load_typed_run_context(client, run_id)
    prefix = load_committed_prefix(client, run_id)
    accounts = tuple(context.get("account_ids") or ())
    if (context.get("mode") != "backtest"
            or prefix is None or prefix.status != "running"
            or not accounts or len(set(accounts)) != len(accounts)
            or any(not isinstance(account, str) or not account for account in accounts)):
        raise ValueError("Terminal V2 recovery lacks pinned Backtest account authority")
    seal = load_terminal_v2_commit(client, prefix, account_ids=accounts)
    batch_id = str(UUID(str(seal["batch_id"])))
    terminal = _rows(client, "SELECT * FROM arte.trading_event_v1 "
                     f"WHERE batch_id=toUUID({_literal(batch_id)}) "
                     f"AND sequence={int(seal['last_sequence'])} FORMAT JSONEachRow")
    if len(terminal) != 1:
        raise RuntimeError("Terminal V2 lifecycle event is missing or duplicated")
    event = _verify_v1_rows("trading_event_v1", tuple(terminal))[0]
    if ((event["category"], event["entity_type"], event["entity_id"])
            != ("lifecycle", "run", run_id)
            or int(event["sequence"]) != int(seal["last_sequence"])):
        raise RuntimeError("Terminal V2 lifecycle event differs from seal")
    terminal_at = _instant(event["event_time"])

    rows = _rows(client, f"SELECT * FROM arte.{_ANCHOR} "
                 f"WHERE run_id={_literal(run_id)} FORMAT JSONEachRow")
    if len(rows) != len(accounts):
        raise RuntimeError("Terminal V2 anchors differ from pinned account count")
    anchors: dict[str, dict[str, Any]] = {}
    for row in rows:
        content = {key: value for key, value in row.items()
                   if key != "content_hash"}
        canonical = _canonical_typed_content(_ANCHOR, content, stored_utc=True)
        if sha256(canonical_json(canonical).encode("utf-8")).hexdigest() != row.get("content_hash"):
            raise RuntimeError("Terminal V2 anchor row hash differs")
        account_id = canonical["account_id"]
        if account_id in anchors or account_id not in accounts:
            raise RuntimeError("Terminal V2 anchor account population differs")
        if (canonical["run_id"] != run_id
                or canonical["batch_id"] != batch_id
                or int(canonical["last_sequence"]) != int(seal["last_sequence"])
                or int(canonical["state_revision"]) != int(seal["last_sequence"])
                or canonical["anchor_month"] != seal["run_month"]
                or _instant(canonical["anchored_at"]) != terminal_at):
            raise RuntimeError("Terminal V2 anchor differs from terminal sequence")
        anchors[account_id] = canonical
    if set(anchors) != set(accounts):
        raise RuntimeError("Terminal V2 anchors omit a pinned account")

    result = {}
    for account_id in sorted(accounts):
        anchor = anchors[account_id]
        snapshot = load_portfolio_snapshot(
            client, run_id=run_id, account_id=account_id,
            state_revision=int(seal["last_sequence"]))
        if (snapshot is None
                or snapshot.get("state_hash") != anchor["snapshot_hash"]
                or snapshot.get("state_revision") != int(seal["last_sequence"])
                or _instant(snapshot.get("snapshot_at")) != terminal_at):
            raise RuntimeError("Terminal V2 anchor differs from committed portfolio state")
        result[account_id] = snapshot
    if (load_committed_prefix(client, run_id) != prefix
            or load_typed_run_context(client, run_id) != context
            or load_terminal_v2_commit(client, prefix, account_ids=accounts) != seal):
        raise RuntimeError("Terminal V2 recovery authority changed during audit")
    return result
