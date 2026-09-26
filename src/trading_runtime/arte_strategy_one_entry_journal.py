"""Staged normalized Strategy 1 entry evidence, not yet a writer family.

The existing typed intent and protection rows own prices, policy, sizing and
target fraction. This child owns only Strategy 1's nonredundant causal facts.
It must be added to the V4 commit seal and cold recovery before publication.
"""
from __future__ import annotations

from datetime import date
from decimal import Decimal
from uuid import NAMESPACE_URL, UUID, uuid5

from .arte_intent_projection import _number
from .arte_journal_schema import TableContract
from .signals import StrategyIntent
from .strategy_one_intent import strategy_one_entry_intent
from .strategy_one_stateful import StrategyOneEntryProposal


ENTRY_EVIDENCE = TableContract(
    "trading_strategy_one_entry_evidence_v1",
    (
        ("record_id", "UUID"), ("parent_record_id", "UUID"),
        ("run_id", "String"), ("event_month", "Date"),
        ("batch_id", "UUID"), ("strategy_number", "UInt32"),
        ("assignment_id", "String"), ("episode_start_ms", "UInt32"),
        ("boundary_ms", "UInt32"), ("target_level_id", "String"),
        ("frozen_gap", "Decimal(38, 18)"),
        ("bos_break_boundary_ms", "UInt32"),
        ("bos_support_level_id", "String"),
        ("content_hash", "FixedString(64)"),
    ),
    "toYYYYMM(event_month)", "run_id, parent_record_id, record_id",
)


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
