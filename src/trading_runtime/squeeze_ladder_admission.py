"""Once-per-extended-session admission from normalized OMS group evidence.

No mutable sidecar lock is authoritative. Cold startup supplies verified
normalized snapshots; the coordinator updates its bounded in-memory projection
from committed acknowledgements. A filled-trade counter is insufficient.
"""
from datetime import date
from zoneinfo import ZoneInfo

from .arte_oms_projection import FrozenOmsGroup
from .order_management import OrderManagementState


def ladder_admission_lock(groups: tuple[FrozenOmsGroup, ...], *, session_date: date,
                          session: str, account_id: str, ticker: str) -> str | None:
    """Return accepted/unresolved lock evidence, independent of remaining shares.

    PM and AH are distinct sessions. Cancelled accepted parents consume the
    session even with zero fills. A genuinely rejected, never-acknowledged
    submission does not consume it; an unknown outcome blocks new admission.
    Source prefix/coverage and as-of filtering are the cold reader's authority.
    """
    if (not isinstance(groups, tuple) or type(session_date) is not date
            or session not in {"premarket", "afterhours"}
            or not account_id or not ticker or ticker != ticker.upper()):
        raise ValueError("Invalid ladder session admission identity")
    accepted = unresolved = False
    for group in groups:
        if not isinstance(group, FrozenOmsGroup):
            raise ValueError("Ladder admission requires normalized frozen OMS snapshots")
        profile = group.intent.resolved_protection_profile()
        if (group.account_id != account_id or group.intent.ticker != ticker
                or profile is None or profile.identity != "early-squeeze-ladder-prepared@1"
                or group.intent.action != "enter_long"):
            continue
        at = group.intent.event_time
        if at.tzinfo is None:
            raise ValueError("Ladder admission source lacks a timezone")
        at = at.astimezone(ZoneInfo("America/New_York"))
        minute = at.hour * 60 + at.minute
        source_session = ("premarket" if 240 <= minute < 570 else
                          "afterhours" if 960 <= minute < 1200 else None)
        if source_session is None:
            raise ValueError("Ladder admission source is outside extended sessions")
        if at.date() != session_date or source_session != session:
            continue
        if (not group.group_id or set(group.broker_order_roles) - set(group.broker_order_ids)
                or not isinstance(group.state, OrderManagementState)):
            raise ValueError("Ladder admission source has inconsistent broker ownership")
        if any(role == "entry" for role in group.broker_order_roles.values()):
            accepted = True
        elif group.broker_order_ids or group.state not in {OrderManagementState.REJECTED, OrderManagementState.CANCELLED}:
            unresolved = True
    return "accepted_batch_consumed_session" if accepted else "unresolved_submission" if unresolved else None
