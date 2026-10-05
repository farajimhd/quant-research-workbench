"""Declared acquisition limits from caller-verified normalized OMS ownership.

The caller owns complete committed-prefix verification and the exact set of
group identities belonging to its strategy. Neither profile names nor strategy
numbers select this rule. This projection grants no financial permission.
"""
from datetime import date, datetime
from enum import StrEnum
from zoneinfo import ZoneInfo

from .arte_oms_projection import FrozenOmsGroup
from .order_management import OrderManagementState


class SessionAcquisitionPolicy(StrEnum):
    ONCE_PER_EXTENDED_SESSION = "once_per_extended_session"


def session_acquisition_lock(
    groups: tuple[FrozenOmsGroup, ...], *, policy: SessionAcquisitionPolicy,
    owned_group_ids: frozenset[str], session_date: date, session: str,
    account_id: str, ticker: str, as_of: datetime,
) -> str | None:
    """Return a consumed/unknown lock for the exact declared strategy scope.

    ``owned_group_ids`` comes from independently verified strategy/assignment
    lineage; FrozenOmsGroup itself carries no strategy identity. An empty set
    means verified absence, not missing recovery coverage. Accepted entry ACKs
    consume a session even after zero-fill cancellation. Rejected/cancelled
    submissions with no broker identity do not. Unknown outcomes block retry.
    """
    zone = ZoneInfo("America/New_York")
    if (not isinstance(groups, tuple)
            or policy is not SessionAcquisitionPolicy.ONCE_PER_EXTENDED_SESSION
            or not isinstance(owned_group_ids, frozenset)
            or any(type(value) is not str or not value for value in owned_group_ids)
            or type(session_date) is not date
            or session not in {"premarket", "afterhours"}
            or type(account_id) is not str or not account_id
            or type(ticker) is not str or not ticker or ticker != ticker.upper()
            or not isinstance(as_of, datetime) or as_of.utcoffset() is None):
        raise ValueError("Invalid session acquisition policy or scope")
    local_as_of = as_of.astimezone(zone)
    if local_as_of.date() != session_date:
        raise ValueError("Session acquisition as-of date differs from scope")
    inventory = set()
    for group in groups:
        if not isinstance(group, FrozenOmsGroup):
            raise ValueError("Session acquisition requires normalized frozen OMS groups")
        if group.group_id in owned_group_ids:
            if group.group_id in inventory:
                raise ValueError("Session acquisition has duplicate owned group evidence")
            inventory.add(group.group_id)
    if inventory != owned_group_ids:
        raise ValueError("Session acquisition is missing declared owned group evidence")
    accepted = unresolved = False
    for group in groups:
        if (group.group_id not in owned_group_ids or group.account_id != account_id
                or group.intent.ticker != ticker or group.intent.action != "enter_long"):
            continue
        clocks = (group.intent.event_time, group.created_at, group.updated_at)
        if (any(not isinstance(at, datetime) or at.utcoffset() is None for at in clocks)
                or any(at > as_of for at in clocks)):
            raise ValueError("Session acquisition source has unavailable or future clocks")
        if group.created_at > group.updated_at:
            raise ValueError("Session acquisition source has reversed clocks")
        at = group.intent.event_time.astimezone(zone)
        minute = at.hour * 60 + at.minute
        source_session = ("premarket" if 240 <= minute < 570 else
                          "afterhours" if 960 <= minute < 1200 else None)
        if source_session is None:
            raise ValueError("Session acquisition source is outside extended sessions")
        if at.date() != session_date or source_session != session:
            continue
        if (not group.group_id or not isinstance(group.state, OrderManagementState)
                or len(set(group.broker_order_ids)) != len(group.broker_order_ids)
                or set(group.broker_order_roles) - set(group.broker_order_ids)):
            raise ValueError("Session acquisition source has inconsistent broker ownership")
        if any(role == "entry" for role in group.broker_order_roles.values()):
            accepted = True
        elif (group.broker_order_ids or group.state not in {
                OrderManagementState.REJECTED, OrderManagementState.CANCELLED}):
            unresolved = True
        elif group.filled_quantity != 0:
            raise ValueError("Session acquisition filled source lacks an entry acknowledgement")
    return ("accepted_batch_consumed_session" if accepted else
            "unresolved_submission" if unresolved else None)
