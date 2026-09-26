"""Staged normalized Strategy 1 entry evidence, not yet a writer family.

The existing typed intent and protection rows own prices, policy, sizing and
target fraction. This child owns only Strategy 1's nonredundant causal facts.
It must be added to the V4 commit seal and cold recovery before publication.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from hashlib import sha256
from uuid import NAMESPACE_URL, UUID, uuid5
from zoneinfo import ZoneInfo

from .arte_intent_projection import _number
from .arte_intent_projection import load_committed_strategy_intent_page
from .arte_journal_writer import (
    _canonical_typed_content, _literal, _rows, typed_row,
)
from .arte_journal_commit_v4 import V4CommittedPrefix
from .journal_contract import canonical_json
from .arte_strategy_one_entry_schema import ENTRY_EVIDENCE
from .signals import StrategyIntent
from .strategy_one_intent import strategy_one_entry_intent
from .strategy_one_stateful import StrategyOneEntryProposal


def project_strategy_one_entry_evidence(
    proposal: StrategyOneEntryProposal, intent: StrategyIntent, *,
    session_date: date, run_id: str, batch_id: str, parent_record_id: str,
) -> dict[str, str | int]:
    """Link only exact numbered evidence to its separately typed intent.

    `content_hash` and batch sealing belong to the background journal writer,
    never the execution callback. This projector performs no I/O.
    """
    if (not isinstance(proposal, StrategyOneEntryProposal)
            or not isinstance(intent, StrategyIntent)
            or not run_id or not proposal.target_level_id
            or not proposal.bos_support_level_id):
        raise ValueError("Strategy 1 entry evidence identity is incomplete")
    batch = str(UUID(batch_id))
    parent = str(UUID(parent_record_id))
    expected = strategy_one_entry_intent(proposal, session_date=session_date)
    if intent != expected:
        raise ValueError("Strategy 1 entry evidence differs from its typed intent")
    gap = _number(proposal.frozen_gap)
    if gap is None or Decimal(gap) <= 0:
        raise ValueError("Strategy 1 entry evidence requires a positive frozen gap")
    if (type(proposal.episode_start_ms) is not int
            or not 0 < proposal.episode_start_ms <= proposal.boundary_ms
            or type(proposal.bos_break_boundary_ms) is not int
            or not 0 < proposal.bos_break_boundary_ms <= proposal.boundary_ms):
        raise ValueError("Strategy 1 entry evidence has invalid causal boundaries")
    return {
        "record_id": str(uuid5(NAMESPACE_URL, f"{parent}:strategy-one-entry")),
        "parent_record_id": parent,
        "run_id": run_id,
        "event_month": session_date.replace(day=1).isoformat(),
        "batch_id": batch,
        "strategy_number": proposal.strategy_number,
        "assignment_id": proposal.assignment_id,
        "episode_start_ms": proposal.episode_start_ms,
        "boundary_ms": proposal.boundary_ms,
        "target_level_id": proposal.target_level_id,
        "frozen_gap": gap,
        "bos_break_boundary_ms": proposal.bos_break_boundary_ms,
        "bos_support_level_id": proposal.bos_support_level_id,
    }


def seal_strategy_one_entry_evidence(row: dict[str, str | int]) -> dict[str, str | int]:
    """Hash the normalized persisted values on the journal writer lane."""
    return typed_row(ENTRY_EVIDENCE.name, row)


@dataclass(frozen=True, slots=True)
class RecoveredStrategyOneEntry:
    sequence: int
    parent_record_id: str
    proposal: StrategyOneEntryProposal
    intent: StrategyIntent


@dataclass(frozen=True, slots=True)
class RecoveredStrategyOneEntryPage:
    entries: tuple[RecoveredStrategyOneEntry, ...]
    scanned_through_sequence: int
    exhausted: bool


def load_committed_strategy_one_entry_page(
    client, prefix: V4CommittedPrefix, *, after_sequence: int = 0,
    limit: int = 200,
) -> RecoveredStrategyOneEntryPage:
    """Reconstruct exact proposals from a previously cold-verified V4 prefix.

    Verify the run prefix once before paging; rechecking every commit for
    every page would make recovery quadratic in a long Backtest journal.
    """
    if (not isinstance(prefix, V4CommittedPrefix)
            or not 1 <= limit <= 500 or not 0 <= after_sequence <= prefix.last_sequence
            or not prefix.run_id or not prefix.batch_ids
            or prefix.last_sequence < len(prefix.batch_ids)):
        raise ValueError("Strategy 1 recovery needs the current verified V4 prefix")
    intents = load_committed_strategy_intent_page(
        client, prefix, after_sequence=after_sequence, limit=limit)
    scanned = intents[-1].sequence if intents else prefix.last_sequence
    exhausted = len(intents) < limit
    selected = tuple(row for row in intents
                     if row.intent.reason == "strategy_one_entry")
    if not selected:
        return RecoveredStrategyOneEntryPage((), scanned, exhausted)
    ids = {row.record_id: row for row in selected}
    sql_ids = ",".join(f"toUUID({_literal(record_id)})" for record_id in ids)
    columns = ",".join(name for name, _ in ENTRY_EVIDENCE.columns)
    rows = _rows(client,
        f"SELECT {columns} FROM arte.{ENTRY_EVIDENCE.name} "
        f"WHERE run_id={_literal(prefix.run_id)} "
        f"AND parent_record_id IN ({sql_ids}) "
        f"LIMIT {len(ids) + 1} FORMAT JSONEachRow")
    if len(rows) != len(ids):
        raise RuntimeError("Committed Strategy 1 entry evidence is missing or duplicated")
    allowed_batches = set(prefix.batch_ids)
    seen = set()
    result = []
    for row in rows:
        parent = str(UUID(str(row["parent_record_id"])))
        if parent in seen or parent not in ids:
            raise RuntimeError("Committed Strategy 1 entry has an unknown parent")
        seen.add(parent)
        content = {key: value for key, value in row.items()
                   if key != "content_hash"}
        canonical = _canonical_typed_content(
            ENTRY_EVIDENCE.name, content, stored_utc=True)
        digest = sha256(canonical_json(canonical).encode()).hexdigest()
        recovered = ids[parent]
        instant = recovered.intent.event_time.astimezone(
            ZoneInfo("America/New_York"))
        session_date = instant.date()
        if (row["content_hash"] != digest
                or str(UUID(str(row["batch_id"]))) not in allowed_batches
                or str(UUID(str(row["batch_id"]))) != recovered.batch_id
                or row["record_id"] != str(uuid5(
                    NAMESPACE_URL, f"{parent}:strategy-one-entry"))
                or row["event_month"] != session_date.replace(day=1).isoformat()
                or row["strategy_number"] != 1):
            raise RuntimeError("Committed Strategy 1 entry evidence changed")
        intent = recovered.intent
        if intent.invalidation_price is None or intent.profit_target_price is None:
            raise RuntimeError("Committed Strategy 1 entry lacks typed protection")
        proposal = StrategyOneEntryProposal(
            str(row["assignment_id"]), recovered.account_id, intent.ticker,
            int(row["boundary_ms"]), int(row["episode_start_ms"]),
            intent.reference_price, intent.invalidation_price,
            intent.profit_target_price, str(row["target_level_id"]),
            float(row["frozen_gap"]), int(row["bos_break_boundary_ms"]),
            str(row["bos_support_level_id"]), 1,
        )
        if strategy_one_entry_intent(proposal, session_date=session_date) != intent:
            raise RuntimeError("Committed Strategy 1 proposal differs from its intent")
        result.append(RecoveredStrategyOneEntry(
            recovered.sequence, parent, proposal, intent))
    return RecoveredStrategyOneEntryPage(
        tuple(sorted(result, key=lambda item: item.sequence)), scanned, exhausted)
