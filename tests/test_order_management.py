from __future__ import annotations

import tempfile
import unittest
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

from src.trading_runtime.domain import InstrumentContract, TradingMode
from src.trading_runtime.ibkr_schema import (
    LiveOrder,
    OrderRequest,
    OrderStatus,
    PortfolioPosition,
)
from src.trading_runtime.execution_policies import (
    ExecutionEnvelope,
    ExecutionMarketSnapshot,
    ExecutionPolicy,
    ExecutionPolicyName,
    ProtectionProfile,
    ProtectionSlice,
    StopRule,
    StopRuleType,
    TrailingRule,
    TrailingRuleType,
)
from src.trading_runtime.journal import TradingJournal
from src.trading_runtime.order_management import (
    BrokerCommunicationPolicy,
    ExecutionUrgency,
    OrderManagementEngine,
    OrderManagementState,
    ShortabilitySnapshot,
    _ManagedOrderGroup,
    _protective_repair_raw,
    _intent_from_payload,
    _extend_managed_plan,
    _protection_group_key,
    execution_tactic,
)
from src.trading_runtime.risk import RiskAuthority
from src.trading_runtime.signals import (
    STRATEGY_INTENT_SCHEMA_VERSION,
    StrategyEvaluation,
    StrategyIntent,
    normalize_strategy_evaluation,
)
from src.trading_runtime.simulated_broker import SimulatedBrokerAdapter, _Position
from src.trading_runtime.strategy_orders import (
    IbkrStrategyOrderPlanner,
    RuntimeIbkrStrategyOrderPlanner,
    StrategyOrderPlan,
)


class ProtectionRepairMetadataTests(unittest.TestCase):
    def test_repair_stop_overrides_parent_entry_role_without_losing_lineage(self) -> None:
        raw = _protective_repair_raw({
            "canonical_run_id": "run-1",
            "canonical_metadata": {
                "action": "enter_long",
                "execution_role": "entry",
                "reason": "reentry_confirmed",
            },
        })

        self.assertEqual(raw["canonical_run_id"], "run-1")
        self.assertEqual(raw["canonical_metadata"]["action"], "enter_long")
        self.assertEqual(
            raw["canonical_metadata"]["execution_role"], "protective_stop"
        )
        self.assertEqual(
            raw["canonical_metadata"]["reason"], "protective_stop_filled"
        )


NOW = datetime.now(timezone.utc)


def intent(
    *,
    action: str = "enter_long",
    urgency: str = "urgent",
    side_quote: tuple[float, float] = (10.00, 10.02),
    quantity: float = 100,
) -> StrategyIntent:
    return StrategyIntent(
        intent_id=f"intent-{action}-{urgency}",
        ticker="TEST",
        event_time=NOW,
        action=action,  # type: ignore[arg-type]
        quantity=quantity,
        reference_price=10.01,
        invalidation_price=9.80,
        urgency=urgency,  # type: ignore[arg-type]
        metadata={
            "bid": side_quote[0],
            "ask": side_quote[1],
            "quote_observed_at": NOW.isoformat(),
            "tick_size": 0.01,
        },
    )


def planner(strategy_intent: StrategyIntent, account_id: str, _event) -> StrategyOrderPlan:
    side = "BUY" if strategy_intent.action in {"enter_long", "add_long", "cover", "reduce_short"} else "SELL"
    return StrategyOrderPlan(
        (
            OrderRequest(
                acctId=account_id,
                conid=123,
                cOID=strategy_intent.intent_id,
                ticker="TEST",
                orderType="LMT",
                side=side,
                quantity=strategy_intent.quantity,
                price=strategy_intent.reference_price,
            ),
        )
    )


def portfolio_approved(
    journal: TradingJournal,
    request: StrategyIntent,
    account_id: str = "DU1",
) -> StrategyIntent:
    decision_id = f"decision:{request.intent_id}"
    reservation_id = f"reservation:{request.intent_id}"
    state = journal.portfolio_states().get(account_id) or {}
    reservations = [
        row
        for row in state.get("reservations") or []
        if str(row.get("reservation_id") or "") != reservation_id
    ]
    reservations.append(
        {
            "reservation_id": reservation_id,
            "decision_id": decision_id,
            "intent_id": request.intent_id,
            "account_id": account_id,
            "ticker": request.ticker,
            "quantity": request.quantity,
            "status": "reserved",
        }
    )
    journal.save_portfolio_state(account_id, {**state, "reservations": reservations})
    return replace(
        request,
        metadata={
            **request.metadata,
            "portfolio_decision_id": decision_id,
            "portfolio_reservation_id": reservation_id,
        },
    )


class WarningBroker(SimulatedBrokerAdapter):
    def __init__(self, message_ids: list[str]) -> None:
        super().__init__(["DU1"], mode=TradingMode.PAPER)
        self.message_ids = message_ids
        self.replies: list[bool] = []

    async def place_orders(self, account_id, orders):
        return [{"id": "reply-1", "message": ["Review this order"], "messageIds": self.message_ids}]

    async def reply(self, reply_id: str, confirmed: bool):
        self.replies.append(confirmed)
        if confirmed:
            return [{"order_id": "9001", "order_status": "Submitted"}]
        return [{"error": "Order warning declined"}]


class ChainedWarningBroker(WarningBroker):
    async def reply(self, reply_id: str, confirmed: bool):
        self.replies.append(confirmed)
        if reply_id == "reply-1":
            return [{"id": "reply-2", "message": ["Second review"], "messageIds": ["o164"]}]
        return [{"order_id": "9001", "order_status": "Submitted"}]


class RecordingBroker(SimulatedBrokerAdapter):
    def __init__(self) -> None:
        super().__init__(["DU1"], mode=TradingMode.PAPER)
        self.modifications: list[tuple[str, OrderRequest]] = []

    async def modify_order(self, account_id: str, order_id: str, order: OrderRequest):
        self.modifications.append((order_id, order))
        return await super().modify_order(account_id, order_id, order)


class SequencedCommandBroker(RecordingBroker):
    def __init__(self) -> None:
        super().__init__()
        self.command_sequence: list[str] = []

    async def place_orders(self, account_id: str, orders: list[OrderRequest]):
        self.command_sequence.append(
            "place:" + ",".join(str(order.side).upper() for order in orders)
        )
        return await super().place_orders(account_id, orders)

    async def cancel_order(self, account_id: str, order_id: str):
        self.command_sequence.append(f"cancel:{order_id}")
        return await super().cancel_order(account_id, order_id)


class TerminalTargetRaceBroker(RecordingBroker):
    def __init__(self) -> None:
        super().__init__()
        self.terminal_target_id = ""
        self.raced = False

    async def modify_order(self, account_id: str, order_id: str, order: OrderRequest):
        if not self.raced and order_id == self.terminal_target_id:
            self.raced = True
            state = self._orders[order_id]
            state.status = OrderStatus.FILLED
            state.filled = state.requested_quantity
            position = self._positions[account_id][state.request.conid]
            position.quantity = 0.0
        return await super().modify_order(account_id, order_id, order)


class ReconciliationRaceBroker(RecordingBroker):
    def __init__(self, *, position_quantity: float, live_orders: list[LiveOrder]) -> None:
        super().__init__()
        self.position_quantity = position_quantity
        self.forced_live_orders = live_orders
        self.cancellations: list[str] = []

    async def positions(self, account_id: str):
        return [
            PortfolioPosition(
                acctId=account_id,
                conid=123,
                contractDesc="TEST",
                position=self.position_quantity,
                mktPrice=10.02,
                mktValue=self.position_quantity * 10.02,
                avgCost=10.0,
                avgPrice=10.0,
                realizedPnl=0.0,
                unrealizedPnl=0.0,
            )
        ]

    async def live_orders(self):
        return list(self.forced_live_orders)

    async def cancel_order(self, account_id: str, order_id: str):
        self.cancellations.append(order_id)
        return [{"order_id": order_id, "order_status": "Cancelled"}]


class OutcomeUnknownBroker(SimulatedBrokerAdapter):
    def __init__(self) -> None:
        super().__init__(["DU1"], mode=TradingMode.PAPER)
        self.submission_attempts = 0

    async def place_orders(self, account_id, orders):
        self.submission_attempts += 1
        raise TimeoutError("gateway response timed out after transmission")

    async def live_orders(self):
        if self.submission_attempts == 0:
            return []
        return [
            LiveOrder(
                account="DU1",
                orderId="7001",
                conid=123,
                ticker="TEST",
                side="BUY",
                orderType="LMT",
                tif="DAY",
                totalSize=100,
                filledQuantity=0,
                remainingQuantity=100,
                avgPrice=0,
                order_status=OrderStatus.SUBMITTED,
                cOID="intent-enter_long-urgent",
                price=10.02,
            )
        ]


class BlockedShortability:
    async def shortability(self, conid: int) -> ShortabilitySnapshot:
        return ShortabilitySnapshot(conid, 0, "not shortable", NOW)


class ExecutionTacticTests(unittest.TestCase):
    def test_protection_capacity_groups_standalone_oca_alternatives_together(self) -> None:
        common = dict(
            account="DU1",
            conid=123,
            ticker="TEST",
            side="SELL",
            tif="DAY",
            totalSize=100,
            filledQuantity=0,
            remainingQuantity=100,
            avgPrice=0,
            order_status=OrderStatus.SUBMITTED,
            raw={"oca_group": "sim-oca-5"},
        )
        stop = LiveOrder(
            **common,
            orderId="6",
            orderType="STP",
            cOID="exit-stop",
        )
        trail = LiveOrder(
            **common,
            orderId="7",
            orderType="TRAIL",
            cOID="exit-trail",
        )

        self.assertEqual(_protection_group_key(stop), "sim-oca-5")
        self.assertEqual(_protection_group_key(trail), "sim-oca-5")

    def test_repair_order_extends_persisted_batches_and_slice_identity(self) -> None:
        parent = OrderRequest(
            acctId="DU1", conid=123, cOID="parent", ticker="TEST",
            orderType="LMT", side="BUY", quantity=100, price=10,
        )
        stop = OrderRequest(
            acctId="DU1", conid=123, parentId="parent", ticker="TEST",
            orderType="STP", side="SELL", quantity=100, auxPrice=9.8,
        )
        repair = replace(stop, cOID="repair-1", parentId=None, quantity=25)
        plan = StrategyOrderPlan(
            orders=(parent, stop),
            batches=((parent, stop),),
            order_slice_ids=("main", "main"),
        )

        extended = _extend_managed_plan(
            plan, [parent, stop, repair], slice_id="repair-backstop"
        )

        self.assertEqual(extended.orders, (parent, stop, repair))
        self.assertEqual(extended.broker_batches, ((parent, stop), (repair,)))
        self.assertEqual(
            extended.order_slice_ids, ("main", "main", "repair-backstop")
        )

    def test_execution_intent_contract_is_versioned_and_legacy_payloads_migrate(self) -> None:
        current = intent()
        self.assertEqual(
            current.payload()["schema_version"], STRATEGY_INTENT_SCHEMA_VERSION
        )
        legacy_payload = current.payload()
        legacy_payload.pop("schema_version")
        recovered = _intent_from_payload(legacy_payload)
        self.assertEqual(recovered.schema_version, STRATEGY_INTENT_SCHEMA_VERSION)

        with self.assertRaisesRegex(ValueError, "Unsupported Strategy intent schema"):
            StrategyIntent(
                intent_id="future-intent",
                ticker="TEST",
                event_time=NOW,
                action="enter_long",
                quantity=1,
                reference_price=10,
                schema_version=999,
            )

    def test_suppression_does_not_implicitly_authorize_warning_confirmation(self) -> None:
        with patch.dict(
            "os.environ",
            {
                "IBKR_SUPPRESS_ORDER_MESSAGE_IDS": "o163",
                "IBKR_AUTO_CONFIRM_ORDER_MESSAGE_IDS": "",
            },
            clear=False,
        ):
            policy = BrokerCommunicationPolicy.from_environment()
        self.assertEqual(policy.suppressed_message_ids, ("o163",))
        self.assertEqual(policy.auto_confirm_message_ids, ())
        self.assertFalse(policy.warning_decision(("o163",)))

    def test_very_urgent_buy_walks_from_ask_by_ticks_inside_one_second(self) -> None:
        tactic = execution_tactic(
            intent(urgency="very_urgent"),
            BrokerCommunicationPolicy(maximum_reprice_ticks=4),
            enforce_wall_clock_freshness=False,
        )
        assert tactic is not None
        self.assertEqual(tactic.urgency, ExecutionUrgency.VERY_URGENT)
        self.assertEqual([step.price for step in tactic.steps], [10.02, 10.03, 10.04, 10.05, 10.06])
        self.assertEqual([step.after_ms for step in tactic.steps], [0, 150, 300, 450, 600])
        self.assertLess(tactic.maximum_duration_ms, 1000)

    def test_urgent_sell_uses_bid_once_and_regular_buy_improves_to_ask(self) -> None:
        urgent = execution_tactic(
            intent(action="exit", urgency="urgent"),
            BrokerCommunicationPolicy(),
            enforce_wall_clock_freshness=False,
        )
        regular = execution_tactic(
            intent(urgency="regular"),
            BrokerCommunicationPolicy(),
            enforce_wall_clock_freshness=False,
        )
        assert urgent is not None and regular is not None
        self.assertEqual([(step.after_ms, step.price) for step in urgent.steps], [(0, 10.0)])
        self.assertEqual(regular.steps[0].price, 10.01)
        self.assertEqual(regular.steps[-1].price, 10.02)
        self.assertLess(regular.maximum_duration_ms, 1000)

    def test_strategy_evaluation_rejects_direct_order_requests(self) -> None:
        direct = OrderRequest(
            acctId="DU1",
            conid=123,
            cOID="forbidden",
            orderType="LMT",
            side="BUY",
            quantity=1,
            price=10,
        )
        with self.assertRaisesRegex(TypeError, "direct broker orders are forbidden"):
            normalize_strategy_evaluation([direct])  # type: ignore[arg-type]
        self.assertEqual(normalize_strategy_evaluation(StrategyEvaluation()), StrategyEvaluation())


class OrderManagementPolicyTests(unittest.IsolatedAsyncioTestCase):
    async def _manager(
        self,
        directory: str,
        broker: SimulatedBrokerAdapter,
        *,
        policy: BrokerCommunicationPolicy,
        shortability_provider=None,
        state_callback=None,
        causal_execution_clock: bool = False,
    ) -> tuple[OrderManagementEngine, TradingJournal]:
        journal = TradingJournal(Path(directory) / "orders.sqlite3")
        await broker.initialize()
        risk = RiskAuthority()
        await risk.prime(broker, ["DU1"])
        manager = OrderManagementEngine(
            broker=broker,
            planner=planner,
            risk=risk,
            journal=journal,
            run_id="run-1",
            strategy_id="strategy-1",
            strategy_revision=1,
            policy=policy,
            shortability_provider=shortability_provider,
            state_callback=state_callback,
            causal_execution_clock=causal_execution_clock,
        )
        return manager, journal

    async def test_reconcile_does_not_reproject_unchanged_terminal_orders(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            broker = SimulatedBrokerAdapter(["DU1"], mode=TradingMode.PAPER)
            projected = []

            async def record_projection(snapshot) -> None:
                projected.append(snapshot)

            manager, journal = await self._manager(
                directory,
                broker,
                policy=BrokerCommunicationPolicy(),
                state_callback=record_projection,
            )
            requested = portfolio_approved(journal, intent())
            await manager.submit_intent(requested, account_id="DU1", event=None)

            await manager.reconcile()
            first_count = len(projected)
            await manager.reconcile()

            self.assertGreater(first_count, 0)
            self.assertEqual(len(projected), first_count)
            await manager.close()
            journal.close()

    async def test_oms_rejects_missing_or_mismatched_portfolio_authority(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            broker = SimulatedBrokerAdapter(["DU1"], mode=TradingMode.PAPER)
            manager, journal = await self._manager(
                directory,
                broker,
                policy=BrokerCommunicationPolicy(),
            )
            with self.assertRaisesRegex(ValueError, "durable Portfolio"):
                await manager.submit_intent(intent(), account_id="DU1", event=None)
            approved = portfolio_approved(journal, intent())
            mismatched = replace(approved, quantity=approved.quantity + 1)
            with self.assertRaisesRegex(ValueError, "mismatched Portfolio reservation"):
                await manager.submit_intent(
                    mismatched, account_id="DU1", event=None
                )
            self.assertEqual(await broker.live_orders(), [])
            await manager.close()
            journal.close()

    async def test_causal_entry_deadline_cancels_before_a_later_market_fill(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            broker = SimulatedBrokerAdapter(["DU1"], mode=TradingMode.BACKTEST)
            manager, journal = await self._manager(
                directory,
                broker,
                policy=BrokerCommunicationPolicy(),
            )
            try:
                request = replace(
                    intent(side_quote=(10.00, 10.02), quantity=10),
                    reference_price=10.01,
                    execution_policy=ExecutionPolicy(
                        policy_id="test-urgent",
                        name=ExecutionPolicyName.ADAPTIVE_URGENT,
                        envelope=ExecutionEnvelope(
                            maximum_buy_price=10.02,
                            deadline_ms=750,
                            maximum_reprices=4,
                        ),
                    ),
                )
                submitted = await manager.submit_intent(
                    portfolio_approved(journal, request),
                    account_id="DU1",
                    event=None,
                )
                self.assertEqual(submitted.state, OrderManagementState.ACKNOWLEDGED)

                expired = await manager.expire_entry_deadlines(
                    NOW + timedelta(seconds=13)
                )

                self.assertEqual(len(expired), 1)
                self.assertEqual(expired[0].state, OrderManagementState.CANCELLED)
                orders = await broker.live_orders()
                self.assertEqual(orders[0].order_status, OrderStatus.CANCELLED)
                records = [
                    row
                    for row in journal.records("run-1")
                    if row.entity_type == "order_cancel_requested"
                ]
                self.assertEqual(records[-1].payload["reason"], "execution_deadline")
                self.assertEqual(records[-1].event_time, NOW + timedelta(seconds=13))
            finally:
                await manager.close()
                journal.close()

    async def test_causal_adaptive_entry_reprices_before_historical_deadline(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            broker = SimulatedBrokerAdapter(["DU1"], mode=TradingMode.BACKTEST)
            manager, journal = await self._manager(
                directory,
                broker,
                policy=BrokerCommunicationPolicy(),
                causal_execution_clock=True,
            )
            try:
                request = replace(
                    intent(side_quote=(10.00, 10.02), quantity=100),
                    reference_price=10.01,
                    execution_policy=ExecutionPolicy(
                        policy_id="test-causal-urgent",
                        name=ExecutionPolicyName.ADAPTIVE_URGENT,
                        envelope=ExecutionEnvelope(
                            maximum_buy_price=10.20,
                            deadline_ms=5_000,
                            maximum_reprices=4,
                        ),
                    ),
                )
                submitted = await manager.submit_intent(
                    portfolio_approved(journal, request),
                    account_id="DU1",
                    event=None,
                )
                self.assertEqual(submitted.current_limit_price, 10.02)
                self.assertIsNone(manager.snapshots()[0].internal_reaction_ms)

                causal_time = NOW + timedelta(milliseconds=100)
                manager.on_market_snapshot(
                    ExecutionMarketSnapshot(
                        "TEST",
                        10.04,
                        10.06,
                        0.01,
                        causal_time,
                        "qmd-history",
                    )
                )
                repriced = await manager.advance_entry_execution(causal_time)

                self.assertEqual(len(repriced), 1)
                self.assertGreater(float(repriced[0].current_limit_price or 0), 10.02)
                live = await broker.live_orders()
                self.assertEqual(live[0].price, repriced[0].current_limit_price)
                records = [
                    row
                    for row in journal.records("run-1")
                    if row.entity_type == "order_repriced"
                ]
                self.assertEqual(records[-1].event_time, causal_time)
                self.assertEqual(
                    records[-1].payload["quote_observed_at"],
                    causal_time.isoformat(),
                )

                manager.on_market_snapshot(
                    ExecutionMarketSnapshot(
                        "TEST",
                        10.05,
                        10.07,
                        0.01,
                        causal_time,
                        "qmd-history",
                    )
                )
                self.assertEqual(
                    await manager.advance_entry_execution(causal_time),
                    (),
                )
            finally:
                await manager.close()
                journal.close()

    async def test_historical_marketable_entry_fills_from_latest_causal_quote(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            event_time = datetime(2026, 8, 21, 14, 0, tzinfo=timezone.utc)
            broker = SimulatedBrokerAdapter(["DU1"], mode=TradingMode.BACKTEST)
            manager, journal = await self._manager(
                directory,
                broker,
                policy=BrokerCommunicationPolicy(),
            )
            try:
                from src.market_engine.events import QuoteEvent

                await broker.on_market_event(
                    QuoteEvent(
                        ask_exchange=11,
                        ask_price=10.03,
                        ask_size=100,
                        bid_exchange=12,
                        bid_price=10.00,
                        bid_size=100,
                        conditions=(),
                        indicators=(),
                        ingest_ts=event_time - timedelta(milliseconds=100),
                        raw={},
                        sequence=1,
                        source="test",
                        tape=3,
                        ticker="TEST",
                        ts=event_time - timedelta(milliseconds=100),
                    )
                )
                request = replace(
                    intent(side_quote=(10.00, 10.02), quantity=10),
                    event_time=event_time,
                    reference_price=10.02,
                    metadata={
                        **intent(side_quote=(10.00, 10.02)).metadata,
                        "quote_observed_at": event_time.isoformat(),
                    },
                )

                snapshot = await manager.submit_intent(
                    portfolio_approved(journal, request),
                    account_id="DU1",
                    event=None,
                )

                self.assertEqual(snapshot.filled_quantity, 10)
                self.assertEqual(snapshot.remaining_quantity, 0)
                executions = [
                    row
                    for row in journal.records("run-1")
                    if row.category == "execution"
                ]
                self.assertEqual(len(executions), 1)
                self.assertEqual(executions[0].event_time, event_time)
                self.assertEqual(executions[0].payload["price"], 10.02)
            finally:
                await manager.close()
                journal.close()

    async def test_protection_replacement_cancels_children_before_parent(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            broker = SimulatedBrokerAdapter(["DU1"], mode=TradingMode.PAPER)
            manager, journal = await self._manager(
                directory,
                broker,
                policy=BrokerCommunicationPolicy(),
            )
            prefix = "strategy-1-v1-"
            parent = OrderRequest(
                acctId="DU1", conid=123, cOID=f"{prefix}entry", ticker="TEST",
                orderType="LMT", side="BUY", quantity=10, price=10,
            )
            child = OrderRequest(
                acctId="DU1", conid=123, cOID="", parentId=parent.cOID,
                ticker="TEST", orderType="STP", side="SELL", quantity=10,
                auxPrice=9.8, isSingleGroup=True,
            )
            await broker.place_orders("DU1", [parent, child])

            responses = await manager.cancel_strategy_protection(
                account_id="DU1",
                ticker="TEST",
                client_id_prefix=prefix,
                event_time=NOW,
            )

            self.assertEqual(len(responses), 2)
            states = {row.orderId: row.order_status for row in await broker.live_orders()}
            self.assertEqual(states, {"1": OrderStatus.CANCELLED, "2": OrderStatus.CANCELLED})
            await manager.close()
            journal.close()

    async def test_partial_entry_repair_restores_target_and_stop_as_one_oca_pair(self) -> None:
        broker = ReconciliationRaceBroker(position_quantity=10.0, live_orders=[])
        with tempfile.TemporaryDirectory() as directory:
            manager, journal = await self._manager(
                directory,
                broker,
                policy=BrokerCommunicationPolicy(),
            )
            broker._positions["DU1"][123] = _Position(
                conid=123,
                ticker="TEST",
                quantity=10.0,
                avg_cost=10.0,
            )
            profile = ProtectionProfile(
                profile_id="structural-single-target",
                revision=1,
                slices=(
                    ProtectionSlice(
                        "position",
                        1.0,
                        StopRule(StopRuleType.FIXED_PRICE, price=9.8),
                        profit_target_price=10.5,
                    ),
                ),
            )
            entry_intent = replace(
                intent(quantity=100),
                intent_id="generic-partial-entry",
                protection_profile=profile,
                profit_target_price=10.5,
            )
            entry_request = OrderRequest(
                acctId="DU1",
                conid=123,
                cOID="generic-partial-entry",
                ticker="TEST",
                orderType="LMT",
                side="BUY",
                quantity=100,
                price=10.01,
            )
            group = _ManagedOrderGroup(
                group_id="generic-partial-entry-group",
                intent=entry_intent,
                account_id="DU1",
                plan=StrategyOrderPlan((entry_request,)),
                state=OrderManagementState.PARTIALLY_FILLED,
                created_at=NOW,
                updated_at=NOW,
                orders=[entry_request],
                broker_order_ids=["100"],
                broker_order_request_indexes={"100": 0},
                broker_order_roles={"100": "entry"},
                filled_by_broker_order={"100": 10.0},
                filled_quantity=10.0,
                remaining_quantity=90.0,
            )
            manager._groups[group.group_id] = group
            manager._group_by_broker_id["100"] = group.group_id

            result = await manager.reconcile_protection(group)

            self.assertEqual(result["required_quantity"], 10.0)
            self.assertEqual(result["protected_quantity"], 0)
            self.assertEqual(result["actions"][0]["action"], "place_missing_oca_protection")
            target, stop = group.orders[-2:]
            self.assertEqual((target.orderType, target.side, target.quantity), ("LMT", "SELL", 10.0))
            self.assertEqual(target.price, 10.5)
            self.assertTrue(target.isSingleGroup)
            self.assertEqual((stop.orderType, stop.side, stop.quantity), ("STP", "SELL", 10.0))
            self.assertEqual(stop.auxPrice, 9.8)
            self.assertTrue(stop.isSingleGroup)
            repaired_roles = {
                group.broker_order_roles[order_id]
                for order_id in group.broker_order_ids
                if order_id != "100"
            }
            self.assertEqual(repaired_roles, {"profit_target", "protective_stop"})
            await manager.close()
            journal.close()

    async def test_sliced_entry_reconciliation_uses_only_causally_processed_slice(self) -> None:
        prefix = "strategy-1-v1-"
        slice_sizes = (60.0, 60.0, 60.0, 59.0, 59.0)
        requests: list[OrderRequest] = []
        live_stops: list[LiveOrder] = []
        broker_order_ids: list[str] = []
        request_indexes: dict[str, int] = {}
        roles: dict[str, str] = {}
        filled_by_broker_order: dict[str, float] = {}
        for index, quantity in enumerate(slice_sizes):
            parent_id = f"{prefix}entry-{index}"
            entry_broker_id = str(index * 2 + 1)
            stop_broker_id = str(index * 2 + 2)
            entry = OrderRequest(
                acctId="DU1",
                conid=123,
                cOID=parent_id,
                ticker="TEST",
                orderType="LMT",
                side="BUY",
                quantity=quantity,
                price=10.0,
            )
            stop = OrderRequest(
                acctId="DU1",
                conid=123,
                cOID="",
                parentId=parent_id,
                ticker="TEST",
                orderType="STP",
                side="SELL",
                quantity=quantity,
                auxPrice=9.8,
            )
            entry_request_index = len(requests)
            requests.extend((entry, stop))
            broker_order_ids.extend((entry_broker_id, stop_broker_id))
            request_indexes[entry_broker_id] = entry_request_index
            request_indexes[stop_broker_id] = entry_request_index + 1
            roles[entry_broker_id] = "entry"
            roles[stop_broker_id] = "protective_stop"
            live_stops.append(
                LiveOrder(
                    account="DU1",
                    orderId=stop_broker_id,
                    conid=123,
                    ticker="TEST",
                    side="SELL",
                    orderType="STP",
                    tif="DAY",
                    totalSize=quantity,
                    filledQuantity=0,
                    remainingQuantity=quantity,
                    avgPrice=0,
                    order_status=OrderStatus.INACTIVE,
                    parentId=parent_id,
                )
            )
        filled_by_broker_order["1"] = slice_sizes[0]
        broker = ReconciliationRaceBroker(
            position_quantity=sum(slice_sizes),
            live_orders=live_stops,
        )
        with tempfile.TemporaryDirectory() as directory:
            manager, journal = await self._manager(
                directory,
                broker,
                policy=BrokerCommunicationPolicy(),
            )
            group = _ManagedOrderGroup(
                group_id="sliced-entry-race",
                intent=intent(quantity=sum(slice_sizes)),
                account_id="DU1",
                plan=StrategyOrderPlan(tuple(requests)),
                state=OrderManagementState.PARTIALLY_FILLED,
                created_at=NOW,
                updated_at=NOW,
                orders=requests,
                broker_order_ids=broker_order_ids,
                broker_order_request_indexes=request_indexes,
                broker_order_roles=roles,
                filled_by_broker_order=filled_by_broker_order,
                filled_quantity=slice_sizes[0],
                remaining_quantity=sum(slice_sizes[1:]),
            )
            manager._groups[group.group_id] = group
            for order_id in broker_order_ids:
                manager._group_by_broker_id[order_id] = group.group_id

            result = await manager.reconcile_protection(group)

            self.assertEqual(result["required_quantity"], slice_sizes[0])
            self.assertEqual(result["protected_quantity"], slice_sizes[0])
            self.assertEqual(result["actions"], [])
            self.assertEqual(broker.cancellations, [])
            self.assertEqual(broker.modifications, [])
            self.assertFalse(
                any("repair-" in str(order.cOID or "") for order in group.orders)
            )
            await manager.close()
            journal.close()

    async def test_completed_entry_group_does_not_protect_later_campaign_position(self) -> None:
        broker = ReconciliationRaceBroker(
            position_quantity=250.0,
            live_orders=[],
        )
        with tempfile.TemporaryDirectory() as directory:
            manager, journal = await self._manager(
                directory,
                broker,
                policy=BrokerCommunicationPolicy(),
            )
            entry_request = OrderRequest(
                acctId="DU1",
                conid=123,
                cOID="strategy-1-v1-old-entry",
                ticker="TEST",
                orderType="LMT",
                side="BUY",
                quantity=345,
                price=10.0,
            )
            target_request = OrderRequest(
                acctId="DU1",
                conid=123,
                cOID="",
                parentId=entry_request.cOID,
                ticker="TEST",
                orderType="LMT",
                side="SELL",
                quantity=345,
                price=10.5,
            )
            group = _ManagedOrderGroup(
                group_id="completed-old-campaign",
                intent=intent(quantity=345),
                account_id="DU1",
                plan=StrategyOrderPlan((entry_request, target_request)),
                state=OrderManagementState.FILLED,
                created_at=NOW,
                updated_at=NOW,
                orders=[entry_request, target_request],
                broker_order_ids=["1", "2"],
                broker_order_request_indexes={"1": 0, "2": 1},
                broker_order_roles={"1": "entry", "2": "profit_target"},
                filled_by_broker_order={"1": 345.0, "2": 345.0},
                filled_quantity=345.0,
                remaining_quantity=0.0,
            )
            manager._groups[group.group_id] = group
            manager._group_by_broker_id.update(
                {"1": group.group_id, "2": group.group_id}
            )

            result = await manager.reconcile_protection(group)

            self.assertEqual(result["required_quantity"], 0.0)
            self.assertEqual(result["protected_quantity"], 0.0)
            self.assertEqual(result["actions"], [])
            self.assertFalse(
                any("repair-" in str(order.cOID or "") for order in group.orders)
            )
            await manager.close()
            journal.close()

    async def test_dynamic_ratchet_never_modifies_another_order_group(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            broker = RecordingBroker()
            manager, journal = await self._manager(
                directory,
                broker,
                policy=BrokerCommunicationPolicy(),
            )
            profile = ProtectionProfile(
                profile_id="dynamic-single",
                revision=1,
                slices=(ProtectionSlice(
                    "position",
                    1.0,
                    StopRule(StopRuleType.FIXED_PRICE, price=9.5),
                    trailing=TrailingRule(
                        rule_type=TrailingRuleType.BREAKEVEN_THEN_TRAIL,
                        activation_gain_percent=0.0,
                    ),
                ),),
            )
            owner_intent = replace(
                intent(quantity=10),
                intent_id="dynamic-owner",
                protection_profile=profile,
            )
            owner = _ManagedOrderGroup(
                group_id="dynamic-owner-group",
                intent=owner_intent,
                account_id="DU1",
                plan=StrategyOrderPlan(()),
                state=OrderManagementState.FILLED,
                created_at=NOW,
                updated_at=NOW,
                orders=[],
                high_water_price=11.0,
                low_water_price=10.0,
            )
            foreign_order = OrderRequest(
                acctId="DU1", conid=123, cOID="foreign-exit-stop", ticker="TEST",
                orderType="STP", side="SELL", quantity=10, auxPrice=9.0,
            )
            broker._book_execution(
                OrderRequest(
                    acctId="DU1", conid=123, cOID="seed-position", ticker="TEST",
                    orderType="MKT", side="BUY", quantity=10,
                ),
                10.0,
                10.0,
                0.0,
            )
            response = await broker.place_orders("DU1", [foreign_order])
            foreign_id = response[0]["order_id"]
            foreign = _ManagedOrderGroup(
                group_id="foreign-exit-group",
                intent=intent(action="exit", quantity=10),
                account_id="DU1",
                plan=StrategyOrderPlan((foreign_order,)),
                state=OrderManagementState.ACKNOWLEDGED,
                created_at=NOW,
                updated_at=NOW,
                orders=[foreign_order],
                broker_order_ids=[foreign_id],
                broker_order_request_indexes={foreign_id: 0},
            )
            manager._groups = {owner.group_id: owner, foreign.group_id: foreign}
            manager._group_by_broker_id[foreign_id] = foreign.group_id

            await manager._ratchet_dynamic_protection(
                owner,
                ExecutionMarketSnapshot("TEST", 11.0, 11.02, 0.01, NOW, "test", volatility=0.1),
            )

            self.assertEqual(broker.modifications, [])
            await manager.close()
            journal.close()

    async def test_volatility_trail_waits_for_configured_activation_gain(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            broker = RecordingBroker()
            manager, journal = await self._manager(
                directory,
                broker,
                policy=BrokerCommunicationPolicy(),
            )
            profile = ProtectionProfile(
                profile_id="delayed-volatility-trail",
                revision=1,
                slices=(ProtectionSlice(
                    "position",
                    1.0,
                    StopRule(StopRuleType.FIXED_PRICE, price=9.5),
                    trailing=TrailingRule(
                        rule_type=TrailingRuleType.VOLATILITY_TRAIL,
                        volatility_multiple=1.0,
                        activation_gain_percent=8.0,
                    ),
                ),),
            )
            owner_intent = replace(
                intent(quantity=10),
                intent_id="delayed-trail-owner",
                protection_profile=profile,
            )
            stop_request = OrderRequest(
                acctId="DU1",
                conid=123,
                cOID="delayed-trail-stop",
                ticker="TEST",
                orderType="STP",
                side="SELL",
                quantity=10,
                auxPrice=9.5,
            )
            broker._book_execution(
                OrderRequest(
                    acctId="DU1", conid=123, cOID="seed-position", ticker="TEST",
                    orderType="MKT", side="BUY", quantity=10,
                ),
                10.0,
                10.0,
                0.0,
            )
            response = await broker.place_orders("DU1", [stop_request])
            order_id = response[0]["order_id"]
            owner = _ManagedOrderGroup(
                group_id="delayed-trail-group",
                intent=owner_intent,
                account_id="DU1",
                plan=StrategyOrderPlan((stop_request,)),
                state=OrderManagementState.FILLED,
                created_at=NOW,
                updated_at=NOW,
                orders=[stop_request],
                broker_order_ids=[order_id],
                broker_order_request_indexes={order_id: 0},
                high_water_price=10.5,
                low_water_price=10.0,
            )
            manager._groups = {owner.group_id: owner}
            manager._group_by_broker_id[order_id] = owner.group_id

            await manager._ratchet_dynamic_protection(
                owner,
                ExecutionMarketSnapshot(
                    "TEST", 10.5, 10.52, 0.01, NOW, "test", volatility=0.1
                ),
            )

            self.assertEqual(broker.modifications, [])
            await manager.close()
            journal.close()

    async def test_oms_records_preserve_portfolio_causal_lineage(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            broker = SimulatedBrokerAdapter(["DU1"], mode=TradingMode.PAPER)
            manager, journal = await self._manager(
                directory,
                broker,
                policy=BrokerCommunicationPolicy(),
            )
            try:
                request = replace(
                    intent(quantity=10),
                    metadata={
                        **intent(quantity=10).metadata,
                        "correlation_id": "run:assignment-TEST",
                        "causation_id": "portfolio-decision-7",
                    },
                )
                await manager.submit_intent(
                    portfolio_approved(journal, request),
                    account_id="DU1",
                    event=None,
                )
                records = [
                    row
                    for row in journal.order_management_records(limit=100)
                    if row.payload.get("intent_id") == request.intent_id
                    and row.payload.get("correlation_id")
                ]
                self.assertTrue(records)
                self.assertTrue(
                    all(row.payload["correlation_id"] == "run:assignment-TEST" for row in records)
                )
                self.assertTrue(
                    all(row.payload["causation_id"] == "portfolio-decision-7" for row in records)
                )
                self.assertTrue(all(row.event_time == NOW for row in records))
            finally:
                await manager.close()
                journal.close()

    async def test_allowlisted_warning_is_confirmed_and_complete_transcript_is_saved(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            broker = WarningBroker(["o163"])
            manager, journal = await self._manager(
                directory,
                broker,
                policy=BrokerCommunicationPolicy(auto_confirm_message_ids=("o163",)),
            )
            snapshot = await manager.submit_intent(
                portfolio_approved(journal, intent()), account_id="DU1", event=None
            )
            self.assertEqual(snapshot.state, OrderManagementState.ACKNOWLEDGED)
            self.assertEqual(broker.replies, [True])
            decisions = [
                row
                for row in journal.records("run-1")
                if row.entity_type == "order_warning_decision"
            ]
            self.assertEqual(decisions[0].payload["message_ids"], ["o163"])
            self.assertTrue(decisions[0].payload["confirmed"])
            await manager.close()
            journal.close()

    async def test_unknown_warning_is_declined_without_blocking_the_order_lane(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            broker = WarningBroker(["unknown-1"])
            manager, journal = await self._manager(
                directory,
                broker,
                policy=BrokerCommunicationPolicy(auto_confirm_message_ids=("o163",)),
            )
            snapshot = await manager.submit_intent(
                portfolio_approved(journal, intent()), account_id="DU1", event=None
            )
            self.assertEqual(snapshot.state, OrderManagementState.POLICY_BLOCKED)
            self.assertEqual(broker.replies, [False])
            await manager.close()
            journal.close()

    async def test_sequential_allowlisted_warning_chain_is_resolved_in_one_lane(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            broker = ChainedWarningBroker(["o163"])
            manager, journal = await self._manager(
                directory,
                broker,
                policy=BrokerCommunicationPolicy(auto_confirm_message_ids=("o163", "o164")),
            )
            snapshot = await manager.submit_intent(
                portfolio_approved(journal, intent()), account_id="DU1", event=None
            )
            self.assertEqual(snapshot.state, OrderManagementState.ACKNOWLEDGED)
            self.assertEqual(broker.replies, [True, True])
            decisions = [
                row.payload
                for row in journal.records("run-1")
                if row.entity_type == "order_warning_decision"
            ]
            self.assertEqual([row["message_ids"] for row in decisions], [["o163"], ["o164"]])
            await manager.close()
            journal.close()

    async def test_unavailable_short_borrow_is_skipped_and_logged(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            broker = SimulatedBrokerAdapter(["DU1"], mode=TradingMode.PAPER)
            manager, journal = await self._manager(
                directory,
                broker,
                policy=BrokerCommunicationPolicy(),
                shortability_provider=BlockedShortability(),
            )
            with self.assertRaisesRegex(ValueError, "does not report sufficient shortable"):
                await manager.submit_intent(
                    portfolio_approved(
                        journal, intent(action="enter_short", urgency="urgent")
                    ),
                    account_id="DU1",
                    event=None,
                )
            skipped = [row for row in journal.records("run-1") if row.entity_type == "short_order_skipped"]
            self.assertEqual(skipped[0].payload["ibkr_fields"]["7636"], 0)
            await manager.close()
            journal.close()

    async def test_unknown_submission_outcome_reconciles_by_client_id_without_resubmission(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            broker = OutcomeUnknownBroker()
            manager, journal = await self._manager(
                directory,
                broker,
                policy=BrokerCommunicationPolicy(),
            )
            requested = portfolio_approved(journal, intent())
            with self.assertRaisesRegex(TimeoutError, "timed out"):
                await manager.submit_intent(requested, account_id="DU1", event=None)
            self.assertEqual(manager.snapshots()[0].state, OrderManagementState.OUTCOME_UNKNOWN)
            reconciled = await manager.reconcile()
            self.assertEqual(reconciled[0].state, OrderManagementState.WORKING)
            with self.assertRaisesRegex(ValueError, "already been submitted"):
                await manager.submit_intent(requested, account_id="DU1", event=None)
            self.assertEqual(broker.submission_attempts, 1)
            await manager.close()
            journal.close()

    async def test_full_profit_pocket_modifies_existing_target_and_preserves_protection(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            broker = RecordingBroker()
            real_planner = IbkrStrategyOrderPlanner()
            instrument = InstrumentContract("TEST", 123, "TEST", "STK", "USD")

            def bracket_planner(strategy_intent, account_id, _event):
                return real_planner.plan(
                    account_id=account_id,
                    instrument=instrument,
                    intent=strategy_intent,
                    strategy_id="strategy-1",
                    strategy_revision=1,
                )

            journal = TradingJournal(Path(directory) / "orders.sqlite3")
            await broker.initialize()
            risk = RiskAuthority()
            await risk.prime(broker, ["DU1"])
            manager = OrderManagementEngine(
                broker=broker,
                planner=bracket_planner,
                risk=risk,
                journal=journal,
                run_id="run-1",
                strategy_id="strategy-1",
                strategy_revision=1,
                policy=BrokerCommunicationPolicy(),
            )
            entry = StrategyIntent(
                **{
                    **intent().payload(),
                    "intent_id": "entry-1",
                    "profit_target_price": 10.50,
                    "trailing_amount": 0.10,
                }
            )
            entry_snapshot = await manager.submit_intent(
                portfolio_approved(journal, entry), account_id="DU1", event=None
            )
            self.assertEqual(len(entry_snapshot.broker_order_ids), 4)
            pocket = StrategyIntent(
                **{
                    **intent(
                        action="take_profit",
                        urgency="very_urgent",
                        quantity=99.999999,
                    ).payload(),
                    "intent_id": "pocket-1",
                    "metadata": {
                        **intent(action="take_profit").metadata,
                        # Portfolio approval floors quantities to six decimals; the
                        # full broker position can retain additional precision.
                        "position_quantity": 99.9999999,
                        "buy_back": True,
                    },
                }
            )
            pocket_snapshot = await manager.submit_intent(
                portfolio_approved(journal, pocket), account_id="DU1", event=None
            )
            self.assertEqual(pocket_snapshot.state, OrderManagementState.ACKNOWLEDGED)
            self.assertTrue(pocket_snapshot.reentry_after_fill)
            self.assertEqual(len(broker.modifications), 1)
            modified_order_id, replacement = broker.modifications[0]
            self.assertEqual(modified_order_id, entry_snapshot.broker_order_ids[1])
            self.assertEqual(replacement.price, 10.0)
            self.assertEqual(replacement.parentId, entry_snapshot.client_order_ids[0])
            self.assertTrue(replacement.isSingleGroup)
            self.assertEqual(
                replacement.raw["canonical_metadata"]["execution_role"],
                "managed_exit",
            )
            self.assertEqual(
                replacement.raw["canonical_metadata"]["reason"],
                "strategy_exit",
            )
            source_group = next(
                group
                for group in manager._groups.values()
                if group.intent.intent_id == entry.intent_id
            )
            self.assertTrue(source_group.protection_delegated)
            self.assertEqual(
                (await manager.reconcile_protection(source_group))["status"],
                "delegated_to_managed_exit",
            )
            source_group.protection_delegated = False
            broker._positions["DU1"][123] = _Position(
                conid=123,
                ticker="TEST",
                quantity=100.0,
                avg_cost=10.0,
            )
            await manager._restore_managed_exit_delegation()
            self.assertTrue(source_group.protection_delegated)
            await manager.close()
            journal.close()

    async def test_fresh_full_exit_delegates_before_old_protection_is_cancelled(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            broker = RecordingBroker()
            real_planner = IbkrStrategyOrderPlanner()
            instrument = InstrumentContract("TEST", 123, "TEST", "STK", "USD")

            def bracket_planner(strategy_intent, account_id, _event):
                return real_planner.plan(
                    account_id=account_id,
                    instrument=instrument,
                    intent=strategy_intent,
                    strategy_id="strategy-1",
                    strategy_revision=1,
                )

            journal = TradingJournal(Path(directory) / "orders.sqlite3")
            await broker.initialize()
            risk = RiskAuthority()
            await risk.prime(broker, ["DU1"])
            manager = OrderManagementEngine(
                broker=broker,
                planner=bracket_planner,
                risk=risk,
                journal=journal,
                run_id="run-1",
                strategy_id="strategy-1",
                strategy_revision=1,
                policy=BrokerCommunicationPolicy(),
            )
            entry = replace(
                intent(),
                intent_id="entry-without-target",
                profit_target_price=None,
            )
            await manager.submit_intent(
                portfolio_approved(journal, entry), account_id="DU1", event=None
            )
            source_group = next(
                group
                for group in manager._groups.values()
                if group.intent.intent_id == entry.intent_id
            )
            broker._positions["DU1"][123] = _Position(
                conid=123,
                ticker="TEST",
                quantity=100.0,
                avg_cost=10.0,
            )
            exit_intent = replace(
                intent(action="exit"),
                intent_id="fresh-full-exit",
                metadata={
                    **intent(action="exit").metadata,
                    "position_quantity": 100.0,
                    "position_side": "long",
                },
            )

            exit_snapshot = await manager.submit_intent(
                portfolio_approved(journal, exit_intent),
                account_id="DU1",
                event=None,
            )

            self.assertEqual(exit_snapshot.state, OrderManagementState.ACKNOWLEDGED)
            self.assertTrue(source_group.protection_delegated)
            self.assertEqual(
                (await manager.reconcile_protection(source_group))["status"],
                "delegated_to_managed_exit",
            )
            self.assertFalse(
                any("repair-" in str(order.cOID or "") for order in source_group.orders)
            )
            await manager.close()
            journal.close()

    async def test_historical_full_exit_delegates_before_immediate_causal_fill(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            from src.market_engine.events import QuoteEvent

            event_time = datetime(2026, 8, 21, 14, 0, tzinfo=timezone.utc)
            broker = SimulatedBrokerAdapter(["DU1"], mode=TradingMode.BACKTEST)
            instrument = InstrumentContract("TEST", 123, "TEST", "STK", "USD")
            runtime_planner = RuntimeIbkrStrategyOrderPlanner(
                {"TEST": instrument},
                strategy_id="strategy-1",
                strategy_revision=1,
                run_id="run-1",
            )

            def bracket_planner(strategy_intent, account_id, event):
                return runtime_planner.plan(
                    intent=strategy_intent,
                    account_id=account_id,
                    event=event,
                )

            journal = TradingJournal(Path(directory) / "orders.sqlite3")
            await broker.initialize()
            risk = RiskAuthority()
            await risk.prime(broker, ["DU1"])
            manager = OrderManagementEngine(
                broker=broker,
                planner=bracket_planner,
                risk=risk,
                journal=journal,
                run_id="run-1",
                strategy_id="strategy-1",
                strategy_revision=1,
                policy=BrokerCommunicationPolicy(),
            )
            try:
                await broker.on_market_event(
                    QuoteEvent(
                        ask_exchange=11,
                        ask_price=10.02,
                        ask_size=1_000,
                        bid_exchange=12,
                        bid_price=10.00,
                        bid_size=1_000,
                        conditions=(),
                        indicators=(),
                        ingest_ts=event_time - timedelta(milliseconds=100),
                        raw={"conid": 123},
                        sequence=1,
                        source="test",
                        tape=3,
                        ticker="TEST",
                        ts=event_time - timedelta(milliseconds=100),
                    )
                )
                entry = replace(
                    intent(quantity=100),
                    intent_id="historical-entry-with-stop",
                    event_time=event_time,
                    reference_price=10.02,
                    profit_target_price=None,
                    metadata={
                        **intent(quantity=100).metadata,
                        "quote_observed_at": event_time.isoformat(),
                    },
                )
                await manager.submit_intent(
                    portfolio_approved(journal, entry),
                    account_id="DU1",
                    event=None,
                )
                source_group = next(
                    group
                    for group in manager._groups.values()
                    if group.intent.intent_id == entry.intent_id
                )
                self.assertEqual((await broker.positions("DU1"))[0].position, 100)

                exit_intent = replace(
                    intent(action="exit", quantity=100),
                    intent_id="historical-immediate-exit",
                    event_time=event_time,
                    reference_price=10.00,
                    metadata={
                        **intent(action="exit").metadata,
                        "position_quantity": 100.0,
                        "position_side": "long",
                        "quote_observed_at": event_time.isoformat(),
                    },
                )
                exit_snapshot = await manager.submit_intent(
                    portfolio_approved(journal, exit_intent),
                    account_id="DU1",
                    event=None,
                )

                self.assertEqual(exit_snapshot.filled_quantity, 100)
                self.assertTrue(source_group.protection_delegated)
                self.assertFalse(
                    any(
                        "repair-" in str(order.cOID or "")
                        for order in source_group.orders
                    )
                )
                self.assertEqual(await broker.positions("DU1"), [])
            finally:
                await manager.close()
                journal.close()

    async def test_exit_cancels_partially_filled_entry_before_submitting_sell(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            broker = SequencedCommandBroker()
            real_planner = IbkrStrategyOrderPlanner()
            instrument = InstrumentContract("TEST", 123, "TEST", "STK", "USD")

            def bracket_planner(strategy_intent, account_id, _event):
                return real_planner.plan(
                    account_id=account_id,
                    instrument=instrument,
                    intent=strategy_intent,
                    strategy_id="strategy-1",
                    strategy_revision=1,
                )

            journal = TradingJournal(Path(directory) / "orders.sqlite3")
            await broker.initialize()
            risk = RiskAuthority()
            await risk.prime(broker, ["DU1"])
            manager = OrderManagementEngine(
                broker=broker,
                planner=bracket_planner,
                risk=risk,
                journal=journal,
                run_id="run-1",
                strategy_id="strategy-1",
                strategy_revision=1,
                policy=BrokerCommunicationPolicy(),
            )
            try:
                entry = replace(
                    intent(quantity=100),
                    intent_id="partial-entry-before-exit",
                    profit_target_price=10.50,
                )
                entry_snapshot = await manager.submit_intent(
                    portfolio_approved(journal, entry),
                    account_id="DU1",
                    event=None,
                )
                parent_id = entry_snapshot.broker_order_ids[0]
                parent = broker._orders[parent_id]
                parent.filled = 40.0
                parent.status = OrderStatus.SUBMITTED
                source_group = manager._groups[entry_snapshot.group_id]
                source_group.filled_quantity = 40.0
                source_group.remaining_quantity = 60.0
                source_group.filled_by_broker_order[parent_id] = 40.0
                source_group.state = OrderManagementState.PARTIALLY_FILLED
                broker._positions["DU1"][123] = _Position(
                    conid=123,
                    ticker="TEST",
                    quantity=40.0,
                    avg_cost=10.01,
                )
                broker.command_sequence.clear()
                exit_request = replace(
                    intent(action="exit", urgency="very_urgent", quantity=40),
                    intent_id="exit-while-entry-remainder-working",
                    metadata={
                        **intent(action="exit").metadata,
                        "position_quantity": 40.0,
                        "position_side": "long",
                        "reason_code": "downside_macd_closed",
                    },
                )

                exit_snapshot = await manager.submit_intent(
                    portfolio_approved(journal, exit_request),
                    account_id="DU1",
                    event=None,
                )

                self.assertEqual(
                    broker.command_sequence[0],
                    f"cancel:{parent_id}",
                )
                self.assertTrue(
                    any(step.startswith("place:SELL") for step in broker.command_sequence[1:])
                )
                self.assertEqual(parent.status, OrderStatus.CANCELLED)
                self.assertEqual(exit_snapshot.action, "exit")
                self.assertEqual(exit_snapshot.remaining_quantity, 40.0)
                self.assertEqual(source_group.state, OrderManagementState.CANCEL_PENDING)
                records = [
                    row
                    for row in journal.records("run-1")
                    if row.category == "order_management"
                    and row.entity_type == "entry_acquisition_frozen_before_exit"
                ]
                self.assertEqual(len(records), 1)
                self.assertEqual(
                    records[0].payload["command_order"],
                    "cancel_entries_then_submit_exit",
                )
            finally:
                await manager.close()
                journal.close()

    async def test_full_exit_reuses_partially_filled_target_at_cumulative_quantity(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            broker = RecordingBroker()
            real_planner = IbkrStrategyOrderPlanner()
            instrument = InstrumentContract("TEST", 123, "TEST", "STK", "USD")

            def bracket_planner(strategy_intent, account_id, _event):
                return real_planner.plan(
                    account_id=account_id,
                    instrument=instrument,
                    intent=strategy_intent,
                    strategy_id="strategy-1",
                    strategy_revision=1,
                )

            journal = TradingJournal(Path(directory) / "orders.sqlite3")
            await broker.initialize()
            risk = RiskAuthority()
            await risk.prime(broker, ["DU1"])
            manager = OrderManagementEngine(
                broker=broker,
                planner=bracket_planner,
                risk=risk,
                journal=journal,
                run_id="run-1",
                strategy_id="strategy-1",
                strategy_revision=1,
                policy=BrokerCommunicationPolicy(),
            )
            try:
                entry = StrategyIntent(
                    **{
                        **intent(quantity=100).payload(),
                        "intent_id": "partially-targeted-entry",
                        "profit_target_price": 10.50,
                    }
                )
                entry_snapshot = await manager.submit_intent(
                    portfolio_approved(journal, entry), account_id="DU1", event=None
                )
                parent_id, target_id = entry_snapshot.broker_order_ids[:2]
                broker._orders[parent_id].status = OrderStatus.FILLED
                broker._orders[parent_id].filled = 100.0
                broker._orders[target_id].status = OrderStatus.SUBMITTED
                broker._orders[target_id].filled = 40.0
                broker._positions["DU1"][123] = _Position(
                    conid=123,
                    ticker="TEST",
                    quantity=60.0,
                    avg_cost=10.0,
                )
                exit_request = replace(
                    intent(action="exit", urgency="very_urgent", quantity=60),
                    intent_id="exit-after-partial-target",
                    metadata={
                        **intent(action="exit").metadata,
                        "position_quantity": 60.0,
                        "reason_code": "macd_signal_crossed_above_line",
                    },
                )

                exit_snapshot = await manager.submit_intent(
                    portfolio_approved(journal, exit_request),
                    account_id="DU1",
                    event=None,
                )

                self.assertEqual(exit_snapshot.state, OrderManagementState.ACKNOWLEDGED)
                self.assertEqual(exit_snapshot.remaining_quantity, 60.0)
                self.assertEqual(broker.modifications[-1][0], target_id)
                self.assertEqual(broker.modifications[-1][1].quantity, 100.0)
                adopted = manager._groups[exit_snapshot.group_id]
                self.assertEqual(adopted.filled_by_broker_order[target_id], 40.0)
            finally:
                await manager.close()
                journal.close()

    async def test_full_exit_reprices_every_sliced_target_without_resizing_one_child(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            broker = RecordingBroker()
            real_planner = IbkrStrategyOrderPlanner()
            instrument = InstrumentContract("TEST", 123, "TEST", "STK", "USD")

            def bracket_planner(strategy_intent, account_id, _event):
                return real_planner.plan(
                    account_id=account_id,
                    instrument=instrument,
                    intent=strategy_intent,
                    strategy_id="strategy-1",
                    strategy_revision=1,
                )

            journal = TradingJournal(Path(directory) / "orders.sqlite3")
            await broker.initialize()
            risk = RiskAuthority()
            await risk.prime(broker, ["DU1"])
            manager = OrderManagementEngine(
                broker=broker,
                planner=bracket_planner,
                risk=risk,
                journal=journal,
                run_id="run-1",
                strategy_id="strategy-1",
                strategy_revision=1,
                policy=BrokerCommunicationPolicy(),
            )
            profile = ProtectionProfile(
                profile_id="two-slice",
                revision=1,
                slices=(
                    ProtectionSlice(
                        "first",
                        0.5,
                        StopRule(StopRuleType.FIXED_PRICE, price=9.5),
                        profit_target_price=10.5,
                    ),
                    ProtectionSlice(
                        "second",
                        0.5,
                        StopRule(StopRuleType.FIXED_PRICE, price=9.5),
                        profit_target_price=11.0,
                    ),
                ),
            )
            entry = replace(
                intent(quantity=100),
                intent_id="two-slice-entry",
                protection_profile=profile,
            )
            entry_snapshot = await manager.submit_intent(
                portfolio_approved(journal, entry),
                account_id="DU1",
                event=None,
            )
            exit_intent = replace(
                intent(action="exit", urgency="very_urgent", quantity=100),
                intent_id="two-slice-exit",
                invalidation_price=9.4,
                metadata={
                    **intent(action="exit").metadata,
                    "position_quantity": 100.0,
                    "reason_code": "downside_macd_closed",
                },
            )

            exit_snapshot = await manager.submit_intent(
                portfolio_approved(journal, exit_intent),
                account_id="DU1",
                event=None,
            )

            self.assertEqual(exit_snapshot.action, "exit")
            self.assertEqual(len(exit_snapshot.broker_order_ids), 2)
            self.assertEqual(len(broker.modifications), 2)
            self.assertEqual(
                {replacement.quantity for _, replacement in broker.modifications},
                {50.0},
            )
            self.assertEqual(
                set(exit_snapshot.broker_order_ids),
                {entry_snapshot.broker_order_ids[1], entry_snapshot.broker_order_ids[4]},
            )
            source_group = next(
                group
                for group in manager._groups.values()
                if group.intent.intent_id == entry.intent_id
            )
            self.assertTrue(source_group.protection_delegated)
            self.assertEqual(
                (await manager.reconcile_protection(source_group))["status"],
                "delegated_to_managed_exit",
            )
            await manager.close()
            journal.close()

    async def test_full_exit_reconciles_target_fill_race_without_duplicate_sell(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            broker = TerminalTargetRaceBroker()
            real_planner = IbkrStrategyOrderPlanner()
            instrument = InstrumentContract("TEST", 123, "TEST", "STK", "USD")

            def bracket_planner(strategy_intent, account_id, _event):
                return real_planner.plan(
                    account_id=account_id,
                    instrument=instrument,
                    intent=strategy_intent,
                    strategy_id="strategy-1",
                    strategy_revision=1,
                )

            journal = TradingJournal(Path(directory) / "orders.sqlite3")
            await broker.initialize()
            risk = RiskAuthority()
            await risk.prime(broker, ["DU1"])
            manager = OrderManagementEngine(
                broker=broker,
                planner=bracket_planner,
                risk=risk,
                journal=journal,
                run_id="run-1",
                strategy_id="strategy-1",
                strategy_revision=1,
                policy=BrokerCommunicationPolicy(),
            )
            entry = StrategyIntent(
                **{
                    **intent(quantity=16).payload(),
                    "intent_id": "entry-before-target-race",
                    "profit_target_price": 10.50,
                }
            )
            entry_snapshot = await manager.submit_intent(
                portfolio_approved(journal, entry), account_id="DU1", event=None
            )
            target_id = entry_snapshot.broker_order_ids[1]
            parent_id = entry_snapshot.broker_order_ids[0]
            broker._orders[parent_id].status = OrderStatus.FILLED
            broker._orders[parent_id].filled = 16.0
            broker._orders[target_id].status = OrderStatus.SUBMITTED
            broker._positions["DU1"][123] = _Position(
                conid=123,
                ticker="TEST",
                quantity=16.0,
                avg_cost=10.0,
            )
            broker.terminal_target_id = target_id
            exit_request = replace(
                intent(action="exit", urgency="very_urgent", quantity=16),
                intent_id="exit-during-target-race",
                invalidation_price=9.4,
                metadata={
                    **intent(action="exit").metadata,
                    "position_quantity": 16.0,
                    "reason_code": "downside_macd_closed",
                },
            )

            exit_snapshot = await manager.submit_intent(
                portfolio_approved(journal, exit_request),
                account_id="DU1",
                event=None,
            )

            self.assertTrue(broker.raced)
            self.assertEqual(exit_snapshot.state, OrderManagementState.FILLED)
            self.assertEqual(exit_snapshot.remaining_quantity, 0.0)
            self.assertEqual(exit_snapshot.broker_order_ids, ())
            self.assertEqual(len(await broker.live_orders()), len(entry_snapshot.broker_order_ids))
            events = journal.order_management_records()
            self.assertTrue(
                any(
                    row.entity_type == "protected_exit_already_satisfied"
                    for row in events
                )
            )
            await manager.close()
            journal.close()

    async def test_protective_child_fill_is_reported_as_exit_not_entry(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            broker = RecordingBroker()
            real_planner = IbkrStrategyOrderPlanner()
            instrument = InstrumentContract("TEST", 123, "TEST", "STK", "USD")
            callbacks = []

            async def capture(snapshot):
                callbacks.append(snapshot)

            def bracket_planner(strategy_intent, account_id, _event):
                return real_planner.plan(
                    account_id=account_id,
                    instrument=instrument,
                    intent=strategy_intent,
                    strategy_id="strategy-1",
                    strategy_revision=1,
                )

            journal = TradingJournal(Path(directory) / "orders.sqlite3")
            await broker.initialize()
            risk = RiskAuthority()
            await risk.prime(broker, ["DU1"])
            manager = OrderManagementEngine(
                broker=broker,
                planner=bracket_planner,
                risk=risk,
                journal=journal,
                run_id="run-1",
                strategy_id="strategy-1",
                strategy_revision=1,
                policy=BrokerCommunicationPolicy(),
                fill_callback=capture,
            )
            entry = StrategyIntent(
                **{
                    **intent().payload(),
                    "intent_id": "entry-with-target",
                    "profit_target_price": 10.50,
                }
            )
            snapshot = await manager.submit_intent(
                portfolio_approved(journal, entry), account_id="DU1", event=None
            )
            child = LiveOrder(
                account="DU1",
                orderId=snapshot.broker_order_ids[1],
                conid=123,
                ticker="TEST",
                side="SELL",
                orderType="LMT",
                tif="DAY",
                totalSize=100,
                filledQuantity=100,
                remainingQuantity=0,
                avgPrice=10.50,
                order_status=OrderStatus.FILLED,
                parentId=snapshot.client_order_ids[0],
            )
            result = await manager.on_order_update(child)
            assert result is not None
            self.assertEqual(result.action, "exit")
            self.assertEqual(result.fill_role, "profit_target")
            self.assertEqual(callbacks[-1].action, "exit")
            await manager.close()
            journal.close()

    async def test_protective_fill_cancels_unfilled_entry_remainder(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            broker = RecordingBroker()
            real_planner = IbkrStrategyOrderPlanner()
            instrument = InstrumentContract("TEST", 123, "TEST", "STK", "USD")

            def bracket_planner(strategy_intent, account_id, _event):
                return real_planner.plan(
                    account_id=account_id,
                    instrument=instrument,
                    intent=strategy_intent,
                    strategy_id="strategy-1",
                    strategy_revision=1,
                )

            journal = TradingJournal(Path(directory) / "orders.sqlite3")
            await broker.initialize()
            risk = RiskAuthority()
            await risk.prime(broker, ["DU1"])
            manager = OrderManagementEngine(
                broker=broker,
                planner=bracket_planner,
                risk=risk,
                journal=journal,
                run_id="run-1",
                strategy_id="strategy-1",
                strategy_revision=1,
                policy=BrokerCommunicationPolicy(),
            )
            entry = StrategyIntent(
                **{
                    **intent(quantity=100).payload(),
                    "intent_id": "partial-entry-with-target",
                    "profit_target_price": 10.50,
                }
            )
            snapshot = await manager.submit_intent(
                portfolio_approved(journal, entry), account_id="DU1", event=None
            )
            parent_id, child_id = snapshot.broker_order_ids[:2]
            await manager.on_order_update(
                LiveOrder(
                    account="DU1", orderId=parent_id, conid=123, ticker="TEST",
                    side="BUY", orderType="LMT", tif="DAY", totalSize=100,
                    filledQuantity=25, remainingQuantity=75, avgPrice=10.02,
                    order_status=OrderStatus.SUBMITTED,
                    cOID=snapshot.client_order_ids[0],
                )
            )
            await manager.on_order_update(
                LiveOrder(
                    account="DU1", orderId=child_id, conid=123, ticker="TEST",
                    side="SELL", orderType="LMT", tif="DAY", totalSize=100,
                    filledQuantity=25, remainingQuantity=75, avgPrice=10.50,
                    order_status=OrderStatus.SUBMITTED,
                    parentId=snapshot.client_order_ids[0],
                )
            )

            parent = next(
                row for row in await broker.live_orders()
                if str(row.orderId) == str(parent_id)
            )
            self.assertEqual(parent.order_status, OrderStatus.CANCELLED)
            cancel_records = [
                row.payload
                for row in journal.order_management_records(limit=100)
                if row.entity_type == "order_cancel_requested"
            ]
            self.assertTrue(
                any(
                    row.get("reason")
                    == "protective_exit_started_before_entry_complete"
                    for row in cancel_records
                ),
                cancel_records,
            )
            await manager.close()
            journal.close()


if __name__ == "__main__":
    unittest.main()
