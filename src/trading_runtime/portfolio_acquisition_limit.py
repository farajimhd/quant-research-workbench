"""Causal admission counts from durable accepted Portfolio reservations.

This module does not select a strategy rule or authorize broker execution.
The caller must supply the exact independently owned session and verified
Portfolio history, under its existing admission fence.
"""
from dataclasses import dataclass
from datetime import datetime
from math import isfinite

from .portfolio import ENTRY_ACTIONS, PortfolioReservation


def _aware(value):
    return type(value) is datetime and value.tzinfo is not None and value.utcoffset() is not None


@dataclass(frozen=True, slots=True)
class SessionAcquisitionLimit:
    run_id: str
    account_id: str
    begins_at: datetime
    ends_at: datetime
    maximum: int

    def __post_init__(self):
        if (type(self.run_id) is not str or not self.run_id
                or type(self.account_id) is not str or not self.account_id
                or not _aware(self.begins_at) or not _aware(self.ends_at)
                or self.begins_at >= self.ends_at
                or type(self.maximum) is not int or not 1 <= self.maximum <= 32):
            raise ValueError('Acquisition limit requires an exact bounded session and positive quota')


def accepted_acquisition_ids(scope, reservations, *, run_id, ticker, at):
    if type(scope) is not SessionAcquisitionLimit or run_id != scope.run_id:
        raise ValueError('Acquisition history has foreign session ownership')
    scope.__post_init__()
    if (type(ticker) is not str or not ticker or ticker != ticker.upper()
            or not _aware(at) or not scope.begins_at <= at < scope.ends_at):
        raise ValueError('Acquisition decision requires an exact ticker and causal session clock')
    accepted = set()
    reservation_ids = set()
    for row in reservations:
        if type(row) is not PortfolioReservation:
            raise ValueError('Acquisition history requires typed Portfolio reservations')
        if row.account_id != scope.account_id or row.ticker != ticker or row.action not in ENTRY_ACTIONS:
            continue
        if not _aware(row.created_at) or row.created_at > at:
            raise ValueError('Acquisition history contains missing or future acceptance clocks')
        if not scope.begins_at <= row.created_at < scope.ends_at:
            continue
        if (type(row.quantity) not in {int, float} or not isfinite(row.quantity)
                or row.quantity < 0):
            raise ValueError('Acquisition history has invalid accepted quantity')
        # A zero-quantity cash hold reserves a budget; it is not an acquisition.
        if row.quantity == 0:
            continue
        if not row.intent_id or not row.reservation_id or not row.decision_id:
            raise ValueError('Acquisition history lacks accepted decision identity')
        if row.reservation_id in reservation_ids or row.intent_id in accepted:
            raise ValueError('Acquisition acceptance identity is duplicated')
        reservation_ids.add(row.reservation_id)
        accepted.add(row.intent_id)
    return frozenset(accepted)


def require_owned_session_history(scope, reservations, *, run_id, at):
    """Reject a native independent-session window that hides accepted history.

    Use only with a single-session run's verified Portfolio reservations. This
    is stricter than the generic multi-session counting projection above.
    The original issued window still needs independent caller verification.
    """
    if type(scope) is not SessionAcquisitionLimit or run_id != scope.run_id:
        raise ValueError('Acquisition history has foreign session ownership')
    scope.__post_init__()
    if not _aware(at) or not scope.begins_at <= at <= scope.ends_at:
        raise ValueError('Acquisition recovery has an invalid session clock')
    for row in reservations:
        if type(row) is not PortfolioReservation:
            raise ValueError('Acquisition recovery requires typed Portfolio reservations')
        if row.account_id != scope.account_id or row.action not in ENTRY_ACTIONS:
            continue
        if type(row.quantity) not in {int, float} or not isfinite(row.quantity) or row.quantity < 0:
            raise ValueError('Acquisition recovery has invalid accepted quantity')
        if row.quantity == 0:
            continue
        if (not _aware(row.created_at) or not scope.begins_at <= row.created_at < scope.ends_at
                or row.created_at > at):
            raise ValueError('Acquisition window excludes or precedes owned accepted history')


def acquisition_limit_reached(scope, reservations, *, run_id, ticker, intent_id, at):
    if type(intent_id) is not str or not intent_id:
        raise ValueError('Acquisition request identity is required')
    accepted = accepted_acquisition_ids(scope, reservations, run_id=run_id, ticker=ticker, at=at)
    # Same-intent retries remain the Portfolio's existing idempotent decision.
    return intent_id not in accepted and len(accepted) >= scope.maximum
