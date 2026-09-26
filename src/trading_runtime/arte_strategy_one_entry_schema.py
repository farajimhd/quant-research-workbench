"""Opt-in Strategy 1 journal child; not part of the occupied V1 layout."""
from __future__ import annotations

from .arte_journal_schema import TableContract


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
