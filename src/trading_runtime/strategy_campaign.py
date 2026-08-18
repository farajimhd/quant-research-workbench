from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Any, Iterable


TERMINAL_ASSIGNMENT_STATUSES = {"disabled", "completed", "error"}
SUPPORTED_AUTHORITIES = {"disabled", "manual", "confirm", "automatic"}


class CampaignPhase(StrEnum):
    WATCHING_INITIAL = "watching_initial"
    ENTRY_PENDING = "entry_pending"
    MANAGING_POSITION = "managing_position"
    EXIT_PENDING = "exit_pending"
    REENTRY_WAIT = "reentry_wait"
    PAUSED = "paused"
    COMPLETED = "completed"
    ERROR = "error"


@dataclass(frozen=True, slots=True)
class CampaignLease:
    campaign_id: str
    book_id: str
    ticker: str
    side: str
    state: str = "confirmed"

    @property
    def key(self) -> tuple[str, str]:
        # Ownership is ticker-wide inside a book. Long and short contenders
        # must not open competing positions for the same instrument.
        return self.book_id, self.ticker.upper()


def campaign_id_for(payload: Any) -> str:
    state = dict(_value(payload, "state", {}) or {})
    explicit = str(state.get("campaign_id") or "").strip()
    if explicit:
        return explicit
    strategy_id = str(_value(payload, "strategy_id", "") or "strategy")
    ticker = str(_value(payload, "ticker", "") or "").upper()
    return f"{strategy_id}:{ticker}:{campaign_side_for(payload)}"


def campaign_book_for(payload: Any) -> str:
    state = dict(_value(payload, "state", {}) or {})
    return str(state.get("campaign_book_id") or "default").strip() or "default"


def campaign_side_for(payload: Any) -> str:
    state = dict(_value(payload, "state", {}) or {})
    parameters = dict(_value(payload, "parameters", {}) or {})
    side = str(
        state.get("campaign_side")
        or dict(parameters.get("strategy_behavior") or {}).get("side")
        or "long"
    ).strip().lower()
    if side not in {"long", "short"}:
        raise ValueError("Strategy Campaign side must be long or short")
    return side


def campaign_phase_for(payload: Any, *, position_quantity: float = 0.0) -> CampaignPhase:
    status = str(_value(payload, "status", "") or "")
    state = dict(_value(payload, "state", {}) or {})
    if status == "error":
        return CampaignPhase.ERROR
    if status in {"disabled", "completed"}:
        return CampaignPhase.COMPLETED
    if status == "paused":
        return CampaignPhase.PAUSED
    if status == "entry_pending":
        return CampaignPhase.ENTRY_PENDING
    if status == "exit_pending":
        return CampaignPhase.EXIT_PENDING
    if abs(position_quantity) > 0 or status == "managing":
        return CampaignPhase.MANAGING_POSITION
    if int(state.get("reentries") or 0) > 0 or status == "reentry_cooldown":
        return CampaignPhase.REENTRY_WAIT
    return CampaignPhase.WATCHING_INITIAL


class StrategyCampaignOrchestrator:
    """Arbitrates one session owner while allowing competing watchers.

    Registration is deliberately not ownership. A campaign reserves the key
    immediately before its first entry is submitted and becomes the confirmed
    owner on the first opening fill. This prevents two strategies from racing
    orders while still allowing both to observe and evaluate the same ticker.
    """

    def __init__(self, assignments: Iterable[Any] = ()) -> None:
        self._leases: dict[tuple[str, str], CampaignLease] = {}
        self._assignment_campaigns: dict[str, str] = {}
        self._campaign_active_legs: dict[str, set[str]] = {}
        self._journal: Any | None = None
        self._session_key = ""
        for assignment in assignments:
            self.register(assignment)

    def bind_durable_authority(self, journal: Any, *, session_key: str) -> None:
        if not session_key:
            raise ValueError("Campaign durable authority requires a session key")
        self._journal = journal
        self._session_key = session_key

    def register(self, assignment: Any) -> CampaignLease | None:
        assignment_id = str(_value(assignment, "assignment_id", "") or "").strip()
        ticker = str(_value(assignment, "ticker", "") or "").strip().upper()
        status = str(_value(assignment, "status", "") or "")
        if not assignment_id or not ticker:
            raise ValueError("Campaign assignments require assignment identity and ticker")
        campaign_id = campaign_id_for(assignment)
        previous_campaign = self._assignment_campaigns.get(assignment_id)
        if previous_campaign and previous_campaign != campaign_id:
            self._remove_leg(previous_campaign, assignment_id)
        self._assignment_campaigns[assignment_id] = campaign_id
        if status in TERMINAL_ASSIGNMENT_STATUSES:
            return self._lease_for_assignment(assignment)
        self._campaign_active_legs.setdefault(campaign_id, set()).add(assignment_id)
        # Recovered campaigns that already manage a position are authoritative.
        if status in {"managing", "exit_pending", "reentry_cooldown"}:
            return self.claim(assignment)
        return self._lease_for_assignment(assignment)

    def reserve(self, assignment: Any) -> CampaignLease:
        return self._claim(assignment, state="reserved")

    def claim(self, assignment: Any) -> CampaignLease:
        return self._claim(assignment, state="confirmed")

    def _claim(self, assignment: Any, *, state: str) -> CampaignLease:
        assignment_id = str(_value(assignment, "assignment_id", "") or "").strip()
        ticker = str(_value(assignment, "ticker", "") or "").strip().upper()
        if not assignment_id or not ticker:
            raise ValueError("Campaign assignments require assignment identity and ticker")
        campaign_id = campaign_id_for(assignment)
        lease = CampaignLease(
            campaign_id=campaign_id,
            book_id=campaign_book_for(assignment),
            ticker=ticker,
            side=campaign_side_for(assignment),
            state=state,
        )
        if any(
            row.campaign_id == campaign_id and row.key != lease.key
            for row in self._leases.values()
        ):
            raise ValueError(
                f"Strategy Campaign {campaign_id} cannot span multiple ticker leases"
            )
        current = self._leases.get(lease.key)
        if self._journal is not None:
            durable = self._journal.acquire_campaign_session_ownership(
                _resource_id(lease),
                session_key=self._session_key,
                owner_id=campaign_id,
                state=state,
            )
            if durable is None:
                existing = self._journal.campaign_session_ownership(
                    _resource_id(lease), session_key=self._session_key
                )
                owner_id = str(dict(existing or {}).get("owner_id") or "another campaign")
                raise ValueError(
                    f"{ticker} is already owned by active Strategy Campaign "
                    f"{owner_id} in book {lease.book_id}"
                )
            lease = CampaignLease(
                campaign_id=campaign_id,
                book_id=lease.book_id,
                ticker=lease.ticker,
                side=lease.side,
                state=str(durable["state"]),
            )
        if current is not None and current.campaign_id != campaign_id:
            raise ValueError(
                f"{ticker} is already owned by active Strategy Campaign "
                f"{current.campaign_id} in book {lease.book_id}"
            )
        if current is not None and current.campaign_id == campaign_id and current.state == "confirmed":
            lease = current
        self._leases[lease.key] = lease
        self._campaign_active_legs.setdefault(campaign_id, set()).add(assignment_id)
        return lease

    def release_reservation(self, assignment: Any) -> None:
        lease = self._lease_for_assignment(assignment)
        if lease is not None and lease.state == "reserved":
            if self._journal is not None:
                self._journal.release_campaign_session_reservation(
                    _resource_id(lease),
                    session_key=self._session_key,
                    owner_id=lease.campaign_id,
                )
            self._leases.pop(lease.key, None)

    def can_evaluate(self, assignment: Any) -> bool:
        lease = self._lease_for_assignment(assignment)
        if self._journal is not None:
            proposed = CampaignLease(
                campaign_id=campaign_id_for(assignment),
                book_id=campaign_book_for(assignment),
                ticker=str(_value(assignment, "ticker", "") or "").upper(),
                side=campaign_side_for(assignment),
            )
            durable = self._journal.campaign_session_ownership(
                _resource_id(proposed), session_key=self._session_key
            )
            if durable is not None:
                return str(durable.get("owner_id") or "") == proposed.campaign_id
        return lease is None or lease.campaign_id == campaign_id_for(assignment)

    def _lease_for_assignment(self, assignment: Any) -> CampaignLease | None:
        return self.lease_for(
            book_id=campaign_book_for(assignment),
            ticker=str(_value(assignment, "ticker", "") or ""),
        )

    def lease_for(self, *, book_id: str, ticker: str) -> CampaignLease | None:
        return self._leases.get((book_id or "default", ticker.upper()))

    def assert_owner(self, assignment: Any) -> None:
        lease = self._lease_for_assignment(assignment)
        campaign_id = campaign_id_for(assignment)
        if lease is None or lease.campaign_id != campaign_id:
            raise ValueError(
                f"Strategy Campaign {campaign_id} does not own "
                f"{str(_value(assignment, 'ticker', '')).upper()}"
            )

    def _remove_leg(self, campaign_id: str, assignment_id: str) -> None:
        legs = self._campaign_active_legs.get(campaign_id)
        if legs is None:
            return
        legs.discard(assignment_id)
        if legs:
            return
        self._campaign_active_legs.pop(campaign_id, None)
        self._leases = {
            key: lease
            for key, lease in self._leases.items()
            if lease.campaign_id != campaign_id
        }


def campaign_state(
    *,
    campaign_id: str,
    deployment_id: str,
    profile_id: str,
    book_id: str,
    universe_id: str,
    side: str,
) -> dict[str, str]:
    if side not in {"long", "short"}:
        raise ValueError("Strategy Campaign side must be long or short")
    return {
        "campaign_id": campaign_id,
        "campaign_deployment_id": deployment_id,
        "campaign_profile_id": profile_id,
        "campaign_book_id": book_id or "default",
        "campaign_universe_id": universe_id,
        "campaign_side": side,
    }


def validate_campaign_policy(policy: dict[str, Any]) -> None:
    interactive_authorities = {"manual", "confirm", "automatic"}
    if str(policy.get("initial_entry_authority") or "") not in interactive_authorities:
        raise ValueError("Campaign policy initial_entry_authority is unsupported")
    if str(policy.get("reentry_authority") or "") not in SUPPORTED_AUTHORITIES:
        raise ValueError("Campaign policy reentry_authority is unsupported")
    if str(policy.get("exit_authority") or "") not in interactive_authorities:
        raise ValueError("Campaign policy exit_authority is unsupported")
    if str(policy.get("protective_exit_authority") or "") != "automatic":
        raise ValueError("Protective exits must remain automatic")
    if int(policy.get("maximum_reentries") or 0) < 0:
        raise ValueError("Campaign maximum reentries cannot be negative")
    if int(policy.get("reentry_cooldown_ms") or 0) < 0:
        raise ValueError("Campaign reentry cooldown cannot be negative")
    if int(policy.get("maximum_initial_watch_ms") or 0) < 0:
        raise ValueError("Campaign initial watch duration cannot be negative")
    if str(policy.get("session_end_behavior") or "") not in {
        "keep_watching",
        "stop_when_flat",
        "exit_and_stop",
    }:
        raise ValueError("Campaign session-end behavior is unsupported")


def _value(payload: Any, key: str, default: Any) -> Any:
    if isinstance(payload, dict):
        return payload.get(key, default)
    return getattr(payload, key, default)


def _resource_id(lease: CampaignLease) -> str:
    return f"{lease.book_id}|{lease.ticker.upper()}"
