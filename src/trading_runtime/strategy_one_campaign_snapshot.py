"""Normalized V4 campaign ownership at a committed Strategy 1 boundary.

The root seals an empty ownership set as well as a nonempty one. Each child is
one active ticker/book owner; no command cache, JSON, or broker market row is
stored here. The ordered journal publisher, not Backtest execution, owns SQL.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from hashlib import sha256
import json
from typing import Any, Mapping, Protocol, Sequence
from uuid import NAMESPACE_URL, UUID, uuid5

from src.trading_runtime.arte_journal_schema import TableContract
from src.trading_runtime.keeper_session import ManagedKeeperSession
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


@dataclass(frozen=True, slots=True)
class CampaignSnapshotHead:
    run_id: str
    checkpoint_sequence: int
    journal_batch_id: str
    snapshot_hash: str
    keeper_version: int


class CampaignSnapshotHeadReader(Protocol):
    def read_head(self, *, run_id: str) -> CampaignSnapshotHead: ...


class ManagedCampaignSnapshotHeadReader:
    """Read only the Keeper-selected normalized campaign checkpoint."""

    def __init__(self, session: ManagedKeeperSession) -> None:
        if not isinstance(session, ManagedKeeperSession):
            raise TypeError("Campaign head requires a managed Keeper session")
        self._session = session

    @staticmethod
    def path(run_id: str) -> str:
        if (type(run_id) is not str or not run_id
                or any(char in run_id for char in "\r\n\x00")):
            raise ValueError("Campaign head run identity is invalid")
        return ("/trading/strategy-one-campaign-snapshot/v1/"
                + sha256(run_id.encode()).hexdigest() + "/head")

    def read_head(self, *, run_id: str) -> CampaignSnapshotHead:
        session, client = self._session, self._session.client
        if not session.writable or client.client_id is None:
            raise RuntimeError("Campaign Keeper session is unavailable")
        generation, client_id = session._generation, client.client_id
        try:
            raw, stat = client.get(self.path(run_id))
            fields = raw.decode("utf-8").split("\n")
            if (len(fields) != 5 or fields[:2] != ["1", run_id]
                    or str(int(fields[2])) != fields[2] or int(fields[2]) < 1
                    or str(UUID(fields[3])) != fields[3]
                    or len(fields[4]) != 64
                    or any(char not in "0123456789abcdef" for char in fields[4])
                    or type(stat.version) is not int or stat.version < 0):
                raise ValueError
            head = CampaignSnapshotHead(
                run_id, int(fields[2]), fields[3], fields[4], stat.version)
        except Exception as exc:
            raise ValueError("Campaign Keeper head missing or corrupt") from exc
        if (not session.writable or session._generation != generation
                or client.client_id != client_id):
            raise RuntimeError("Campaign Keeper session changed during read")
        return head


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


def load_campaign_snapshot(
    client: Any, *, run_id: str, checkpoint_sequence: int,
) -> CampaignSnapshotRows:
    """SELECT bounded exact rows; no returned row is trusted without its seal."""
    from src.backend.backtest_market_data import _literal, assert_select_only

    if (type(run_id) is not str or not run_id or "\x00" in run_id
            or type(checkpoint_sequence) is not int or checkpoint_sequence < 1
            or not callable(getattr(client, "execute", None))):
        raise ValueError("Campaign recovery needs an exact run and sequence")
    where = (f"run_id={_literal(run_id)} "
             f"AND checkpoint_sequence={checkpoint_sequence}")
    root_sql = assert_select_only(
        f"SELECT * FROM arte.{ROOT.name} WHERE {where} "
        "LIMIT 2 FORMAT JSONEachRow")
    roots = [json.loads(line) for line in client.execute(root_sql).splitlines()
             if line.strip()]
    if (len(roots) != 1
            or type(roots[0].get("owner_count")) is not int
            or not 0 <= roots[0]["owner_count"] <= 100_000):
        raise RuntimeError("Campaign checkpoint root is missing, duplicate, or unbounded")
    root = roots[0]
    child_sql = assert_select_only(
        f"SELECT * FROM arte.{OWNER.name} WHERE {where} "
        f"AND snapshot_id=toUUID({_literal(root['snapshot_id'])}) "
        f"LIMIT {int(root['owner_count']) + 1} FORMAT JSONEachRow")
    owners = tuple(json.loads(line) for line in client.execute(child_sql).splitlines()
                   if line.strip())
    return verify_campaign_snapshot(CampaignSnapshotRows(root, owners))


def load_attested_campaign_snapshot(
    client: Any, keeper: CampaignSnapshotHeadReader, *, run_id: str,
    checkpoint_sequence: int,
    first_price_source=None, fixed_lot_resume=None,
) -> CampaignSnapshotRows:
    """Require a stable running V4 prefix and its exact completed cursor."""
    from src.trading_runtime.arte_journal_commit_v4 import load_verified_v4_prefix
    from src.trading_runtime.arte_journal_projection import load_latest_backtest_cursor

    if fixed_lot_resume is not None:
        from src.backend.backtest_fixed_structural_lot_resume import require_fixed_structural_lot_resume
        binding = require_fixed_structural_lot_resume(fixed_lot_resume, run_id)
        if first_price_source is not None and first_price_source is not binding.source.price_authority:
            raise ValueError('Selected resume received conflicting first-price authority')
        prefix = binding.prefix(client, run_id)
    else:
        prefix = load_verified_v4_prefix(client, run_id, **({} if first_price_source is None else {'first_price_source': first_price_source}))
    if (prefix is None or prefix.status != "running"
            or prefix.last_sequence != checkpoint_sequence):
        raise RuntimeError("Campaign recovery lacks its committed running V4 prefix")
    cursor = load_latest_backtest_cursor(client, prefix)
    rows = load_campaign_snapshot(
        client, run_id=run_id, checkpoint_sequence=checkpoint_sequence)
    root = rows.snapshot
    selected = keeper.read_head(run_id=run_id)
    if (not isinstance(cursor, dict)
            or not isinstance(selected, CampaignSnapshotHead)
            or selected.run_id != run_id
            or selected.checkpoint_sequence != checkpoint_sequence
            or selected.journal_batch_id != root["journal_batch_id"]
            or selected.snapshot_hash != root["content_hash"]
            or cursor.get("run_id") != run_id
            or cursor.get("event_sequence") != checkpoint_sequence
            or cursor.get("batch_id") != root["journal_batch_id"]
            or cursor.get("session_date") != root["session_date"]
            or cursor.get("boundary_ms") != root["boundary_ms"]
            or prefix.last_batch_id != root["journal_batch_id"]
            or (require_fixed_structural_lot_resume(fixed_lot_resume, run_id).prefix(client, run_id)
                if fixed_lot_resume is not None else load_verified_v4_prefix(client, run_id, **({} if first_price_source is None else {'first_price_source': first_price_source}))) != prefix
            or keeper.read_head(run_id=run_id) != selected):
        raise RuntimeError("Campaign checkpoint differs from committed market cursor")
    return rows


def publish_campaign_snapshot(
    client: Any, session: ManagedKeeperSession, rows: CampaignSnapshotRows,
    *, first_price_source=None,
) -> CampaignSnapshotHead:
    """Background-writer-only children-first publication and Keeper selection."""
    from src.trading_runtime.arte_journal_commit_v4 import load_writer_v4_snapshot_prefix
    from src.trading_runtime.arte_journal_projection import load_latest_backtest_cursor
    from src.trading_runtime.arte_journal_writer import _insert
    from src.trading_runtime.arte_typed_insert_dispatch import TypedInsertDispatch

    if (not isinstance(session, ManagedKeeperSession) or not session.writable
            or getattr(client, "typed_insert_strict", False) is not True
            or not isinstance(getattr(client, "typed_insert_dispatch", None),
                              TypedInsertDispatch)
            or client.typed_insert_dispatch.keeper is not session.client):
        raise RuntimeError("Campaign publication lacks a fenced journal writer")
    expected = verify_campaign_snapshot(rows)
    root = expected.snapshot
    run_id, sequence, batch_id = (
        root["run_id"], root["checkpoint_sequence"], root["journal_batch_id"])
    prefix = load_writer_v4_snapshot_prefix(client, run_id, **({} if first_price_source is None else {'first_price_source': first_price_source}))
    if (prefix is None or prefix.status != "running"
            or prefix.last_sequence != sequence
            or prefix.last_batch_id != batch_id):
        raise RuntimeError("Campaign publication lacks its exact V4 cursor")
    cursor = load_latest_backtest_cursor(client, prefix)
    if (not isinstance(cursor, dict)
            or cursor.get("run_id") != run_id
            or cursor.get("event_sequence") != sequence
            or cursor.get("batch_id") != batch_id
            or cursor.get("session_date") != root["session_date"]
            or cursor.get("boundary_ms") != root["boundary_ms"]):
        raise RuntimeError("Campaign publication differs from completed market cursor")
    reader = ManagedCampaignSnapshotHeadReader(session)
    if session.client.exists(reader.path(run_id)) is None:
        previous = None
    else:
        previous = reader.read_head(run_id=run_id)
        if previous.checkpoint_sequence == sequence:
            if (previous.journal_batch_id != batch_id
                    or previous.snapshot_hash != root["content_hash"]
                    or load_attested_campaign_snapshot(
                        client, reader, run_id=run_id,
                        checkpoint_sequence=sequence,
                        **({} if first_price_source is None else {'first_price_source': first_price_source})) != expected):
                raise RuntimeError("Campaign repeat differs from selected snapshot")
            return previous
        if previous.checkpoint_sequence > sequence:
            raise RuntimeError("Campaign publication would rewind Keeper head")
    operations = []
    for table, family in ((OWNER, expected.owners), (ROOT, (root,))):
        if not family:
            continue
        token = (f"campaign-state:{run_id}:{sequence}:"
                 f"{root['content_hash']}:{table.name}")
        _insert(client, table.name, family, token,
                dispatch_sequence=sequence, dispatch_batch_id=batch_id,
                dispatch_campaign_snapshot_hash=root["content_hash"])
        operations.append((table.name, token))
    if load_campaign_snapshot(
            client, run_id=run_id, checkpoint_sequence=sequence) != expected:
        raise RuntimeError("Campaign readback differs from captured state")
    for table, token in operations:
        client.typed_insert_dispatch.seal_verified_operation(
            run_id=run_id, table=table, token=token, batch_id=batch_id,
            batch_last_sequence=sequence, campaign_snapshot=True)
    client.typed_insert_dispatch.compact_verified_campaign_snapshot(
        run_id=run_id, batch_id=batch_id, last_sequence=sequence,
        snapshot_hash=root["content_hash"], operations=tuple(operations),
        previous=previous)
    selected = reader.read_head(run_id=run_id)
    if (selected.checkpoint_sequence != sequence
            or selected.journal_batch_id != batch_id
            or selected.snapshot_hash != root["content_hash"]):
        raise RuntimeError("Campaign Keeper readback differs from captured state")
    return selected
