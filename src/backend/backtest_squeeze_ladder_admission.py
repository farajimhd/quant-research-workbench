"""Prepared ladder financial admission to one shared Portfolio capital request."""
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from math import isfinite
from uuid import NAMESPACE_URL, uuid5
from zoneinfo import ZoneInfo

from src.backend.backtest_squeeze_ladder_entry import LadderBreakoutDecision
from src.trading_runtime.arte_oms_projection import FrozenOmsGroup
from src.trading_runtime.execution_policies import (
    ExecutionEnvelope, ExecutionPolicy, ExecutionPolicyName, PartialFillPolicy,
)
from src.trading_runtime.signals import CapitalRequest, StrategyIntent
from src.trading_runtime.squeeze_ladder_admission import ladder_admission_lock
from src.trading_runtime.strategy_engine import AssignmentStatus, StrategyPermissions
from src.trading_runtime.strategy_one_stateful import StrategyOneFinancialView


@dataclass(frozen=True, slots=True)
class LadderAdmissionDecision:
    reason: str
    market_decision: LadderBreakoutDecision
    intent: StrategyIntent | None = None


def admit_ladder_proposal(decision: LadderBreakoutDecision, financial: StrategyOneFinancialView, *,
                          session_date: date, groups: tuple[FrozenOmsGroup, ...]) -> LadderAdmissionDecision:
    """Consume verified as-of OMS snapshots; never spend, reserve or order.

    The caller supplies the committed current-run prefix. Empty groups means
    verified no prior acquisitions, not unavailable recovery evidence. This
    helper is prepared only; full native publication must bind that proof and
    persist the market decision's named scalar evidence before order admission.
    """
    if (not isinstance(decision, LadderBreakoutDecision) or not isinstance(financial, StrategyOneFinancialView)
            or type(session_date) is not date or not isinstance(groups, tuple)
            or financial.ticker != decision.setup.ticker or financial.ticker != financial.ticker.upper()
            or not financial.account_id or not financial.assignment_id
            or not isinstance(financial.status, AssignmentStatus)
            or not isinstance(financial.permissions, StrategyPermissions)
            or type(financial.position_quantity) not in (int, float)
            or not isfinite(financial.position_quantity) or financial.position_quantity < 0
            or any(type(value) is not bool for value in
                   (financial.pending_entry, financial.pending_exit, financial.pending_capital_request))):
        raise ValueError("Ladder financial admission has invalid identity/state")
    def reject(reason):
        return LadderAdmissionDecision(reason, decision)
    if decision.reason != 'entry_proposed' or decision.protection is None:
        return reject('market_proposal_unavailable')
    boundary = datetime.combine(session_date, time(4), tzinfo=ZoneInfo('America/New_York')) + timedelta(milliseconds=decision.boundary_ms)
    minute = boundary.hour * 60 + boundary.minute
    session = ('premarket' if 240 <= minute < 570 else 'afterhours' if 960 <= minute < 1200 else None)
    if session is None or boundary.date() != session_date:
        return reject('extended_session_required')
    at = boundary.astimezone(timezone.utc)
    for group in groups:
        if (not isinstance(group, FrozenOmsGroup) or group.updated_at.tzinfo is None
                or group.intent.event_time.tzinfo is None
                or group.updated_at > at or group.intent.event_time > at):
            raise ValueError("Ladder admission snapshot is unavailable or from the future")
    locked = ladder_admission_lock(groups, session_date=session_date, session=session,
                                   account_id=financial.account_id, ticker=financial.ticker)
    if locked:
        return reject(locked)
    if financial.position_quantity > 0:
        return reject('position_requires_management')
    if financial.pending_exit or financial.status == AssignmentStatus.EXIT_PENDING:
        return reject('exit_fill_pending')
    if financial.pending_entry or financial.pending_capital_request or financial.status == AssignmentStatus.ENTRY_PENDING:
        return reject('entry_fill_pending')
    if (financial.status in {AssignmentStatus.DISABLED, AssignmentStatus.PAUSED,
                             AssignmentStatus.COMPLETED, AssignmentStatus.ERROR}
            or not financial.permissions.observe or not financial.permissions.enter):
        return reject('entry_permission_closed')
    if decision.entry_limit_int is None or decision.setup.stop is None:
        raise ValueError("Prepared ladder proposal lacks complete protection geometry")
    profile = decision.protection
    entry, stop = decision.entry_limit_int / 10000., decision.setup.stop.stop_int / 10000.
    if (profile.identity != 'early-squeeze-ladder-prepared@1' or len(profile.slices) not in (2,3,5)
            or not 0 < stop < entry
            or any(item.stop.price != stop or item.profit_target_price is None
                   or item.profit_target_price <= entry for item in profile.slices)):
        raise ValueError("Prepared ladder proposal protection differs from frozen setup")
    identity = (f'prepared-ladder:{session_date}:{financial.assignment_id}:{financial.account_id}:'
                f'{financial.ticker}:{decision.boundary_ms}:{decision.setup.admission_boundary_ms}')
    intent = StrategyIntent(intent_id=str(uuid5(NAMESPACE_URL, identity)), ticker=financial.ticker,
        event_time=at, action='enter_long', quantity=0., reference_price=entry,
        capital_request=CapitalRequest(mode='mandate_fraction', value=1/3),
        invalidation_price=stop, profit_target_price=None,
        execution_policy=ExecutionPolicy(policy_id='strategy-adaptive_urgent',
            name=ExecutionPolicyName.ADAPTIVE_URGENT,
            envelope=ExecutionEnvelope(persist_until_cancelled=True, maximum_buy_price=entry),
            partial_fill_policy=PartialFillPolicy.COMPLETE_REMAINDER, quote_source='qmd'),
        protection_profile=profile, urgency='urgent', time_in_force='', outside_rth=True,
        reason='prepared_ladder_entry', metadata={})
    return LadderAdmissionDecision('capital_request_proposed', decision, intent)
