"""Shared backfill decision helpers."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class BackfillDecision:
    mode: str
    reason: str
    total_gap_days: float
    run_inline: bool
    generate_script: bool
    workstation_required: bool


def decide_backfill(
    *,
    total_gap_days: float,
    is_workstation: bool,
    inline_threshold_days: float = 30.0,
) -> BackfillDecision:
    if total_gap_days <= 0:
        return BackfillDecision("none", "no_gap", total_gap_days, False, False, False)
    if total_gap_days <= inline_threshold_days:
        return BackfillDecision("inline", "small_gap", total_gap_days, True, False, False)
    if is_workstation:
        return BackfillDecision("workstation_inline", "large_gap_on_workstation", total_gap_days, True, False, True)
    return BackfillDecision("manual_script", "large_gap_requires_workstation", total_gap_days, False, True, True)
