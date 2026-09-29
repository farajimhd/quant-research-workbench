"""Normalized V4 campaign ownership at a committed Strategy 1 boundary.

The root seals an empty ownership set as well as a nonempty one. Each child is
one active ticker/book owner; no command cache, JSON, or broker market row is
stored here. The ordered journal publisher, not Backtest execution, owns SQL.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Any, Mapping, Sequence
from uuid import NAMESPACE_URL, UUID, uuid5

from src.trading_runtime.arte_journal_schema import TableContract
from src.trading_runtime.strategy_one_protection_snapshot import _digest


ROOT = TableContract(
    "trading_strategy_one_campaign_snapshot_v1",
    (("snapshot_id", "UUID"), ("run_id", "String"),
     ("snapshot_month", "Date"), ("session_date", "Date"),
     ("checkpoint_sequence", "UInt64"), ("boundary_ms", "UInt32"),
     ("journal_batch_id", "UUID"), ("owner_count", "UInt32"),
     ("owner_hash", "FixedString(64)"), ("content_hash", "FixedString(64)")),
    "toYYYYMM(snapshot_month)", "run_id, checkpoint_sequence, snapshot_id",
)
OWNER = TableContract(
    "trading_strategy_one_campaign_owner_v1",
    (("snapshot_id", "UUID"), ("run_id", "String"),
     ("snapshot_month", "Date"), ("checkpoint_sequence", "UInt64"),
     ("resource_id", "String"), ("session_key", "String"),
     ("owner_id", "String"), ("state", "LowCardinality(String)"),
     ("epoch", "UInt64"), ("content_hash", "FixedString(64)")),
    "toYYYYMM(snapshot_month)",
    "run_id, checkpoint_sequence, resource_id, session_key",
)
TABLES = (ROOT, OWNER)


@dataclass(frozen=True, slots=True)
class CampaignSnapshotRows:
    snapshot: dict[str, Any]
    owners: tuple[dict[str, Any], ...]


def project_campaign_snapshot(
    *, run_id: str, session_date: date, checkpoint_sequence: int,
    boundary_ms: int, journal_batch_id: str,
    ownership: Sequence[Mapping[str, Any]],
) -> CampaignSnapshotRows:
    """Encode exact in-memory owners; wall-clock ``updated_at`` is not causal."""
    try:
        UUID(journal_batch_id)
    except (TypeError, ValueError) as exc:
        raise ValueError("Campaign snapshot needs a committed V4 batch") from exc
    if (type(run_id) is not str or not run_id
            or not isinstance(session_date, date)
            or type(checkpoint_sequence) is not int or checkpoint_sequence < 1
            or type(boundary_ms) is not int
            or not 0 < boundary_ms <= 57_600_000 or boundary_ms % 100
            or not isinstance(ownership, (tuple, list))):
        raise ValueError("Campaign snapshot needs a pinned completed boundary")
    snapshot_id = str(uuid5(
        NAMESPACE_URL, f"strategy-one-campaign-v1:{run_id}:{checkpoint_sequence}"))
    month = session_date.replace(day=1).isoformat()
    children = []
    seen = set()
    required = {"resource_id", "session_key", "owner_id", "state", "epoch"}
    for source in ownership:
        if not isinstance(source, Mapping) or not required <= source.keys():
            raise ValueError("Campaign owner is missing a scalar identity")
        identity = (source["resource_id"], source["session_key"])
        if (any(type(value) is not str or not value for value in (*identity, source["owner_id"]))
                or identity in seen or source["state"] not in {"reserved", "confirmed"}
                or type(source["epoch"]) is not int or source["epoch"] < 1):
            raise ValueError("Campaign snapshot has an invalid or duplicate owner")
        seen.add(identity)
        child = dict(snapshot_id=snapshot_id, run_id=run_id,
                     snapshot_month=month, checkpoint_sequence=checkpoint_sequence,
                     resource_id=identity[0], session_key=identity[1],
                     owner_id=source["owner_id"], state=source["state"],
                     epoch=source["epoch"])
        children.append({**child, "content_hash": _digest(child)})
    children.sort(key=lambda row: (row["resource_id"], row["session_key"]))
    root = dict(snapshot_id=snapshot_id, run_id=run_id,
                snapshot_month=month, session_date=session_date.isoformat(),
                checkpoint_sequence=checkpoint_sequence,
                boundary_ms=boundary_ms, journal_batch_id=str(UUID(journal_batch_id)),
                owner_count=len(children),
                owner_hash=_digest([row["content_hash"] for row in children]))
    return CampaignSnapshotRows(
        {**root, "content_hash": _digest(root)}, tuple(children))


def verify_campaign_snapshot(rows: CampaignSnapshotRows) -> CampaignSnapshotRows:
    """Reject missing children, changed owners, or an unsealed empty set."""
    if not isinstance(rows, CampaignSnapshotRows):
        raise TypeError("Campaign recovery requires normalized snapshot rows")
    root = rows.snapshot
    try:
        expected = project_campaign_snapshot(
            run_id=root["run_id"], session_date=date.fromisoformat(root["session_date"]),
            checkpoint_sequence=root["checkpoint_sequence"],
            boundary_ms=root["boundary_ms"],
            journal_batch_id=root["journal_batch_id"], ownership=rows.owners)
    except (KeyError, TypeError, ValueError) as exc:
        raise RuntimeError("Campaign snapshot contract is invalid") from exc
    if expected != rows:
        raise RuntimeError("Campaign snapshot rows differ from their committed seal")
    return rows
