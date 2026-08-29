from __future__ import annotations

import tempfile
import unittest
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

from src.trading_runtime.domain import InstrumentContract, TradingMode
from src.trading_runtime.ibkr_schema import LiveOrder, OrderRequest, OrderStatus
from src.trading_runtime.execution_policies import (
    ExecutionMarketSnapshot,
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
from src.trading_runtime.simulated_broker import SimulatedBrokerAdapter
from src.trading_runtime.strategy_orders import IbkrStrategyOrderPlanner, StrategyOrderPlan


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
        )
        return manager, journal

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
