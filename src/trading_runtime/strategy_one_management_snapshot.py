"""Normalized Strategy 1 manager checkpoint rows and fenced publication.

The protection snapshot owns position geometry. This family adds only the
submitted entry sources and unconfirmed break witnesses needed to restore the
manager. The ordered journal worker writes children and protection first,
then selects the verified seal in Keeper; Backtest execution never writes
these tables directly.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from hashlib import sha256
import json
from math import isfinite
from typing import Any, Protocol
from uuid import UUID

from src.backend.backtest_strategy_one_management import (
    StrategyOneClosedPosition, StrategyOneManagementRunner,
    StrategyOneManagementState,
)
from src.trading_runtime.arte_journal_schema import TableContract
from src.trading_runtime.keeper_session import ManagedKeeperSession
from src.trading_runtime.strategy_one_position import ResistanceBreak
from src.trading_runtime.strategy_one_protection_snapshot import (
    TABLES as PROTECTION_TABLES,
    ProtectionSnapshotRows, _canonical_snapshot_row,
    canonical_protection_snapshot_rows,
    _digest, _price, project_protection_snapshot,
    restore_protection_snapshot, load_protection_snapshot_rows,
)
from src.trading_runtime.strategy_one_stateful import StrategyOneEntryProposal


PARENT = TableContract(
    "trading_strategy_one_manager_snapshot_v2",
    (("snapshot_id", "UUID"), ("run_id", "String"),
     ("snapshot_month", "Date"), ("session_date", "Date"),
     ("checkpoint_sequence", "UInt64"), ("boundary_ms", "UInt32"),
     ("protection_hash", "FixedString(64)"),
     ("source_count", "UInt32"), ("source_hash", "FixedString(64)"),
     ("pending_break_count", "UInt32"),
     ("pending_break_hash", "FixedString(64)"),
     ("position_high_count", "UInt32"),
     ("position_high_hash", "FixedString(64)"),
     ("closed_position_count", "UInt32"),
     ("closed_position_hash", "FixedString(64)"),
     ("content_hash", "FixedString(64)")),
    "toYYYYMM(snapshot_month)", "run_id, checkpoint_sequence, snapshot_id",
)
SOURCE = TableContract(
    "trading_strategy_one_manager_source_v2",
    (("snapshot_id", "UUID"), ("run_id", "String"),
     ("snapshot_month", "Date"), ("checkpoint_sequence", "UInt64"),
     ("account_id", "String"), ("assignment_id", "String"),
     ("ticker", "LowCardinality(String)"),
     ("boundary_ms", "UInt32"), ("episode_start_ms", "UInt32"),
     ("reference_ask", "Decimal(38, 18)"),
     ("initial_stop", "Decimal(38, 18)"),
     ("initial_target", "Decimal(38, 18)"),
     ("target_level_id", "String"),
     ("frozen_gap", "Decimal(38, 18)"),
     ("bos_break_boundary_ms", "UInt32"),
     ("bos_support_level_id", "String"),
     ("strategy_number", "UInt16"),
     ("content_hash", "FixedString(64)")),
    "toYYYYMM(snapshot_month)",
    "run_id, checkpoint_sequence, account_id, assignment_id, ticker",
)
BREAK = TableContract(
    "trading_strategy_one_manager_pending_break_v2",
    (("snapshot_id", "UUID"), ("run_id", "String"),
     ("snapshot_month", "Date"), ("checkpoint_sequence", "UInt64"),
     ("account_id", "String"), ("assignment_id", "String"),
     ("ticker", "LowCardinality(String)"), ("ordinal", "UInt16"),
     ("completed_boundary_ms", "UInt32"),
     ("unified_level_id", "String"),
     ("lower", "Decimal(38, 18)"), ("upper", "Decimal(38, 18)"),
     ("content_hash", "FixedString(64)")),
    "toYYYYMM(snapshot_month)",
    "run_id, checkpoint_sequence, account_id, assignment_id, ticker, ordinal",
)
HIGH = TableContract(
    "trading_strategy_one_manager_position_high_v2",
    (("snapshot_id", "UUID"), ("run_id", "String"),
     ("snapshot_month", "Date"), ("checkpoint_sequence", "UInt64"),
     ("account_id", "String"), ("assignment_id", "String"),
     ("ticker", "LowCardinality(String)"), ("high_int", "UInt64"),
     ("content_hash", "FixedString(64)")),
    "toYYYYMM(snapshot_month)",
    "run_id, checkpoint_sequence, account_id, assignment_id, ticker",
)
CLOSED = TableContract(
    "trading_strategy_one_manager_closed_position_v2",
    (("snapshot_id", "UUID"), ("run_id", "String"),
     ("snapshot_month", "Date"), ("checkpoint_sequence", "UInt64"),
     ("account_id", "String"), ("assignment_id", "String"),
     ("ticker", "LowCardinality(String)"),
     ("closed_boundary_ms", "UInt32"),
     ("entry_resistance_id", "String"), ("high_int", "UInt64"),
     ("content_hash", "FixedString(64)")),
    "toYYYYMM(snapshot_month)",
    "run_id, checkpoint_sequence, account_id, assignment_id, ticker",
)
TABLES = (PARENT, SOURCE, BREAK, HIGH, CLOSED)


@dataclass(frozen=True, slots=True)
class ManagerSnapshotRows:
    snapshot: dict[str, Any]
    sources: tuple[dict[str, Any], ...]
    pending_breaks: tuple[dict[str, Any], ...]
    protection: ProtectionSnapshotRows
    position_highs: tuple[dict[str, Any], ...] = ()
    closed_positions: tuple[dict[str, Any], ...] = ()


@dataclass(frozen=True, slots=True)
class ManagerSnapshotHead:
    run_id: str
    checkpoint_sequence: int
    journal_batch_id: str
    snapshot_hash: str
    keeper_version: int


class ManagerSnapshotHeadReader(Protocol):
    def read_head(self, *, run_id: str) -> ManagerSnapshotHead: ...


class ManagedManagerSnapshotHeadReader:
    """Read a durable Keeper selection, never create or advance it."""

    def __init__(self, session: ManagedKeeperSession) -> None:
        if not isinstance(session, ManagedKeeperSession):
            raise TypeError("Strategy 1 manager head needs managed Keeper")
        self._session = session

    @staticmethod
    def path(run_id: str) -> str:
        if (type(run_id) is not str or not run_id
                or any(char in run_id for char in "\r\n\x00")):
            raise ValueError("Strategy 1 manager head run is invalid")
        return ("/trading/strategy-one-manager-snapshot/v2/"
                + sha256(run_id.encode()).hexdigest() + "/head")

    def read_head(self, *, run_id: str) -> ManagerSnapshotHead:
        session, client = self._session, self._session.client
        if not session.writable or client.client_id is None:
            raise RuntimeError("Strategy 1 manager Keeper session is unavailable")
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
            head = ManagerSnapshotHead(
                run_id, int(fields[2]), fields[3], fields[4], stat.version)
        except Exception as exc:
            raise ValueError("Strategy 1 manager Keeper head missing or corrupt") from exc
        if (not session.writable or session._generation != generation
                or client.client_id != client_id):
            raise RuntimeError("Strategy 1 manager Keeper session changed during read")
        return head


def project_manager_snapshot(*, run_id: str, session_date: date,
                             checkpoint_sequence: int,
                             state: StrategyOneManagementState,
                             max_pending_breaks: int = 256,
                             ) -> ManagerSnapshotRows:
    """One complete, scalar state root at a completed journal cursor."""
    StrategyOneManagementRunner._validate_capture(
        state, max_pending_breaks=max_pending_breaks)
    positions = {(key[0], key[2], key[1]): value
                 for key, value in state.positions}
    protection = project_protection_snapshot(
        run_id=run_id, session_date=session_date,
        checkpoint_sequence=checkpoint_sequence,
        boundary_ms=state.boundary_ms, positions=positions)
    root = protection.snapshot
    common = dict(snapshot_id=root["snapshot_id"], run_id=run_id,
                  snapshot_month=root["snapshot_month"],
                  checkpoint_sequence=checkpoint_sequence)
    sources = []
    for key, proposal in state.submitted:
        account, assignment, ticker = key
        if (not 0 < proposal.boundary_ms <= state.boundary_ms
                or proposal.episode_start_ms > proposal.boundary_ms
                or not proposal.target_level_id or not proposal.bos_support_level_id
                or any(type(value) not in (int, float) or not isfinite(value)
                       for value in (proposal.reference_ask, proposal.initial_stop,
                                     proposal.initial_target, proposal.frozen_gap))):
            raise ValueError("Strategy 1 manager source is not causal")
        payload = dict(**common, account_id=account,
                       assignment_id=assignment, ticker=ticker,
                       boundary_ms=proposal.boundary_ms,
                       episode_start_ms=proposal.episode_start_ms,
                       reference_ask=_price(proposal.reference_ask),
                       initial_stop=_price(proposal.initial_stop),
                       initial_target=_price(proposal.initial_target),
                       target_level_id=proposal.target_level_id,
                       frozen_gap=_price(proposal.frozen_gap),
                       bos_break_boundary_ms=proposal.bos_break_boundary_ms,
                       bos_support_level_id=proposal.bos_support_level_id,
                       strategy_number=proposal.strategy_number)
        sources.append({**payload, "content_hash": _digest(payload)})
    breaks = []
    for key, pending in state.pending_breaks:
        account, assignment, ticker = key
        for ordinal, witness in enumerate(pending):
            payload = dict(**common, account_id=account,
                           assignment_id=assignment, ticker=ticker,
                           ordinal=ordinal,
                           completed_boundary_ms=witness.completed_boundary_ms,
                           unified_level_id=witness.level["unified_level_id"],
                           lower=_price(witness.level["lower"]),
                           upper=_price(witness.level["upper"]))
            breaks.append({**payload, "content_hash": _digest(payload)})
    highs = []
    for (account, assignment, ticker), high_int in state.position_highs:
        payload = dict(**common, account_id=account,
                       assignment_id=assignment, ticker=ticker,
                       high_int=high_int)
        highs.append({**payload, "content_hash": _digest(payload)})
    closed = []
    for (account, assignment, ticker), prior in state.closed_positions:
        payload = dict(**common, account_id=account,
                       assignment_id=assignment, ticker=ticker,
                       closed_boundary_ms=prior.closed_boundary_ms,
                       entry_resistance_id=prior.entry_resistance_id,
                       high_int=prior.high_int)
        closed.append({**payload, "content_hash": _digest(payload)})
    seal = dict(**common, session_date=session_date.isoformat(),
                boundary_ms=state.boundary_ms,
                protection_hash=root["content_hash"],
                source_count=len(sources),
                source_hash=_digest([row["content_hash"] for row in sources]),
                pending_break_count=len(breaks),
                pending_break_hash=_digest([row["content_hash"] for row in breaks]),
                position_high_count=len(highs),
                position_high_hash=_digest([row["content_hash"] for row in highs]),
                closed_position_count=len(closed),
                closed_position_hash=_digest([row["content_hash"] for row in closed]))
    return ManagerSnapshotRows(
        {**seal, "content_hash": _digest(seal)}, tuple(sources),
        tuple(breaks), protection, tuple(highs), tuple(closed))


def restore_manager_snapshot(rows: ManagerSnapshotRows, *,
                             max_pending_breaks: int = 256,
                             ) -> StrategyOneManagementState:
    """Reject partial/foreign children before constructing executable state."""
    if not isinstance(rows, ManagerSnapshotRows):
        raise ValueError("Strategy 1 manager recovery needs typed rows")
    rows = ManagerSnapshotRows(
        _canonical_snapshot_row(PARENT, rows.snapshot),
        tuple(sorted((_canonical_snapshot_row(SOURCE, row)
                      for row in rows.sources), key=lambda row: (
                          row["account_id"], row["assignment_id"],
                          row["ticker"]))),
        tuple(sorted((_canonical_snapshot_row(BREAK, row)
                      for row in rows.pending_breaks), key=lambda row: (
                          row["account_id"], row["assignment_id"],
                          row["ticker"], row["ordinal"]))),
        canonical_protection_snapshot_rows(rows.protection),
        tuple(sorted((_canonical_snapshot_row(HIGH, row)
                      for row in rows.position_highs), key=lambda row: (
                          row["account_id"], row["assignment_id"], row["ticker"]))),
        tuple(sorted((_canonical_snapshot_row(CLOSED, row)
                      for row in rows.closed_positions), key=lambda row: (
                          row["account_id"], row["assignment_id"], row["ticker"]))),
    )
    seal = rows.snapshot
    if (seal.get("content_hash") != _digest({
            key: value for key, value in seal.items() if key != "content_hash"})
            or seal.get("protection_hash") != rows.protection.snapshot.get(
                "content_hash")
            or seal.get("snapshot_id") != rows.protection.snapshot.get(
                "snapshot_id")
            or seal.get("boundary_ms") != rows.protection.snapshot.get(
                "boundary_ms")
            or seal.get("run_id") != rows.protection.snapshot.get("run_id")
            or seal.get("checkpoint_sequence") != rows.protection.snapshot.get(
                "checkpoint_sequence")
            or seal.get("source_count") != len(rows.sources)
            or seal.get("pending_break_count") != len(rows.pending_breaks)
            or seal.get("position_high_count") != len(rows.position_highs)
            or seal.get("closed_position_count") != len(rows.closed_positions)):
        raise ValueError("Strategy 1 manager snapshot seal differs")
    for family, children, expected in (
            ("source", rows.sources, seal["source_hash"]),
            ("break", rows.pending_breaks, seal["pending_break_hash"]),
            ("high", rows.position_highs, seal["position_high_hash"]),
            ("closed", rows.closed_positions, seal["closed_position_hash"])):
        if (any(row.get("content_hash") != _digest({
                key: value for key, value in row.items()
                if key != "content_hash"})
                or any(row.get(key) != seal.get(key) for key in (
                    "snapshot_id", "run_id", "snapshot_month",
                    "checkpoint_sequence")) for row in children)
                or _digest([row["content_hash"] for row in children]) != expected):
            raise ValueError(f"Strategy 1 manager {family} children differ")
    submitted = []
    for row in rows.sources:
        key = (row["account_id"], row["assignment_id"], row["ticker"])
        proposal = StrategyOneEntryProposal(
            row["assignment_id"], row["account_id"], row["ticker"],
            int(row["boundary_ms"]), int(row["episode_start_ms"]),
            float(row["reference_ask"]), float(row["initial_stop"]),
            float(row["initial_target"]), row["target_level_id"],
            float(row["frozen_gap"]), int(row["bos_break_boundary_ms"]),
            row["bos_support_level_id"], int(row["strategy_number"]))
        submitted.append((key, proposal))
    pending: dict[tuple[str, str, str], list[ResistanceBreak]] = {}
    ordinals: dict[tuple[str, str, str], list[int]] = {}
    for row in rows.pending_breaks:
        key = (row["account_id"], row["assignment_id"], row["ticker"])
        ordinals.setdefault(key, []).append(int(row["ordinal"]))
        pending.setdefault(key, []).append(ResistanceBreak(
            int(row["completed_boundary_ms"]), {
                "unified_level_id": row["unified_level_id"],
                "lower": float(row["lower"]), "upper": float(row["upper"]),
                "role": "resistance", "side": "resistance"}))
    if any(values != list(range(len(values))) for values in ordinals.values()):
        raise ValueError("Strategy 1 pending break ordinals differ")
    positions = restore_protection_snapshot(rows.protection)
    state = StrategyOneManagementState(
        int(seal["boundary_ms"]), tuple(submitted),
        tuple(sorted(((account, assignment, ticker), value)
                     for (account, ticker, assignment), value
                     in positions.items())),
        tuple(sorted((key, tuple(value)) for key, value in pending.items())),
        tuple(((row["account_id"], row["assignment_id"], row["ticker"]),
               int(row["high_int"])) for row in rows.position_highs),
        tuple(((row["account_id"], row["assignment_id"], row["ticker"]),
               StrategyOneClosedPosition(
                   int(row["closed_boundary_ms"]),
                   row["entry_resistance_id"], int(row["high_int"])))
              for row in rows.closed_positions))
    StrategyOneManagementRunner._validate_capture(
        state, max_pending_breaks=max_pending_breaks)
    if project_manager_snapshot(
            run_id=seal["run_id"],
            session_date=date.fromisoformat(seal["session_date"]),
            checkpoint_sequence=seal["checkpoint_sequence"],
            state=state, max_pending_breaks=max_pending_breaks) != rows:
        raise ValueError("Strategy 1 manager snapshot does not round-trip exactly")
    return state


def load_unattested_manager_snapshot_rows(
    client: Any, *, run_id: str, checkpoint_sequence: int,
) -> ManagerSnapshotRows:
    """SELECT a content-verified historical image; caller proves V4 authority."""
    from src.backend.backtest_market_data import assert_select_only
    from src.trading_runtime.arte_journal_writer import _literal

    if (type(run_id) is not str or not run_id
            or type(checkpoint_sequence) is not int or checkpoint_sequence < 1
            or not callable(getattr(client, "execute", None))):
        raise ValueError("Strategy 1 manager cold read needs exact run and cursor")

    def read(contract: TableContract, predicate: str, limit: int) -> tuple[dict, ...]:
        columns = ",".join(
            f"toString({name}) AS {name}" if "Decimal(" in kind else name
            for name, kind in contract.columns)
        sql = assert_select_only(
            f"SELECT {columns} FROM arte.{contract.name} WHERE {predicate} "
            f"LIMIT {limit} FORMAT JSONEachRow")
        return tuple(json.loads(line) for line in client.execute(sql).splitlines()
                     if line.strip())

    scope = (f"run_id={_literal(run_id)} "
             f"AND checkpoint_sequence={checkpoint_sequence}")
    seals = read(PARENT, scope, 2)
    if len(seals) != 1:
        raise RuntimeError("Strategy 1 manager lacks exactly one selected seal")
    seal = seals[0]
    if (seal.get("run_id") != run_id
            or seal.get("checkpoint_sequence") != checkpoint_sequence
            or seal.get("snapshot_id") is None):
        raise RuntimeError("Strategy 1 manager seal differs from requested cursor")
    try:
        snapshot_id = str(UUID(str(seal["snapshot_id"])))
    except ValueError as exc:
        raise RuntimeError("Strategy 1 manager snapshot ID is malformed") from exc
    predicate = f"snapshot_id=toUUID('{snapshot_id}')"
    source_count = seal.get("source_count")
    break_count = seal.get("pending_break_count")
    high_count = seal.get("position_high_count")
    closed_count = seal.get("closed_position_count")
    if (type(source_count) is not int or not 0 <= source_count <= 100_000
            or type(break_count) is not int or not 0 <= break_count <= 100_000
            or type(high_count) is not int or not 0 <= high_count <= 100_000
            or type(closed_count) is not int or not 0 <= closed_count <= 100_000):
        raise RuntimeError("Strategy 1 manager child bound is invalid")
    protection = load_protection_snapshot_rows(
        client, run_id=run_id, checkpoint_sequence=checkpoint_sequence)
    rows = ManagerSnapshotRows(
        seal, read(SOURCE, predicate, source_count + 1),
        read(BREAK, predicate, break_count + 1), protection,
        read(HIGH, predicate, high_count + 1),
        read(CLOSED, predicate, closed_count + 1))
    restore_manager_snapshot(rows)
    return rows


def load_attested_manager_snapshot(client: Any, keeper: ManagerSnapshotHeadReader,
                                   *, run_id: str,
                                   checkpoint_sequence: int,
                                   ) -> StrategyOneManagementState:
    """Read only a Keeper-selected snapshot at the exact cold V4 cursor."""
    from src.trading_runtime.arte_journal_commit_v4 import load_verified_v4_prefix
    from src.trading_runtime.arte_journal_projection import load_latest_backtest_cursor

    if (type(run_id) is not str or not run_id
            or type(checkpoint_sequence) is not int or checkpoint_sequence < 1
            or not callable(getattr(client, "execute", None))
            or not callable(getattr(keeper, "read_head", None))):
        raise ValueError("Strategy 1 manager cold read lacks exact authorities")
    prefix = load_verified_v4_prefix(client, run_id)
    if (prefix is None or prefix.status != "running"
            or prefix.last_sequence != checkpoint_sequence
            or not prefix.batch_ids
            or prefix.last_batch_id != prefix.batch_ids[-1]):
        raise RuntimeError("Strategy 1 manager lacks a running verified V4 cursor")
    first = keeper.read_head(run_id=run_id)
    if (not isinstance(first, ManagerSnapshotHead)
            or first.run_id != run_id
            or first.checkpoint_sequence != checkpoint_sequence
            or first.journal_batch_id != prefix.last_batch_id
            or type(first.keeper_version) is not int or first.keeper_version < 0
            or type(first.snapshot_hash) is not str
            or len(first.snapshot_hash) != 64
            or any(char not in "0123456789abcdef"
                   for char in first.snapshot_hash)):
        raise RuntimeError("Strategy 1 manager Keeper head differs from V4 cursor")
    cursor = load_latest_backtest_cursor(client, prefix)
    if (not isinstance(cursor, dict)
            or cursor.get("run_id") != run_id
            or cursor.get("event_sequence") != checkpoint_sequence
            or cursor.get("batch_id") != prefix.last_batch_id):
        raise RuntimeError("Strategy 1 manager lacks a committed market cursor")
    rows = load_unattested_manager_snapshot_rows(
        client, run_id=run_id, checkpoint_sequence=checkpoint_sequence)
    if (rows.snapshot["content_hash"] != first.snapshot_hash
            or rows.snapshot["boundary_ms"] != cursor.get("boundary_ms")
            or rows.snapshot["session_date"] != cursor.get("session_date")):
        raise RuntimeError("Strategy 1 manager seal differs from selected cursor")
    state = restore_manager_snapshot(rows)
    if keeper.read_head(run_id=run_id) != first:
        raise RuntimeError("Strategy 1 manager Keeper head changed during cold read")
    return state


def publish_manager_snapshot(client: Any, session: ManagedKeeperSession,
                             rows: ManagerSnapshotRows, *,
                             journal_batch_id: str) -> ManagerSnapshotHead:
    """Journal-worker-only rows-first, head-last publication at a V4 cursor.

    A lost INSERT response leaves a pending Keeper operation and stops this
    writer. Neither orphan ClickHouse rows nor an unselected seal are a
    recovery checkpoint. The market/strategy thread never calls this function.
    """
    from src.backend.backtest_market_data import assert_select_only
    from src.trading_runtime.arte_journal_commit_v4 import load_writer_v4_snapshot_prefix
    from src.trading_runtime.arte_journal_projection import load_latest_backtest_cursor
    from src.trading_runtime.arte_journal_writer import _insert, _literal, _wire_row
    from src.trading_runtime.arte_typed_insert_dispatch import TypedInsertDispatch

    if (not isinstance(session, ManagedKeeperSession) or not session.writable
            or getattr(client, "typed_insert_strict", False) is not True
            or not isinstance(getattr(client, "typed_insert_dispatch", None),
                              TypedInsertDispatch)
            or client.typed_insert_dispatch.keeper is not session.client
            or not isinstance(rows, ManagerSnapshotRows)):
        raise RuntimeError("Strategy 1 manager publication lacks fenced writer")
    restored = restore_manager_snapshot(rows)
    seal = rows.snapshot
    run_id, sequence = seal["run_id"], seal["checkpoint_sequence"]
    try:
        if str(UUID(journal_batch_id)) != journal_batch_id:
            raise ValueError
    except (TypeError, ValueError) as exc:
        raise ValueError("Manager snapshot batch ID is invalid") from exc
    prefix = load_writer_v4_snapshot_prefix(client, run_id)
    if (prefix is None or prefix.status != "running"
            or prefix.last_sequence != sequence
            or prefix.last_batch_id != journal_batch_id):
        raise RuntimeError("Manager snapshot lacks exact running V4 cursor")
    cursor = load_latest_backtest_cursor(client, prefix)
    if (not isinstance(cursor, dict)
            or cursor.get("run_id") != run_id
            or cursor.get("event_sequence") != sequence
            or cursor.get("batch_id") != journal_batch_id
            or cursor.get("boundary_ms") != seal["boundary_ms"]
            or cursor.get("session_date") != seal["session_date"]):
        raise RuntimeError("Manager snapshot cursor differs from captured state")
    reader = ManagedManagerSnapshotHeadReader(session)
    path = reader.path(run_id)
    if session.client.exists(path) is None:
        previous = None
    else:
        previous = reader.read_head(run_id=run_id)
        if previous.checkpoint_sequence == sequence:
            if (previous.journal_batch_id != journal_batch_id
                    or previous.snapshot_hash != seal["content_hash"]):
                raise RuntimeError("Manager snapshot head conflicts with same cursor")
            if load_attested_manager_snapshot(
                    client, reader, run_id=run_id,
                    checkpoint_sequence=sequence) != restored:
                raise RuntimeError("Manager snapshot repeat differs from selected state")
            return previous
        if previous.checkpoint_sequence > sequence:
            raise RuntimeError("Manager snapshot would rewind Keeper head")

    protection = rows.protection
    families = (
        (
            ("trading_strategy_one_protection_state_v1", protection.states),
            ("trading_strategy_one_protection_resistance_v1",
             protection.resistances),
            ("trading_strategy_one_protection_snapshot_v1",
             (protection.snapshot,)),
        ),
        (
            (SOURCE.name, rows.sources), (BREAK.name, rows.pending_breaks),
            (HIGH.name, rows.position_highs),
            (CLOSED.name, rows.closed_positions),
            (PARENT.name, (seal,)),
        ),
    )
    operations: list[tuple[str, str]] = []
    selected_id = str(UUID(str(seal["snapshot_id"])))
    for group in families:
        for table, expected in group:
            if not expected:
                continue
            token = f"manager-state:{run_id}:{sequence}:{seal['content_hash']}:{table}"
            _insert(client, table, tuple(expected), token,
                    dispatch_sequence=sequence,
                    dispatch_batch_id=journal_batch_id,
                    dispatch_manager_snapshot_hash=seal["content_hash"])
            operations.append((table, token))

    contracts = {table.name: table for table in (*TABLES, *PROTECTION_TABLES)}
    expected_rows = {
        "trading_strategy_one_protection_snapshot_v1": (protection.snapshot,),
        "trading_strategy_one_protection_state_v1": protection.states,
        "trading_strategy_one_protection_resistance_v1": protection.resistances,
        PARENT.name: (seal,), SOURCE.name: rows.sources,
        BREAK.name: rows.pending_breaks,
        HIGH.name: rows.position_highs,
        CLOSED.name: rows.closed_positions,
    }
    for table, expected in expected_rows.items():
        contract = contracts[table]
        projection = ",".join(
            f"toString({name}) AS {name}" if "Decimal(" in kind else name
            for name, kind in contract.columns)
        sql = assert_select_only(
            f"SELECT {projection} FROM arte.{table} "
            f"WHERE snapshot_id=toUUID({_literal(selected_id)}) "
            f"LIMIT {len(expected) + 1} FORMAT JSONEachRow")
        observed = tuple(json.loads(line) for line in client.execute(sql).splitlines()
                         if line.strip())
        if (len(observed) != len(expected)
                or sorted((_wire_row(table, row) for row in observed),
                          key=lambda row: json.dumps(row, sort_keys=True))
                != sorted((_wire_row(table, row) for row in expected),
                          key=lambda row: json.dumps(row, sort_keys=True))):
            raise RuntimeError(f"Manager snapshot readback differs: {table}")
    for table, token in operations:
        client.typed_insert_dispatch.seal_verified_operation(
            run_id=run_id, table=table, token=token,
            batch_id=journal_batch_id, batch_last_sequence=sequence,
            manager_snapshot=True)
    client.typed_insert_dispatch.compact_verified_manager_snapshot(
        run_id=run_id, batch_id=journal_batch_id, last_sequence=sequence,
        snapshot_hash=seal["content_hash"], operations=tuple(operations),
        previous=previous)
    selected = reader.read_head(run_id=run_id)
    if (selected.checkpoint_sequence != sequence
            or selected.journal_batch_id != journal_batch_id
            or selected.snapshot_hash != seal["content_hash"]):
        raise RuntimeError("Manager snapshot Keeper readback differs")
    return selected
