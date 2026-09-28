"""Normalized Strategy 1 manager checkpoint rows; no publication gate yet.

The protection snapshot owns position geometry. This family adds only the
submitted entry sources and unconfirmed break witnesses needed to restore the
manager. A journal/Keeper publisher must write children and protection first,
then this seal; Backtest execution never writes these tables directly.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from math import isfinite
from typing import Any

from src.backend.backtest_strategy_one_management import (
    StrategyOneManagementRunner, StrategyOneManagementState,
)
from src.trading_runtime.arte_journal_schema import TableContract
from src.trading_runtime.strategy_one_position import ResistanceBreak
from src.trading_runtime.strategy_one_protection_snapshot import (
    ProtectionSnapshotRows, _digest, _price, project_protection_snapshot,
    restore_protection_snapshot,
)
from src.trading_runtime.strategy_one_stateful import StrategyOneEntryProposal


PARENT = TableContract(
    "trading_strategy_one_manager_snapshot_v1",
    (("snapshot_id", "UUID"), ("run_id", "String"),
     ("snapshot_month", "Date"), ("session_date", "Date"),
     ("checkpoint_sequence", "UInt64"), ("boundary_ms", "UInt32"),
     ("protection_hash", "FixedString(64)"),
     ("source_count", "UInt32"), ("source_hash", "FixedString(64)"),
     ("pending_break_count", "UInt32"),
     ("pending_break_hash", "FixedString(64)"),
     ("content_hash", "FixedString(64)")),
    "toYYYYMM(snapshot_month)", "run_id, checkpoint_sequence, snapshot_id",
)
SOURCE = TableContract(
    "trading_strategy_one_manager_source_v1",
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
    "trading_strategy_one_manager_pending_break_v1",
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
TABLES = (PARENT, SOURCE, BREAK)


@dataclass(frozen=True, slots=True)
class ManagerSnapshotRows:
    snapshot: dict[str, Any]
    sources: tuple[dict[str, Any], ...]
    pending_breaks: tuple[dict[str, Any], ...]
    protection: ProtectionSnapshotRows


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
    seal = dict(**common, session_date=session_date.isoformat(),
                boundary_ms=state.boundary_ms,
                protection_hash=root["content_hash"],
                source_count=len(sources),
                source_hash=_digest([row["content_hash"] for row in sources]),
                pending_break_count=len(breaks),
                pending_break_hash=_digest([row["content_hash"] for row in breaks]))
    return ManagerSnapshotRows(
        {**seal, "content_hash": _digest(seal)}, tuple(sources),
        tuple(breaks), protection)


def restore_manager_snapshot(rows: ManagerSnapshotRows, *,
                             max_pending_breaks: int = 256,
                             ) -> StrategyOneManagementState:
    """Reject partial/foreign children before constructing executable state."""
    if not isinstance(rows, ManagerSnapshotRows):
        raise ValueError("Strategy 1 manager recovery needs typed rows")
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
            or seal.get("pending_break_count") != len(rows.pending_breaks)):
        raise ValueError("Strategy 1 manager snapshot seal differs")
    for family, children, expected in (
            ("source", rows.sources, seal["source_hash"]),
            ("break", rows.pending_breaks, seal["pending_break_hash"])):
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
        tuple(sorted((key, tuple(value)) for key, value in pending.items())))
    StrategyOneManagementRunner._validate_capture(
        state, max_pending_breaks=max_pending_breaks)
    if project_manager_snapshot(
            run_id=seal["run_id"],
            session_date=date.fromisoformat(seal["session_date"]),
            checkpoint_sequence=seal["checkpoint_sequence"],
            state=state, max_pending_breaks=max_pending_breaks) != rows:
        raise ValueError("Strategy 1 manager snapshot does not round-trip exactly")
    return state
