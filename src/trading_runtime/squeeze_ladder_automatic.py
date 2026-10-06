"""Automatic fixed-lot acquisitions through native financial authorities.

No profile name or strategy number selects this behavior. The consuming release
must declare the exact policy and immutable assignment permissions. Any control
intervention closes this baseline; dynamic controls require another contract.
"""
from dataclasses import dataclass
from datetime import date

from .execution_policies import AddProtectionPolicy
from .session_acquisition_admission import SessionAcquisitionPolicy, session_acquisition_lock
from .strategy_engine import AssignmentStatus, StrategyAssignment, StrategyPermissions
from .signals import StrategyEvaluation


@dataclass(frozen=True, slots=True)
class AutomaticLadderPolicy:
    policy_id: str = 'fixed-swing-three-equal-once-session@1'
    acquisition_policy: SessionAcquisitionPolicy = SessionAcquisitionPolicy.ONCE_PER_EXTENDED_SESSION
    permissions: StrategyPermissions = StrategyPermissions(observe=True, enter=True,
        add=False, reduce=True, exit=True, reenter=False)

    def __post_init__(self):
        if (self.policy_id != 'fixed-swing-three-equal-once-session@1'
                or self.acquisition_policy is not SessionAcquisitionPolicy.ONCE_PER_EXTENDED_SESSION
                or self.permissions != StrategyPermissions(observe=True, enter=True,
                    add=False, reduce=True, exit=True, reenter=False)):
            raise ValueError('Automatic ladder requires its complete fixed baseline policy')

    def payload(self):
        return dict(policy_id=self.policy_id, acquisition_policy=self.acquisition_policy.value,
            protection_add_policy=AddProtectionPolicy.INDEPENDENT_FIXED_LOTS.value,
            lot_count=3, allocation='equal', stop='confirmed_frozen_swing_low',
            assignment_permissions=self.permissions.payload(),
            assignment_id_rule='strategy-revision-account-ticker-v1',
            control_policy='immutable_permissions_reject_any_control_command',
            capital_policy='native_portfolio_single_aggregate_request')


@dataclass(frozen=True, slots=True)
class AutomaticLadderRequest:
    """In-memory source companion; it becomes normalized evidence, never metadata."""
    admission: object
    market_context: object
    assignment_id: str
    policy: AutomaticLadderPolicy

    @property
    def intent(self):
        return self.admission.intent

    def verify(self, *, run_id, account_id, session_date):
        from src.backend.backtest_squeeze_ladder_admission import build_ladder_proposal_intent
        from src.backend.backtest_squeeze_ladder_evidence import project_ladder_evidence
        from src.backend.backtest_squeeze_ladder_readback import reconstruct_ladder_market_decision
        context = self.market_context
        decision = self.admission.market_decision
        if (not isinstance(self.policy, AutomaticLadderPolicy) or not self.assignment_id
                or self.admission.reason != 'capital_request_proposed' or self.intent is None
                or context.run_id != run_id or context.session_date != session_date):
            raise ValueError('Automatic ladder request has foreign source or admission')
        context.verify_policy(self.policy)
        if self.assignment_id != f'strategy-{context.configuration.strategy_number}:{account_id}:{decision.setup.ticker}':
            raise ValueError('Automatic ladder assignment differs from its declared ownership rule')
        rows = project_ladder_evidence(self.admission, run_id=run_id,
            batch_id='00000000-0000-0000-0000-000000000001',
            parent_record_id='00000000-0000-0000-0000-000000000002',
            assignment_id=self.assignment_id, session_date=session_date)
        expected = reconstruct_ladder_market_decision(rows,
            observations=context.observations, market=context.market,
            v7=context.v7, pivots=context.pivots, tick_int=context.tick_int,
            stop_buffer_ticks=context.stop_buffer_ticks,
            break_buffer_ticks=context.break_buffer_ticks, target_count=3, allocation='equal',
            geometry_policy=context.geometry_policy, gate_policy=context.gate_policy)
        if expected != decision:
            raise ValueError('Automatic ladder proposal differs from certified causal source')
        intent = build_ladder_proposal_intent(expected, session_date=session_date,
            assignment_id=self.assignment_id, account_id=account_id)
        if self.intent != intent:
            raise ValueError('Automatic ladder intent differs from exact source proposal')
        profile = intent.protection_profile
        if (profile.add_policy != AddProtectionPolicy.INDEPENDENT_FIXED_LOTS
                or len(profile.slices) != 3
                or any(abs(item.quantity_fraction - 1/3) > 1e-12 for item in profile.slices)):
            raise ValueError('Automatic ladder requires three equal independently protected lots')


async def submit_automatic_ladder(runtime, decision, *, assignment: StrategyAssignment,
                                  market_context, policy: AutomaticLadderPolicy):
    """Read actual broker/OMS/Portfolio state before the one shared capital request.

    The caller advances the completed broker boundary first. This method makes
    no fills, allocations, cash copies or permissions. Journal persistence and
    command fencing remain the native runtime's responsibility.
    """
    from src.backend.backtest_strategy_one_financial import read_strategy_one_financial_view
    from src.backend.backtest_squeeze_ladder_admission import admit_ladder_proposal
    from .arte_oms_projection import freeze_oms_group
    from .runtime import RunMode
    if (runtime.config.mode is not RunMode.BACKTEST
            or type(assignment) is not StrategyAssignment
            or assignment.account_id not in runtime.config.account_ids
            or assignment.strategy_id != runtime.config.strategy_id
            or assignment.strategy_revision != runtime.config.strategy_revision
            or assignment.permissions != policy.permissions
            or assignment.status is not AssignmentStatus.WATCHING
            or market_context.run_id != runtime.run_id
            or market_context.session_date != runtime.config.anchor_date):
        raise ValueError('Automatic ladder lacks immutable native assignment authority')
    market_context.verify_policy(policy)
    from src.backend.backtest_market_data import market_day_boundary
    if (runtime.last_event_time != market_day_boundary(runtime.config.anchor_date, decision.boundary_ms)
            or market_context.configuration.payload['strategy']['strategy_id'] != runtime.config.strategy_id
            or market_context.configuration.strategy_number != runtime.config.strategy_revision
            or assignment not in runtime.strategy.assignments()):
        raise ValueError('Automatic ladder lacks its exact completed runtime owner/boundary')
    if not runtime.journal._automatic_fresh_run:
        # Resumed controls have not been independently bound by this baseline.
        raise ValueError('Automatic ladder requires a fresh verified control-free run')
    if runtime.journal.has_control_intervention():
        raise ValueError('Automatic ladder baseline rejects control-command intervention')
    financial = await read_strategy_one_financial_view(assignment, runtime.broker, runtime.order_manager)
    if runtime.portfolio.has_pending_entry_requests(assignment.account_id):
        # Conservatively stop acquisition while Portfolio owns deferred account
        # requests; never manufacture a false pending-capital bit.
        return [{'decision': {'status':'entry_fill_pending'}, 'order_group':None}]
    snapshots = runtime.order_manager.snapshots_for_assignment(assignment.account_id, assignment.assignment_id)
    wanted = {row.group_id for row in snapshots}
    groups = tuple(freeze_oms_group(runtime.order_manager._groups[identity])
                   for identity in sorted(wanted) if identity in runtime.order_manager._groups)
    if len(groups) != len(wanted):
        raise ValueError('Automatic ladder OMS ownership is incomplete')
    at = decision.boundary_ms
    session = 'premarket' if 0 < at < 19_800_000 else 'afterhours' if 43_200_000 <= at < 57_600_000 else None
    if session is None:
        raise ValueError('Automatic ladder requires an extended-session boundary')
    from src.backend.backtest_market_data import market_day_boundary
    locked = session_acquisition_lock(groups, policy=policy.acquisition_policy,
        owned_group_ids=frozenset(wanted), session_date=runtime.config.anchor_date,
        session=session, account_id=assignment.account_id, ticker=assignment.ticker,
        as_of=market_day_boundary(runtime.config.anchor_date, at))
    if locked:
        return [{'decision': {'status':locked}, 'order_group':None}]
    admission = admit_ladder_proposal(decision, financial,
        session_date=runtime.config.anchor_date, groups=groups)
    if admission.intent is None:
        return [{'decision': {'status':admission.reason}, 'order_group':None}]
    request = AutomaticLadderRequest(admission, market_context, assignment.assignment_id, policy)
    request.verify(run_id=runtime.run_id, account_id=assignment.account_id,
                   session_date=runtime.config.anchor_date)
    return await runtime._execute_intents(StrategyEvaluation(intents=(request.intent,)),
        assignment.account_id, None, automatic_entry=request)
