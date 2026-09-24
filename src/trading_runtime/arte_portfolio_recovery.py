"""Read-only conversion of exact typed snapshot revisions into Portfolio state.

The caller chooses pinned revisions and must synchronize broker state before
admitting live orders. This module never constructs an engine or writes a journal.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Mapping

from src.trading_runtime.arte_journal_writer import load_typed_run_context
from src.trading_runtime.arte_portfolio_snapshot import (
    load_portfolio_snapshot, project_portfolio_snapshot,
)
from src.trading_runtime.portfolio import (
    PortfolioAccountProfile, PortfolioAccountState, PortfolioAllocationLot,
    PortfolioControlMode, PortfolioReconciliationDifference, PortfolioReservation,
    PortfolioSyncState, narrow_policy_for_account_class, portfolio_policy_from_payload,
)


@dataclass(frozen=True, slots=True)
class PortfolioRecovery:
    run_id: str
    revisions: dict[str, int]
    states: dict[str, PortfolioAccountState]
    reservations: dict[str, PortfolioReservation]
    allocations: dict[str, PortfolioAllocationLot]
    differences: dict[tuple[str, str], PortfolioReconciliationDifference]
    last_filled_by_reservation: dict[str, float]


def _time(value: Any, *, cutoff_at: datetime) -> datetime:
    if not isinstance(value, str):
        raise ValueError("Portfolio recovery timestamp is not a string")
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None or parsed > cutoff_at:
        raise ValueError("Portfolio recovery timestamp is naive or beyond its cutoff")
    return parsed


def recover_portfolio_engine_state(
    client: Any, *, run_id: str,
    profiles: tuple[PortfolioAccountProfile, ...] | list[PortfolioAccountProfile],
    state_revisions: Mapping[str, int], cutoff_at: datetime,
) -> PortfolioRecovery:
    """Convert exact committed account revisions, never a mutable latest head.

    The cutoff bounds domain timestamps. The revision map is the authoritative
    point-in-time selection; it must come from the caller's verified event fence.
    """
    if (not isinstance(run_id, str) or not run_id
            or not isinstance(cutoff_at, datetime) or cutoff_at.tzinfo is None
            or not profiles or any(type(profile) is not PortfolioAccountProfile for profile in profiles)):
        raise ValueError("Portfolio recovery requires typed run, profiles, and cutoff")
    by_id = {profile.account_id: profile for profile in profiles}
    if len(by_id) != len(profiles) or len({p.account_key for p in profiles}) != len(profiles):
        raise ValueError("Portfolio recovery profiles have duplicate identities")
    if (set(state_revisions) != set(by_id)
            or any(type(revision) is not int or revision < 1 for revision in state_revisions.values())):
        raise ValueError("Portfolio recovery needs one positive revision per account")
    context = load_typed_run_context(client, run_id)
    if set(context["account_ids"]) != set(by_id) or len(context["account_ids"]) != len(by_id):
        raise RuntimeError("Portfolio recovery profiles differ from pinned run accounts")

    states: dict[str, PortfolioAccountState] = {}
    reservations: dict[str, PortfolioReservation] = {}
    allocations: dict[str, PortfolioAllocationLot] = {}
    differences: dict[tuple[str, str], PortfolioReconciliationDifference] = {}
    last_filled: dict[str, float] = {}
    for account_id, profile in by_id.items():
        revision = state_revisions[account_id]
        snapshot = load_portfolio_snapshot(client, run_id=run_id, account_id=account_id,
                                           state_revision=revision)
        if snapshot is None or snapshot["state_revision"] != revision:
            raise RuntimeError(f"Portfolio recovery lacks account revision: {account_id}")
        root = snapshot["families"]["trading_portfolio_snapshot_v1"]
        if (len(root) != 1 or root[0]["run_id"] != run_id
                or root[0]["account_id"] != account_id
                or root[0]["state_revision"] != revision):
            raise RuntimeError("Portfolio recovery snapshot identity differs from request")
        raw = snapshot["state"]
        project_portfolio_snapshot(account_id, raw)
        if raw["account_key"] != profile.account_key:
            raise RuntimeError("Portfolio recovery account key differs from pinned profile")
        state = PortfolioAccountState(profile=profile)
        state.control_mode = PortfolioControlMode(raw["control_mode"])
        state.sync_state = PortfolioSyncState(raw["sync_state"])
        state.snapshot_id = raw["snapshot_id"]
        state.observed_at = (_time(raw["observed_at"], cutoff_at=cutoff_at)
                             if raw["observed_at"] is not None else None)
        state.stale_reason = raw["stale_reason"]
        state.peak_net_liquidation = raw["peak_net_liquidation"]
        state.realized_pnl_baseline = raw["realized_pnl_baseline"]
        if raw["selected_policy"] is not None:
            state.policy_override = narrow_policy_for_account_class(
                portfolio_policy_from_payload(raw["selected_policy"]), profile.account_class)
        state.disabled_strategy_allocations = set(raw["disabled_strategy_allocations"])
        state.pending_operational_commands = [dict(row) for row in raw["pending_operational_commands"]]
        for row in state.pending_operational_commands:
            if "completed_at" in row:
                row["completed_at"] = _time(row["completed_at"], cutoff_at=cutoff_at).isoformat()
        state.pending_entry_requests = {key: dict(value)
                                        for key, value in raw["pending_entry_requests"].items()}
        for row in state.pending_entry_requests.values():
            for key in ("requested_at", "last_validated_at"):
                row[key] = _time(row[key], cutoff_at=cutoff_at).isoformat()
        states[account_id] = state
        for row in raw["reservations"]:
            item = PortfolioReservation(**{**row, "created_at": _time(row["created_at"], cutoff_at=cutoff_at)})
            if item.reservation_id in reservations:
                raise RuntimeError("Portfolio recovery reservation identity is duplicated")
            reservations[item.reservation_id] = item
            last_filled[item.reservation_id] = item.filled_quantity
        for row in raw["allocations"]:
            item = PortfolioAllocationLot(**{**row, "updated_at": _time(row["updated_at"], cutoff_at=cutoff_at)})
            if item.allocation_id in allocations:
                raise RuntimeError("Portfolio recovery allocation identity is duplicated")
            allocations[item.allocation_id] = item
        for row in raw["reconciliation"]:
            item = PortfolioReconciliationDifference(**{**row, "observed_at": _time(row["observed_at"], cutoff_at=cutoff_at)})
            key = (item.account_key, item.ticker)
            if key in differences:
                raise RuntimeError("Portfolio recovery reconciliation identity is duplicated")
            differences[key] = item
    return PortfolioRecovery(run_id, dict(state_revisions), states, reservations,
                             allocations, differences, last_filled)
