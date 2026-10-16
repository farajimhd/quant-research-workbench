"""Owned current-path programme assembly using native OMS and shared Portfolio.

Construction does not initialize a broker, start services or place orders.
Every publication retains its original typed source and actual native receipt.
"""
import asyncio
from contextlib import asynccontextmanager
from dataclasses import asdict

from src.trading_runtime.program_broker_gateway import ProgramBrokerGateway
from src.trading_runtime.program_portfolio_transitions import (
    ProgramPortfolioTransitionAuthority, apply_program_portfolio_transition)
from .program_execution_journal import ProgramExecutionJournal


class ProgramRuntimeAssembly:
    def __init__(self, *, journal, portfolio_authority, broker, attempt_id):
        from uuid import UUID
        if (type(journal) is not ProgramExecutionJournal
                or type(portfolio_authority) is not ProgramPortfolioTransitionAuthority
                or portfolio_authority.writer is not journal.writer
                or str(UUID(attempt_id)) != attempt_id
                or portfolio_authority.record_journal.attempt_id != attempt_id):
            raise TypeError('Program assembly requires one concrete writer, journal and Portfolio authority')
        self.journal, self.portfolio_authority, self.attempt_id = journal, portfolio_authority, attempt_id
        self.broker = ProgramBrokerGateway(broker, journal)
        self.runtime = None
        self.source_context = None
        self.signal_runtime = None
        self._decision_intents = {}
        self._post_fill_diagnostics = {}
        self._lock, self._task, self._depth = asyncio.Lock(), None, 0

    def bind(self, runtime):
        from src.trading_runtime.runtime import TradingRuntime
        if type(runtime) is not TradingRuntime or self.runtime is not None:
            raise TypeError('Program assembly binds exactly one native runtime')
        self.runtime = runtime
        self.require_runtime(runtime, require_source=False)
        self.broker.bind_assembly(self)
        runtime.risk_supervisor._program_execution_assembly = self
        from .program_broker_executions import ProgramBrokerExecutionIntake
        self.execution_intake = ProgramBrokerExecutionIntake(self)

    def require_source_context(self, context, *, certified_configuration, signal_runtime):
        from .signal_execution_context import SignalExecutionContext
        from .certified_program_configuration import CertifiedProgramConfiguration
        from .signal_stream_runtime_service import SignalStreamRuntime
        from src.trading_runtime.strategy_configuration_selection import restore_configuration_selection
        if (type(context) is not SignalExecutionContext
                or type(certified_configuration) is not CertifiedProgramConfiguration
                or type(signal_runtime) is not SignalStreamRuntime or self.runtime is None
                or self.source_context is not None):
            raise TypeError('Program source requires one exact typed execution context and certified configuration')
        selected = restore_configuration_selection(certified_configuration.selection.rows)
        if (context.run_id != self.runtime.run_id or context.source_journal_run_id != self.journal.run_id
                or context.mode != self.journal.mode
                or context.session_key != self.runtime.config.anchor_date.isoformat()
                or context.configuration_revision != certified_configuration.revision['revision_id']
                or selected.header['content_hash'] != self.journal.writer._live_v4_lease.configuration_hash
                or signal_runtime._typed_scope != (context.configuration_revision, context.source_revision)
                or getattr(signal_runtime, 'source_context', None) != context):
            raise ValueError('Program source context differs from actual run, day, mode or certified selection')
        self.source_context, self.signal_runtime = context, signal_runtime
        return context

    def require_runtime(self, runtime, *, require_source=True):
        from src.trading_runtime.columnar_program_actor import AssignedProgramStrategy
        from src.trading_runtime.order_management import OrderManagementEngine
        from src.trading_runtime.portfolio import PortfolioManagementEngine
        from src.trading_runtime.configuration_execution_risk import ConfigurationExecutionRisk
        self.journal.require_current()
        if getattr(self, 'execution_intake', None) is not None:
            self.execution_intake.require_current()
        registration = self.journal.writer._live_v4_lease.registration
        actor = runtime.strategy
        risk = getattr(actor, 'execution_risk', None)
        if (self.runtime is not runtime or runtime.journal is not self.journal
                or runtime.broker is not self.broker or runtime.config.mode.value != self.journal.mode
                or runtime.run_id != self.journal.run_id or type(actor) is not AssignedProgramStrategy
                or type(runtime.order_manager) is not OrderManagementEngine
                or runtime.order_manager.broker is not self.broker
                or runtime.order_manager._program_state_callback_failure is not None
                or type(runtime.portfolio) is not PortfolioManagementEngine
                or not runtime.portfolio._typed_recovery or runtime.portfolio._typed_backtest_recovery
                or runtime.portfolio._typed_admission_poisoned
                or (runtime.config.strategy_id, runtime.config.strategy_revision) != registration.key
                or actor.program_release.header['content_hash'] != registration.expected_content_hash
                or registration.requires_execution_risk and type(risk) is not ConfigurationExecutionRisk
                or risk is not None and risk.header['configuration_hash']
                   != self.journal.writer._live_v4_lease.configuration_hash):
            raise RuntimeError('Program runtime differs from its concrete native owned assembly')
        if require_source and (self.source_context is None or self.signal_runtime is None
                or getattr(self.signal_runtime, 'source_context', None) != self.source_context):
            raise RuntimeError('Program runtime requires its actual sealed Signal Stream execution context')
        return self.journal.mode

    @asynccontextmanager
    async def operation(self):
        task = asyncio.current_task()
        if self._task is task:
            self._depth += 1
            try:
                yield
            finally:
                self._depth -= 1
            return
        async with self._lock:
            self._task, self._depth = task, 1
            try:
                yield
            finally:
                self._task, self._depth = None, 0

    async def flush(self, *, qualified_decisions=None):
        from .program_pending_publication import commit_program_pending_records
        from src.trading_runtime.runtime import typed_run_config_payload
        self.require_runtime(self.runtime)
        publications = await commit_program_pending_records(self.journal, attempt_id=self.attempt_id,
            expected_config=typed_run_config_payload(self.runtime.config),
            run_month=self.runtime.config.anchor_date.replace(day=1),
            source_cursor='program-runtime:' + (self.runtime.last_event_time.isoformat()
                if self.runtime.last_event_time is not None else 'initialize'),
            qualified_decisions=qualified_decisions)
        from src.trading_runtime.rule_decision_publication import QualifiedRuleDecisionBatch
        for unit, receipt in publications:
            if type(unit) is QualifiedRuleDecisionBatch:
                for row in unit.unit.intents:
                    identity = row['strategy_intent_id']
                    if identity in self._decision_intents:
                        raise RuntimeError('Program semantic intent already has a committed decision')
                    self._decision_intents[identity] = (unit, receipt)
                    if len(self._decision_intents) > self.journal.max_pending_records:
                        raise RuntimeError('Unconsumed qualified programme decisions exceeded bounded custody')
        self.broker._retain_receipts(publications)
        return publications

    async def transition(self, account_id, transition, arguments):
        async with self.operation():
            await self.flush()
            head = await self.portfolio_authority.verify_current_head(self.runtime.portfolio, account_id)
            receipt = await apply_program_portfolio_transition(self.runtime.portfolio,
                self.portfolio_authority, account_id=account_id,
                prior_revision=head.state_revision, prior_hash=head.snapshot_hash,
                transition=transition, arguments=arguments)
            await self.journal.refresh_native_prefix()
            return receipt

    async def synchronize(self):
        async with self.operation():
            await self.flush()
            orders = await self.broker.live_orders()
            for account_id in self.runtime.config.account_ids:
                summary, ledger, positions = await asyncio.gather(
                    self.broker.account_summary(account_id), self.broker.account_ledger(account_id),
                    self.broker.positions(account_id))
                await self.portfolio_authority.synchronize_broker_snapshot(self.runtime.portfolio,
                    account_id=account_id, summary=summary, ledger=ledger, positions=positions,
                    open_orders=[row for row in orders if row.account == account_id])
                await self.journal.refresh_native_prefix()

    async def complete_broker_boundary(self, at):
        """Advance a committed QMD clock using actual IBKR executions only."""
        from datetime import datetime, timezone
        async with self.operation():
            self.require_runtime(self.runtime)
            if (self.journal.mode not in {'paper', 'live'} or not isinstance(at, datetime)
                    or at.tzinfo is None or at.utcoffset() is None or at > datetime.now(timezone.utc)
                    or self.runtime.last_event_time is not None and at < self.runtime.last_event_time):
                raise ValueError('Native broker boundary requires a monotonic completed current clock')
            sequence, digest, cutoff = self.signal_runtime.typed_source_boundary(at)
            if sequence < 1 or cutoff != at or not digest:
                raise ValueError('Native broker boundary lacks committed contextual QMD source')
            self.runtime.last_event_time = at
            result = await self.execution_intake.poll(at=at)
            if result.deferred:
                # Native broker observations can be newer than the completed
                # source boundary. Retain those exact fills for the next clock;
                # never expose future positions or reconcile them into this one.
                return result
            await self.runtime.order_manager.reconcile()
            await self.flush()
            await self.synchronize()
            await self.assess_post_fill_risk(at)
            await self.runtime.order_manager.enforce_entry_body_triggers(at)
            await self.runtime.order_manager.advance_adaptive_execution(at)
            await self.runtime.order_manager.expire_entry_deadlines(at)
            await self.flush()
            return result

    async def assess_post_fill_risk(self, at):
        """Classify actual fill excess separately from pre-submit admission."""
        from decimal import Decimal
        from src.trading_runtime.configuration_execution_risk import evaluate_filled_execution_risk
        from hashlib import sha256
        from src.trading_runtime.journal_contract import canonical_json
        from src.trading_runtime.oms_assignment_observation import observe_assignment_orders
        from src.trading_runtime.signals import StrategySignal
        actor = self.runtime.strategy
        contract = actor.execution_risk
        if contract is None:
            return
        header = contract.header
        for assignment in actor.assignments():
            position = actor.position_for(assignment.assignment_id)
            if not position.position_open:
                continue
            observation = observe_assignment_orders(self.runtime.order_manager,
                account_id=assignment.account_id, assignment_id=assignment.assignment_id,
                ticker=assignment.ticker, at=at)
            stops = {Decimal(str(order.stop_price)) for order in observation.working_orders
                     if order.role in {'protective_stop', 'trailing_stop'} and order.stop_price > 0}
            reasons = []
            if actor.execution_state.pending_fee_count:
                reasons.append('post_fill_commissions_pending')
            if len(stops) != 1 or observation.unobserved_order_ids:
                reasons.append('post_fill_protection_unobserved')
            else:
                stop, quantity, average = next(iter(stops)), position.quantity, position.average_price
                classified = evaluate_filled_execution_risk(contract, quantity=quantity,
                    average_price=average, stop_price=stop, episode_net_pnl=position.episode_net_pnl,
                    filled_position=position)
                reasons.extend(classified.reasons)
            if not reasons:
                continue
            identity = sha256(canonical_json({'run_id': self.runtime.run_id,
                'assignment_id': assignment.assignment_id,
                'opened_at': position.opened_at.isoformat() if position.opened_at is not None else None,
                'quantity': str(position.quantity), 'average': str(position.average_price),
                'episode_net_pnl': str(position.episode_net_pnl), 'stops': sorted(map(str, stops)),
                'reasons': reasons, 'risk_hash': header['content_hash']}).encode()).hexdigest()
            if self._post_fill_diagnostics.get(assignment.assignment_id) == identity:
                continue
            reason = 'program:' + ','.join(reasons)
            signal = StrategySignal(identity, 'program_post_fill_risk', assignment.ticker, at,
                'wait', 'neutral', 0., 1., reason, working_timeframe='1s',
                metadata=dict(assignment_id=assignment.assignment_id,
                    reference_price=float(position.average_price), status='completed',
                    reason_code=reason, reason_detail=','.join(reasons),
                    correlation_id=identity, causation_id=contract.header['content_hash']))
            self.journal.append(run_id=self.runtime.run_id, category='strategy_decision', entity_type='signal',
                entity_id=identity, account_id=assignment.account_id, event_time=at,
                payload={**signal.payload(), 'strategy_id': actor.strategy_id,
                    'strategy_revision': actor.revision, 'correlation_id': identity,
                    'causation_id': contract.header['content_hash']})
            await self.flush()
            self._post_fill_diagnostics[assignment.assignment_id] = identity
            if any(reason.endswith('_exceeded') for reason in reasons):
                await self.transition(assignment.account_id, 'set_control',
                    {'mode': 'entries_paused', 'reason': ','.join(reasons)})

    async def execute_evaluation(self, evaluation, *, account_id, assignment_id):
        """Native semantic intent -> durable shared approval -> native OMS."""
        from decimal import Decimal
        from src.trading_runtime.portfolio import _worst_entry_price, PortfolioDecisionStatus
        from dataclasses import replace
        results = []
        async with self.operation():
            self.require_runtime(self.runtime)
            for intent in evaluation.intents:
                decision_source = self._decision_intents.pop(intent.intent_id, None)
                if decision_source is None:
                    raise RuntimeError('Program order requires its qualified native decision receipt before admission')
                if intent.metadata:
                    raise ValueError('Program semantic intent must retain its normalized original source')
                source = self.journal.program_intent_source(intent.intent_id, account_id)
                if source.intent != intent or source.assignment_id != assignment_id:
                    raise RuntimeError('Program submission differs from its committed original semantic intent')
                if intent.action == 'cancel_entry':
                    await self.runtime.order_manager.cancel_entry_acquisition(
                        replace(intent, action='exit', metadata={'assignment_id': assignment_id,
                            'cancel_entry_acquisition': True}), account_id=account_id)
                    await self.runtime.order_manager.reconcile()
                    await self.flush()
                    results.append({'decision': {'status': 'acquisition_cancellation_requested'},
                        'order_group': None})
                    continue
                if intent.action in {'enter_long', 'add_long'}:
                    await self.synchronize()
                outcome = await self.transition(account_id, 'approve_intent',
                    {'intent': intent, 'assignment_id': assignment_id})
                await self.journal.retain_program_admission(self.portfolio_authority,
                    self.runtime.portfolio, outcome)
                decision, approved = outcome.decision, outcome.approved_intent
                if approved is None:
                    await self.runtime._record_intent_rejection(intent, account_id, decision)
                    await self.flush()
                    results.append({'decision': decision.payload(), 'order_group': None})
                    continue
                if (approved.action in {'enter_long', 'add_long'}
                        and getattr(self.runtime.strategy, 'execution_risk', None) is not None):
                    price = _worst_entry_price(approved)
                    quote = self.runtime.order_manager._execution_quote(approved)
                    if quote is not None:
                        price = max(price, quote.ask)
                    risk = self.runtime._program_acquisition_risk(approved, assignment_id=assignment_id,
                        requested_cash=Decimal(str(approved.quantity)) * Decimal(str(price)),
                        execution_price=Decimal(str(price)))
                    if not risk.allowed:
                        await self.transition(account_id, 'release_intent',
                            {'intent_id': approved.intent_id, 'reason': 'program_execution_risk_rejected'})
                        rejected = replace(decision, status=PortfolioDecisionStatus.REJECTED,
                            approved_quantity=0., approved_notional=0., reasons=risk.reasons)
                        await self.runtime._record_intent_rejection(approved, account_id, rejected)
                        await self.flush()
                        results.append({'decision': rejected.payload(), 'order_group': None})
                        continue
                assignment = self.runtime._assignment_for_intent(approved)
                if approved.action == 'enter_long' and assignment is not None:
                    self.runtime.control_plane.campaigns.reserve(assignment)
                group = await self.runtime.order_manager.submit_intent(approved,
                    account_id=account_id, event=None,
                    **({'stop_only_reduction': True} if approved.action == 'reduce_long' else {}))
                await self.flush()
                results.append({'decision': decision.payload(), 'order_group': asdict(group)})
        return results

    async def reject_intent(self, intent, *, account_id, assignment_id, decision):
        async with self.operation():
            if self._decision_intents.pop(intent.intent_id, None) is None:
                raise RuntimeError('Program rejection lacks its qualified native decision receipt')
            source = self.journal.program_intent_source(intent.intent_id, account_id)
            if source.intent != intent or source.assignment_id != assignment_id:
                raise RuntimeError('Program rejection differs from its committed original semantic intent')
            await self.runtime._record_intent_rejection(intent, account_id, decision)
            await self.flush()


def build_program_runtime(*, config, strategy, journal, portfolio, portfolio_authority,
        broker, intent_planner, attempt_id, control_plane=None, strategy_configuration=None):
    """Bind a sealed native assembly; broker initialization remains explicit."""
    from src.trading_runtime.runtime import TradingRuntime
    if type(journal) is ProgramExecutionJournal and journal.fenced_sequence:
        raise RuntimeError('Existing program event prefix requires explicit verified actor/OMS/execution recovery before assembly')
    assembly = ProgramRuntimeAssembly(journal=journal, portfolio_authority=portfolio_authority,
        broker=broker, attempt_id=attempt_id)
    runtime = TradingRuntime(config, assembly.broker, strategy, journal,
        portfolio=portfolio, intent_planner=intent_planner, control_plane=control_plane,
        strategy_configuration=strategy_configuration, program_execution_assembly=assembly)
    assembly.bind(runtime)
    return runtime
