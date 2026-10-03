"""Strategy 43 bridge to the existing Portfolio, OMS, broker and V4 publisher.

The session's certified source reader supplies exact fact references. This
adapter neither calculates market indicators nor owns a shadow cash ledger,
position book, exchange or execution clock.
"""
from __future__ import annotations

from math import isfinite
from uuid import UUID, NAMESPACE_URL, uuid5
from types import SimpleNamespace

from .backtest_strategy_forty_three_journal import StrategyFortyThreeJournal
from .backtest_strategy_forty_three_publisher import StrategyFortyThreePublisher
from src.trading_runtime.runtime import TradingRuntime, RunMode
from src.trading_runtime.order_management import OrderManagementState
from src.trading_runtime.ibkr_schema import OPEN_ORDER_STATUSES
from src.trading_runtime.signals import StrategyIntent
from src.trading_runtime.execution_policies import ExecutionPolicy, ExecutionPolicyName, ExecutionEnvelope
from src.trading_runtime.strategy_forty_three_orders import StrategyFortyThreeOrderPlanner
from src.trading_runtime.strategy_forty_three_coordinator import SubmissionReceipt, CompletedLegFacts
from src.trading_runtime.strategy_forty_three_oms import replace_leg_stop
from src.trading_runtime.strategy_forty_three_rules import (
    STRATEGY_ID, STRATEGY_NUMBER, entry_intents, protective_fee_reserve, native_price,
)

_TERMINAL_RESERVATIONS = {"released", "filled", "cancelled", "rejected", "policy_blocked"}


class StrategyFortyThreeNativePort:
    def __init__(self, *, runtime, publisher, source_token, source_fact_id, session_end_ms=19_800_000):
        if (type(runtime) is not TradingRuntime or runtime.config.mode != RunMode.BACKTEST
                or (runtime.config.strategy_id, runtime.config.strategy_revision) != (STRATEGY_ID, STRATEGY_NUMBER)
                or type(runtime.journal) is not StrategyFortyThreeJournal
                or type(publisher) is not StrategyFortyThreePublisher
                or publisher.journal is not runtime.journal
                or publisher.writer.run_id != runtime.run_id or runtime.order_manager is None
                or type(runtime.intent_planner) is not StrategyFortyThreeOrderPlanner
                or not source_token or not callable(source_fact_id)
                or type(session_end_ms) is not int or session_end_ms % 1000
                or not 10_000 < session_end_ms <= 57_600_000):
            raise ValueError("Strategy 43 native port requires its exact Backtest authorities")
        self.runtime, self.publisher = runtime, publisher
        self.source_token, self.source_fact_id = source_token, source_fact_id
        self.session_end_ms = session_end_ms
        self._published_batches = {}
        self._leg_liquidations = {}
        self._cutoff_time = None

    async def _fence(self):
        # A running publisher owns a fixed prefix. Native broker callbacks may
        # append a suffix while that prefix is persisted; fence the suffix too.
        for _ in range(32):
            self.publisher.enqueue_pending()
            receipt = await self.publisher.await_fence()
            if receipt.last_sequence == self.runtime.journal.latest_sequence(self.runtime.run_id):
                return receipt
        raise RuntimeError("Strategy 43 source publication is not fully fenced")

    async def free_cash_after_reservations(self, account_id):
        runtime = self.runtime
        if account_id not in runtime.config.account_ids:
            raise ValueError("Strategy 43 cash request crossed its native account")
        await runtime._refresh_portfolio_from_broker(for_entry_admission=True)
        state = runtime.portfolio.states[account_id]
        metrics = runtime.portfolio._metrics(state)
        capacity = min(metrics["available_funds"], runtime.portfolio._broker_cash_capacity(state))
        fees = sum(row.reserved_entry_fees for row in runtime.portfolio.reservations.values()
            if row.account_id == account_id and row.status not in _TERMINAL_RESERVATIONS)
        trades = await runtime.broker.trades()
        histories = {}
        for trade in trades:
            histories.setdefault((trade.account, str(trade.order_id)), []).append(trade)
        live = {str(row.orderId): row for row in await runtime.broker.live_orders()}
        reserve = 0.
        for group in runtime.order_manager._groups.values():
            if group.account_id != account_id or group.intent.action != "enter_long":
                continue
            entries = exits = pending = 0.
            paid = [0., 0., 0., 0.]  # stop, target, rotation, session liquidation
            for identity, role in group.broker_order_roles.items():
                history = histories.get((account_id, identity), ())
                if role == "entry":
                    entries += sum(row.size for row in history)
                    held = live.get(identity)
                    if held is not None and held.order_status in OPEN_ORDER_STATUSES:
                        pending += held.remainingQuantity
                else:
                    exits += sum(row.size for row in history)
                    index = 0 if role == "protective_stop" else 1 if role == "profit_target" else 3
                    if any(row.commission is None for row in history):
                        raise RuntimeError("Strategy 43 fee capacity lacks native commission authority")
                    paid[index] += sum(row.commission for row in history)
            quantity = entries - exits + pending
            liquidation_id = self._leg_liquidations.get(group.group_id)
            if liquidation_id:
                liquidation = next((row for row in runtime.order_manager._groups.values()
                    if row.intent.intent_id == liquidation_id), None)
                if liquidation is not None:
                    for identity in liquidation.broker_order_roles:
                        history = histories.get((account_id, identity), ())
                        quantity -= sum(row.size for row in history)
                        if any(row.commission is None for row in history):
                            raise RuntimeError("Strategy 43 liquidation commission authority is missing")
                        paid[3] += sum(row.commission for row in history)
            if quantity < 0 or not quantity.is_integer():
                raise RuntimeError("Strategy 43 native held/pending shares are not integral")
            reserve += protective_fee_reserve(held_and_pending_shares=int(quantity), exit_fees_paid_by_role=paid)
        result = max(0., capacity - metrics["reserved_notional"] - fees - reserve)
        if not isfinite(result):
            raise RuntimeError("Strategy 43 native Portfolio cash capacity is non-finite")
        return result

    async def completed_leg_facts(self, *, state, feature, bid, quote_valid, quote_age_us):
        """Read actual leg fills; never infer them from ticker aggregate holdings.

        The caller supplies one certified completed producer feature. This
        adapter only joins native executions and their exact ownership.
        """
        from src.backend.backtest_market_data import market_day_boundary
        if self._published_batches.get(state.batch.assignment_id) != state.batch:
            raise ValueError("Strategy 43 fill reader lacks its fenced source batch")
        boundary = feature["boundary_ms"]
        if (type(boundary) is not int or boundary % 1000
                or not state.batch.facts.boundary_ms < boundary <= state.batch.facts.session_end_ms
                or type(quote_valid) is not bool or type(quote_age_us) is not int or quote_age_us < 0):
            raise ValueError("Strategy 43 fill reader needs its completed producer boundary")
        start = market_day_boundary(state.session_date, 0)
        histories = {}
        for trade in await self.runtime.broker.trades():
            histories.setdefault((trade.account, str(trade.order_id)), []).append(trade)
        result, held_total = [], 0
        for leg in state.legs:
            if not leg.group_id:
                continue
            group = self.runtime.order_manager._groups.get(leg.group_id)
            if (group is None or group.intent.intent_id != leg.intent_id
                    or group.account_id != state.batch.account_id):
                raise RuntimeError("Strategy 43 fill reader lost native leg ownership")
            entries, sales = [], []
            for identity, role in group.broker_order_roles.items():
                (entries if role == "entry" else sales).extend(histories.get((group.account_id, identity), ()))
            liquidation_id = self._leg_liquidations.get(group.group_id)
            if liquidation_id:
                liquidation = next((row for row in self.runtime.order_manager._groups.values()
                    if row.intent.intent_id == liquidation_id), None)
                if liquidation is not None:
                    for identity in liquidation.broker_order_roles:
                        sales.extend(histories.get((group.account_id, identity), ()))
            entries.sort(key=lambda trade: (trade.trade_time, str(trade.order_id)))
            fills = entries + sales
            if len({trade.execution_id for trade in fills}) != len(fills):
                raise RuntimeError("Strategy 43 native leg repeated an execution identity")
            def clock(trade):
                delta = trade.trade_time - start
                micros = (delta.days * 86_400 + delta.seconds) * 1_000_000 + delta.microseconds
                if micros % 100_000 or not isfinite(trade.size) or trade.size <= 0 or not isfinite(trade.price) or trade.price <= 0:
                    raise RuntimeError("Strategy 43 native execution violates its 100 ms contract")
                return micros // 1000
            clocks = [clock(trade) for trade in fills]
            if any(clock <= state.batch.facts.boundary_ms or clock > boundary or clock % 100 for clock in clocks):
                raise RuntimeError("Strategy 43 native fills crossed their causal broker clock")
            acquired = sum(trade.size for trade in entries)
            held = acquired - sum(trade.size for trade in sales)
            if (not 0 <= held <= acquired <= state.batch.legs[leg.ordinal - 1].quantity
                    or not float(held).is_integer() or not float(acquired).is_integer()):
                raise RuntimeError("Strategy 43 native leg inventory is invalid")
            held_total += int(held)
            first_ms = clock(entries[0]) if entries else 0
            result.append(CompletedLegFacts(group.group_id, boundary, first_ms,
                max(clocks, default=0), sum(trade.size * trade.price for trade in entries) / acquired if acquired else 0.,
                int(held), float(group.intent.invalidation_price), bool(feature["observed"]),
                feature["high"], bid, quote_valid, quote_age_us, feature["ten_second_mean_movement"],
                self.source_token, entries[0].price if entries else 0.))
        actual = self.runtime.broker.position_quantity(state.batch.account_id,
            self.runtime._assignment_for_intent(group.intent).conid,
            state.batch.facts.ticker) if result else 0
        if actual != held_total:
            raise RuntimeError("Strategy 43 native ticker inventory contains unattributed shares")
        return tuple(result)

    async def publish_batch(self, state):
        if (state.batch.facts.source_token != self.source_token
                or state.batch.facts.session_end_ms != self.session_end_ms):
            raise ValueError("Strategy 43 batch changed its certified source plan")
        fact_id = self.source_fact_id(state.batch.facts)
        if str(UUID(fact_id)) != fact_id:
            raise ValueError("Strategy 43 producer fact reference is not canonical")
        self.runtime.journal.append_batch(state, source_fact_id=fact_id)
        await self._fence()
        self._published_batches[state.batch.assignment_id] = state.batch

    async def submit_leg(self, batch, ordinal, intent):
        runtime = self.runtime
        if (self._published_batches.get(batch.assignment_id) != batch
                or type(ordinal) is not int or not 1 <= ordinal <= len(batch.legs)
                or intent != entry_intents(batch, session_date=runtime.config.anchor_date)[ordinal - 1]
                or runtime.journal.assignment_for_intent(intent.intent_id) != batch.assignment_id):
            raise ValueError("Strategy 43 submission lacks its fenced source batch")
        await runtime._refresh_portfolio_from_broker(for_entry_admission=True)
        decision, approved = await runtime.portfolio.approve(intent,
            account_id=batch.account_id, assignment_id=batch.assignment_id)
        if approved is None:
            await runtime._record_intent_rejection(intent, batch.account_id, decision)
            await self._fence()
            return SubmissionReceipt(ordinal, intent.intent_id, "", "rejected")
        if approved.quantity != intent.quantity:
            runtime.portfolio.release_intent(intent.intent_id, reason="strategy_forty_three_fixed_leg_resize_rejected")
            await self._fence()
            return SubmissionReceipt(ordinal, intent.intent_id, "", "rejected")
        assignment = runtime._assignment_for_intent(approved)
        if assignment is None or assignment.account_id != batch.account_id or assignment.ticker != intent.ticker:
            raise RuntimeError("Strategy 43 Portfolio approval lost native assignment ownership")
        runtime.control_plane.campaigns.reserve(assignment)
        await self._fence()
        # Submission exceptions retain the native reservation/unknown outcome.
        # The coordinator poisons the session; no alternate order is attempted.
        group = await runtime.order_manager.submit_intent(approved,
            account_id=batch.account_id, event=None)
        if group.state in {OrderManagementState.REJECTED, OrderManagementState.POLICY_BLOCKED}:
            return SubmissionReceipt(ordinal, intent.intent_id, "", "rejected")
        return SubmissionReceipt(ordinal, intent.intent_id, group.group_id, "submitted")

    async def publish_state(self, state):
        if self._published_batches.get(state.batch.assignment_id) != state.batch:
            raise ValueError("Strategy 43 state differs from its published source batch")
        for leg in state.legs:
            if not leg.group_id:
                continue
            group = self.runtime.order_manager._groups.get(leg.group_id)
            if (group is None or group.account_id != state.batch.account_id
                    or group.intent.intent_id != leg.intent_id
                    or group.intent.metadata.get("assignment_id") != state.batch.assignment_id):
                raise RuntimeError("Strategy 43 state lost its exact native leg")
        # Native OMS/fill/reservation/protection rows own durable financial
        # state; never serialize the coordinator into a JSON checkpoint.
        await self._fence()

    async def amend_stop(self, source, facts):
        batch = self._published_batches.get(source.assignment_id)
        if (batch is None or facts.source_token != self.source_token
                or source.account_id != batch.account_id or facts.group_id != source.group_id):
            raise ValueError("Strategy 43 amendment crossed its source plan or account")
        pointer = SimpleNamespace(ticker=batch.facts.ticker,
            boundary_ms=facts.boundary_ms, source_token=facts.source_token)
        self.runtime.journal.append_stop(source, source_fact_id=self.source_fact_id(pointer))
        await self._fence()
        await replace_leg_stop(self.runtime.order_manager, source)
        await self._fence()
        return float(self.runtime.order_manager._groups[source.group_id].intent.invalidation_price)

    async def cancel_session_acquisitions(self, *, at):
        from src.backend.backtest_market_data import market_day_boundary
        if (at != market_day_boundary(self.runtime.config.anchor_date, self.session_end_ms - 10_000)
                or self._cutoff_time is not None):
            raise ValueError("Strategy 43 cutoff must execute once at its pinned terminal boundary")
        self._cutoff_time = at
        manager = self.runtime.order_manager
        for group in tuple(manager._groups.values()):
            if group.intent.action == "enter_long":
                group.updated_at = max(group.updated_at, at)
                await manager._cancel_open_entry_roots(group, "strategy_forty_three_session_cutoff")
        await manager.reconcile()
        await self.runtime._refresh_portfolio_from_broker()
        await self._fence()

    async def liquidate_leg(self, *, state, ordinal, bid, source_fact_id):
        if (self._cutoff_time is None or self._published_batches.get(state.batch.assignment_id) != state.batch
                or type(ordinal) is not int or not 1 <= ordinal <= 15
                or not isfinite(bid) or bid <= 0):
            raise ValueError("Strategy 43 liquidation lacks its pinned cutoff and source batch")
        leg = state.legs[ordinal - 1]
        manager, runtime = self.runtime.order_manager, self.runtime
        group = manager._groups.get(leg.group_id)
        if (group is None or group.account_id != state.batch.account_id
                or group.intent.intent_id != leg.intent_id
                or group.group_id in self._leg_liquidations):
            raise ValueError("Strategy 43 liquidation needs one previously unliquidated native leg")
        trades = await runtime.broker.trades()
        quantity = sum(row.size * (1 if group.broker_order_roles[str(row.order_id)] == "entry" else -1)
            for row in trades if row.account == group.account_id and str(row.order_id) in group.broker_order_roles)
        if quantity < 0 or not float(quantity).is_integer():
            raise RuntimeError("Strategy 43 native leg inventory is invalid at cutoff")
        if quantity == 0:
            return None
        intent = StrategyIntent(str(uuid5(NAMESPACE_URL,
            f"strategy-43-liquidation:{runtime.run_id}:{leg.intent_id}")), group.intent.ticker,
            self._cutoff_time, "exit", quantity, bid,
            execution_policy=ExecutionPolicy(policy_id="strategy-43-liquidation",
                name=ExecutionPolicyName.ADAPTIVE_URGENT,
                envelope=ExecutionEnvelope(minimum_sell_price=native_price(bid * .99),
                    deadline_ms=10_000, maximum_reprices=0, persist_until_cancelled=True)),
            urgency="urgent", outside_rth=True, reason="strategy_forty_three_session_liquidation", metadata={})
        self._leg_liquidations[group.group_id] = intent.intent_id
        runtime.journal.append_leg_exit(intent=intent, account_id=group.account_id,
            assignment_id=state.batch.assignment_id, source_entry_intent_id=leg.intent_id,
            source_fact_id=source_fact_id)
        runtime.intent_planner.authorize_leg_exit(intent, group_id=group.group_id,
            assignment_id=state.batch.assignment_id)
        await self._fence()
        await runtime._refresh_portfolio_from_broker()
        decision, approved = await runtime.portfolio.approve(intent, account_id=group.account_id,
            assignment_id=state.batch.assignment_id)
        if approved is None or approved.quantity != quantity:
            if approved is not None:
                runtime.portfolio.release_intent(intent.intent_id,
                    reason="strategy_forty_three_fixed_liquidation_resize_rejected")
            else:
                await runtime._record_intent_rejection(intent, group.account_id, decision)
            await self._fence()
            raise RuntimeError("Strategy 43 native Portfolio did not admit its full leg liquidation")
        await self._fence()
        group.protection_delegated = True
        group.updated_at = self._cutoff_time
        manager._transition(group, group.state, {"event": "protection_delegated_to_strategy_forty_three_leg_exit"})
        live = await runtime.broker.live_orders()
        excluded = tuple(str(row.orderId) for row in live if manager._group_for_order(row) is not group)
        try:
            await manager.cancel_strategy_protection(account_id=group.account_id,
                ticker=group.intent.ticker, client_id_prefix=manager._protective_order_prefix(),
                event_time=self._cutoff_time, exclude_order_ids=excluded)
            await manager.reconcile()
            remaining = [row for row in await runtime.broker.live_orders()
                         if manager._group_for_order(row) is group
                         and row.order_status in OPEN_ORDER_STATUSES and row.remainingQuantity > 0]
            if remaining:
                raise RuntimeError("Strategy 43 leg protection cancellation is not confirmed")
            await self._fence()
            result = await manager.submit_intent(approved, account_id=group.account_id, event=None)
        except BaseException:
            # Preserve unknown submission outcomes. Restore source protection
            # only when there is no working/uncertain replacement sell path.
            snapshot = manager.snapshot_for_intent(intent.intent_id)
            if snapshot is None or snapshot.state in {OrderManagementState.REJECTED, OrderManagementState.POLICY_BLOCKED}:
                group.protection_delegated = False
                await manager.reconcile_protection(group)
            raise
        await self._fence()
        return result

    async def terminal_receipt(self):
        """Fail qualification on any residual native holding or working order."""
        if self._cutoff_time is None:
            raise RuntimeError("Strategy 43 terminal receipt lacks its session cutoff")
        await self.runtime.order_manager.reconcile()
        await self.runtime._refresh_portfolio_from_broker()
        for batch in self._published_batches.values():
            assignments = [row for row in self.runtime.strategy.assignments()
                           if row.assignment_id == batch.assignment_id]
            if len(assignments) != 1:
                raise RuntimeError("Strategy 43 terminal receipt lost its native assignment")
            assignment = assignments[0]
            if self.runtime.broker.position_quantity(batch.account_id, assignment.conid, batch.facts.ticker):
                raise RuntimeError("Strategy 43 terminal receipt has residual native exposure")
        live = [row for row in await self.runtime.broker.live_orders()
                if row.order_status in OPEN_ORDER_STATUSES and row.remainingQuantity > 0]
        if live:
            raise RuntimeError("Strategy 43 terminal receipt has working native orders")
        receipt = await self._fence()
        return dict(run_id=self.runtime.run_id, last_sequence=receipt.last_sequence,
                    remaining_positions=0, working_orders=0,
                    ticker_batches=len(self._published_batches),
                    liquidation_orders=len(self._leg_liquidations))
