"""Normalized causal resistance-add child of a Strategy 1 typed intent."""
from __future__ import annotations

from datetime import date
from decimal import Decimal
from uuid import NAMESPACE_URL, UUID, uuid5

from .arte_intent_projection import _number
from .arte_strategy_one_entry_schema import ADD_EVIDENCE
from .arte_journal_writer import typed_row
from .signals import StrategyIntent
from .strategy_one_add import StrategyOneAddProposal
from .strategy_one_intent import strategy_one_add_intent


def project_strategy_one_add_evidence(
    proposal: StrategyOneAddProposal, intent: StrategyIntent, *,
    session_date: date, run_id: str, batch_id: str, parent_record_id: str,
) -> dict[str, str | int]:
    """Project only the add-specific rule facts; never metadata or a blob."""
    if (not isinstance(proposal, StrategyOneAddProposal)
            or not isinstance(intent, StrategyIntent)
            or not run_id
            or intent != strategy_one_add_intent(proposal,
                                                  session_date=session_date)):
        raise ValueError("Strategy 1 add evidence differs from its typed intent")
    midpoint = _number(proposal.resistance_midpoint)
    if midpoint is None or Decimal(midpoint) <= 0:
        raise ValueError("Strategy 1 add evidence needs a positive midpoint")
    parent = str(UUID(parent_record_id))
    return {
        "record_id": str(uuid5(NAMESPACE_URL, f"{parent}:strategy-one-add")),
        "parent_record_id": parent,
        "run_id": run_id,
        "event_month": session_date.replace(day=1).isoformat(),
        "batch_id": str(UUID(batch_id)),
        "strategy_number": proposal.strategy_number,
        "assignment_id": proposal.assignment_id,
        "boundary_ms": proposal.boundary_ms,
        "resistance_id": proposal.resistance_id,
        "resistance_midpoint": midpoint,
        "purchase_ordinal": proposal.purchase_ordinal,
    }


def seal_strategy_one_add_evidence(row: dict[str, str | int]) -> dict[str, str | int]:
    return typed_row(ADD_EVIDENCE.name, row)
