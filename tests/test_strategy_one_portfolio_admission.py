"""Strategy 1 uses the shared Portfolio with a disk-free Backtest journal."""
import asyncio
from concurrent.futures import Future
from dataclasses import dataclass, replace
from datetime import date, datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import UUID

import pytest

from src.backend.backtest_journal_memory import BacktestMemoryJournal
from src.backend.backtest_typed_projection import project_pending_backtest_v4_prefix
from src.backend.backtest_typed_publisher import BacktestTypedJournalPublisher
from src.trading_runtime.arte_journal_commit_v4 import (
    load_verified_v4_prefix, publish_base_typed_batch_v4,
    publish_broker_acknowledgement_batch_v4,
    publish_protection_change_batch_v4,
    publish_protection_reconciliation_batch_v4,
    publish_strategy_one_entry_batch_v4,
)
from src.trading_runtime.arte_journal_projection import project_journal_record
from src.trading_runtime.arte_broker_acknowledgement_v4 import project_broker_acknowledgement_v4
from src.trading_runtime.arte_protection_change_v4 import protection_change_batch_v4
from src.trading_runtime.arte_protection_reconciliation_v4 import (
    V4ProtectionReconciliationBatch, project_protection_reconciliation_v4,
)
from src.trading_runtime.arte_intent_projection import strategy_intent_batch
from src.trading_runtime.arte_oms_projection import oms_group_state_batch
from src.trading_runtime.ibkr_schema import AccountLedger, AccountSummary
from src.trading_runtime.domain import InstrumentContract, TradingMode
from src.trading_runtime.execution_policies import ExecutionMarketSnapshot
from src.trading_runtime.order_management import BrokerCommunicationPolicy, OrderManagementEngine
from src.trading_runtime.portfolio import (
    PortfolioAccountProfile, PortfolioManagementEngine, PortfolioPolicy,
)
from src.trading_runtime.strategy_one_intent import strategy_one_entry_intent
from src.trading_runtime.strategy_one_protection_intent import strategy_one_protection_intents
from test_strategy_one_protection_intent import (
    financial as protection_financial, previous as previous_protection,
    transition as protection_transition,
)
from src.trading_runtime.strategy_one_stateful import StrategyOneEntryProposal
from src.trading_runtime.strategy_orders import RuntimeIbkrStrategyOrderPlanner
from src.trading_runtime.simulated_broker import SimulatedBrokerAdapter
from src.trading_runtime.risk import RiskAuthority
from src.trading_runtime.runtime import RunMode, TradingRuntime


def test_numbered_proposal_uses_shared_runtime_portfolio_and_oms_path():
    session = date(2026, 8, 18)
    proposal = StrategyOneEntryProposal(
        "assignment-1", "DU1", "AAA", 31_000, 30_000,
        10.01, 9.89, 12., "R4", .5, 30_000, "S1")
    intent = strategy_one_entry_intent(proposal, session_date=session)
    approved = replace(intent, quantity=5., metadata={"assignment_id": "assignment-1"})

    @dataclass
    class Submitted:
        filled_quantity: float = 0.

    decision = SimpleNamespace(payload=lambda: {"status": "approved"})
    runtime = object.__new__(TradingRuntime)
    runtime.config = SimpleNamespace(
        mode=RunMode.BACKTEST, strategy_id="early-squeeze-strategy",
        strategy_revision=1, account_ids=("DU1",), anchor_date=session)
    runtime.run_id = str(UUID(int=120))
    runtime.journal = BacktestMemoryJournal(run_id=runtime.run_id)
    runtime.intent_planner = object()
    runtime.order_manager = SimpleNamespace(submit_intent=AsyncMock(
        return_value=Submitted()))
    runtime.portfolio = SimpleNamespace(approve=AsyncMock(
        return_value=(decision, approved)), _typed_recovery=False)
    runtime.strategy = SimpleNamespace(assignments=lambda: ())
    runtime.last_event_time = intent.event_time
    runtime._refresh_portfolio_from_broker = AsyncMock()

    result = asyncio.run(runtime.submit_strategy_one_proposal(proposal))
    assert result == [{"decision": {"status": "approved"},
                       "order_group": {"filled_quantity": 0.}}]
    runtime.portfolio.approve.assert_awaited_once_with(
        intent, account_id="DU1", assignment_id="assignment-1")
    runtime.order_manager.submit_intent.assert_awaited_once_with(
        approved, account_id="DU1", event=None)
    source = runtime.journal.records(runtime.run_id)[0]
    assert source.entity_type == "strategy_intent"
    assert runtime.journal.strategy_one_entry_for_record(source.record_id) == (
        proposal, session)
    runtime.portfolio.approve.return_value = (
        decision, replace(approved, metadata={"assignment_id": "other"}))
    with pytest.raises(RuntimeError, match="lost its normalized assignment"):
        asyncio.run(runtime.submit_strategy_one_proposal(proposal))
    assert runtime.order_manager.submit_intent.await_count == 1
    runtime.config.mode = RunMode.REPLAY
    with pytest.raises(ValueError, match="numbered Backtest runtime"):
        asyncio.run(runtime.submit_strategy_one_proposal(proposal))
    assert len(runtime.journal.records(runtime.run_id)) == 2
    runtime.journal.close()


def test_numbered_protection_routes_target_then_stop_and_confirms_state():
    session = date(2026, 8, 18)
    financial = protection_financial()
    previous = previous_protection()
    transition = protection_transition()
    intents = strategy_one_protection_intents(
        previous, transition, financial, session_date=session,
        bid=10., ask=10.01)
    submitted = []

    @dataclass
    class Submitted:
        filled_quantity: float = 0.

    async def approve(intent, *, account_id, assignment_id):
        assert account_id == "DU1" and assignment_id == "assignment-1"
        return (SimpleNamespace(payload=lambda: {"status": "approved"}),
                replace(intent, metadata={"assignment_id": assignment_id}))

    async def submit(intent, *, account_id, event):
        submitted.append(intent.action)
        return Submitted()

    runtime = object.__new__(TradingRuntime)
    runtime.config = SimpleNamespace(
        mode=RunMode.BACKTEST, strategy_id="early-squeeze-strategy",
        strategy_revision=1, account_ids=("DU1",), anchor_date=session)
    runtime.run_id = str(UUID(int=121))
    runtime.journal = BacktestMemoryJournal(run_id=runtime.run_id)
    runtime.intent_planner = object()
    runtime.order_manager = SimpleNamespace(submit_intent=submit)
    runtime.portfolio = SimpleNamespace(
        approve=approve, release_intent=lambda *_a, **_k: None,
        _typed_recovery=False)
    runtime.strategy = SimpleNamespace(assignments=lambda: ())
    runtime.last_event_time = intents[0].event_time
    runtime._refresh_portfolio_from_broker = AsyncMock()
    state = asyncio.run(runtime.submit_strategy_one_protection(
        previous, transition, financial, bid=10., ask=10.01))
    assert state == transition.state
    assert submitted == ["replace_profit_target", "replace_protective_stop"]
    records = runtime.journal.records(runtime.run_id)
    assert [record.entity_id for record in records] == [
        intent.intent_id for intent in intents]
    assert all(record.payload["metadata"] == {} for record in records)
    runtime.portfolio.approve = AsyncMock(return_value=(
        SimpleNamespace(reasons=("no_broker_position_to_protect",),
                        payload=lambda: {"status": "rejected"}), None))
    runtime._fund_momentum_request = AsyncMock()
    runtime._record_intent_rejection = AsyncMock()
    with pytest.raises(RuntimeError, match="was not confirmed"):
        asyncio.run(runtime.submit_strategy_one_protection(
            previous, transition, financial, bid=10., ask=10.01))
    assert submitted == ["replace_profit_target", "replace_protective_stop"]
    runtime.journal.close()


def test_strategy_one_initial_admission_uses_no_sqlite_or_disk():
    at = datetime(2026, 8, 18, 8, 0, 31, tzinfo=timezone.utc)

    async def exercise():
        run_id = str(UUID(int=1))
        journal = BacktestMemoryJournal(run_id=run_id)
        profile = PortfolioAccountProfile(
            "cash", "DU1", "backtest", "simulated",
            PortfolioPolicy(maximum_position_fraction=1.,
                            maximum_ticker_fraction=1.,
                            maximum_planned_risk_fraction=.5,
                            maximum_open_risk_fraction=.5,
                            entry_fee_buffer_bps=0.,
                            allow_outside_rth=True))
        portfolio = PortfolioManagementEngine(
            [profile], journal=journal, run_id=run_id,
            strategy_id="early-squeeze-strategy", strategy_revision=1,
            event_clock=lambda: at)
        portfolio.synchronize_snapshot(
            "DU1", summary=AccountSummary(
                account_id="DU1", netliquidation=9000, totalcashvalue=9000,
                buyingpower=9000, grosspositionvalue=0, availablefunds=9000,
                excessliquidity=9000, timestamp=at),
            ledger=AccountLedger(
                acctId="DU1", cashbalance=9000, settledcash=9000,
                stockmarketvalue=0, netliquidationvalue=9000,
                realizedpnl=0, unrealizedpnl=0, timestamp=at),
            positions=[])
        proposal = StrategyOneEntryProposal(
            "assignment-1", "DU1", "AAA", 31_000, 30_000,
            10.01, 9.89, 12., "R4", .5, 30_000, "S1")
        intent = strategy_one_entry_intent(proposal, session_date=date(2026, 8, 18))
        assert intent.metadata == {}
        decision, approved = await portfolio.approve(
            intent, account_id="DU1", assignment_id=proposal.assignment_id)
        return decision, approved, journal.records(journal.run_id)

    decision, approved, records = asyncio.run(exercise())
    assert approved is not None, decision.reasons
    assert approved.quantity > 0
    assert approved.metadata["assignment_id"] == "assignment-1"
    assert records
    assert decision.decided_at == at
    assert all(record.event_time == at for record in records)
    assert all(record.category == "portfolio_management" for record in records)
    assert {record.entity_type for record in records} == {
        "portfolio_decision", "portfolio_reservation",
    }
    reservation = next(record for record in records
                       if record.entity_type == "portfolio_reservation")
    assert reservation.payload["assignment_id"] == "assignment-1"
    for index, record in enumerate(records, start=1):
        projected = project_journal_record(
            record, run_month=date(2026, 8, 1), attempt_id=str(UUID(int=2)),
            batch_id=str(UUID(int=index + 2)),
            prior_batch_id=str(UUID(int=index + 1)) if index > 1 else str(UUID(int=0)),
            source_cursor="2026-08-18:30000", expected_mode="backtest",
        )
        assert len(projected.events) == 1
        assert projected.events[0]["event_month"] == "2026-08-01"


def test_strategy_one_approved_intent_reaches_causal_oms_without_sqlite():
    at = datetime(2026, 8, 18, 8, 0, 31, tzinfo=timezone.utc)

    async def exercise():
        run_id = str(UUID(int=11))
        journal = BacktestMemoryJournal(run_id=run_id)
        broker = SimulatedBrokerAdapter(["DU1"], mode=TradingMode.BACKTEST,
                                        initial_time=at)
        await broker.initialize()
        risk = RiskAuthority()
        await risk.prime(broker, ["DU1"])
        policy = PortfolioPolicy(maximum_position_fraction=1.,
                                 maximum_ticker_fraction=1.,
                                 maximum_planned_risk_fraction=.5,
                                 maximum_open_risk_fraction=.5,
                                 allow_outside_rth=True)
        portfolio = PortfolioManagementEngine(
            [PortfolioAccountProfile("cash", "DU1", "backtest", "simulated", policy)],
            journal=journal, run_id=run_id, strategy_id="strategy-1",
            strategy_revision=1, event_clock=lambda: at)
        portfolio.synchronize_snapshot(
            "DU1", summary=AccountSummary(
                account_id="DU1", netliquidation=9000, totalcashvalue=9000,
                buyingpower=9000, grosspositionvalue=0, availablefunds=9000,
                excessliquidity=9000, timestamp=at),
            ledger=AccountLedger(
                acctId="DU1", cashbalance=9000, settledcash=9000,
                stockmarketvalue=0, netliquidationvalue=9000,
                realizedpnl=0, unrealizedpnl=0, timestamp=at), positions=[])
        proposal = StrategyOneEntryProposal(
            "assignment-1", "DU1", "AAA", 31_000, 30_000,
            10.01, 9.89, 12., "R4", .5, 30_000, "S1")
        intent = strategy_one_entry_intent(proposal, session_date=date(2026, 8, 18))
        source_record = journal.append_strategy_one_intent(
            intent=intent, proposal=proposal, session_date=date(2026, 8, 18),
            account_id="DU1", strategy_id="strategy-1", strategy_revision=1)
        _, approved = await portfolio.approve(
            intent, account_id="DU1", assignment_id=proposal.assignment_id)
        assert approved is not None
        instrument = InstrumentContract("AAA", 123, "AAA", "STK", "USD")
        planner = RuntimeIbkrStrategyOrderPlanner(
            {"AAA": instrument}, strategy_id="strategy-1",
            strategy_revision=1, run_id=run_id)
        manager = OrderManagementEngine(
            broker=broker,
            planner=lambda item, account_id, event: planner.plan(
                account_id=account_id, intent=item, event=event),
            risk=risk, journal=journal, run_id=run_id,
            strategy_id="strategy-1", strategy_revision=1,
            policy=BrokerCommunicationPolicy(), causal_execution_clock=True)
        manager.on_market_snapshot(ExecutionMarketSnapshot(
            "AAA", 9.99, 10.01, .01, at, "qmd-history"))
        try:
            group = await asyncio.wait_for(manager.submit_intent(
                approved, account_id="DU1", event=None), 5)
            records = journal.records(run_id)
            frozen = tuple(journal.oms_group_for_record(record.record_id)
                           for record in records
                           if record.entity_type == "order_group_state")
            admissions = tuple(journal.oms_admission_for_record(record.record_id)
                               for record in records
                               if record.entity_type == "order_group_state")
            assert admissions and all(row is not None and
                                      row["assignment_id"] == "assignment-1"
                                      for row in admissions)
            source = strategy_intent_batch(
                intent, run_id=run_id, run_month=date(2026, 8, 1),
                account_id="DU1", attempt_id=str(UUID(int=14)),
                batch_id=str(UUID(int=15)), prior_batch_id=str(UUID(int=0)),
                sequence=1, source_cursor="intent", run_status="running",
                recorded_at=at)
            transitions = tuple(record for record in records
                                if record.entity_type == "order_group_state")
            for transition, captured, admission in zip(
                    transitions, frozen, admissions, strict=True):
                projected = oms_group_state_batch(
                    captured, run_id=run_id, run_month=date(2026, 8, 1),
                    attempt_id=str(UUID(int=14)),
                    batch_id=str(UUID(int=16 + transition.sequence)),
                    prior_batch_id=str(UUID(int=15)), sequence=transition.sequence,
                    source_cursor="oms", run_status="running",
                    strategy_id="strategy-1", strategy_revision=1,
                    recorded_at=transition.recorded_at,
                    published_intent_batch=source,
                    committed_intent_batch_id=source.batch_id,
                    admission_source_intent=intent,
                    admission_reservation=admission,
                    journal_record_id=transition.record_id)
                assert projected.events[0]["record_id"] == transition.record_id
            projected_prefix = project_pending_backtest_v4_prefix(
                journal, attempt_id=str(UUID(int=14)),
                run_month=date(2026, 8, 1), prior_sequence=0,
                source_cursor="2026-08-18:31000",
                expected_config={"strategy_id": "strategy-1",
                                 "strategy_revision": 1},
                through_sequence=records[-1].sequence)
            projected_events = [event for unit in projected_prefix
                                for event in (unit.base if hasattr(unit, "base")
                                              else unit).events]
            assert len(projected_events) == len(records)
            assert [event["sequence"] for event in projected_events] == [
                record.sequence for record in records]
            assert any((event["category"], event["entity_type"])
                       == ("order_management", "order_group_state")
                       for event in projected_events)
            assert projected_events[0]["record_id"] == source_record.record_id
            class FencedV4Writer:
                run_mode = "backtest"
                journal_profile = "backtest_v4"
                coalesce_batches = False
                max_events_per_commit = 1

                def __init__(self):
                    self.run_id = run_id
                    self.units = []
                    from tests.test_arte_journal_commit_v4 import attached_v4_client
                    self.client = attached_v4_client()

                def _receipt(self, unit, publish):
                    self.units.append(unit)
                    result = Future()
                    result.set_result(publish())
                    return result

                def submit_base_v4(self, batch):
                    return self._receipt(batch, lambda: publish_base_typed_batch_v4(
                        self.client, batch))

                def submit_strategy_one_entry_v4(self, unit):
                    return self._receipt(unit, lambda: publish_strategy_one_entry_batch_v4(
                        self.client, unit.base, entry_evidence=unit.entry_evidence))

                def submit_broker_acknowledgement_v4(self, unit):
                    return self._receipt(unit, lambda: publish_broker_acknowledgement_batch_v4(
                        self.client, unit.base, acknowledgement=unit.acknowledgement))

                def submit_protection_change_v4(self, unit):
                    return self._receipt(unit, lambda: publish_protection_change_batch_v4(
                        self.client, unit.base, change=unit.change,
                        entry_orders=unit.entry_orders))

                def submit_protection_reconciliation_v4(self, unit):
                    return self._receipt(unit, lambda: publish_protection_reconciliation_batch_v4(
                        self.client, unit.base,
                        reconciliation=unit.reconciliation,
                        actions=unit.actions, replies=unit.replies))

            writer = FencedV4Writer()
            publisher = BacktestTypedJournalPublisher(
                journal, writer, attempt_id=str(UUID(int=14)),
                run_month=date(2026, 8, 1), batch_size=1,
                expected_config={"strategy_id": "strategy-1",
                                 "strategy_revision": 1})
            receipt = await publisher.enqueue_pending()
            assert receipt.last_sequence == records[-1].sequence
            assert len(writer.units) == len(records)
            assert intent.intent_id in publisher._committed_strategy_intents
            assert journal.pending_record_count == 0
            assert load_verified_v4_prefix(
                writer.client, run_id).last_sequence == records[-1].sequence
            latest = frozen[-1]
            assert latest is not None
            manager._groups[group.group_id].broker_order_ids.append("later-mutation")
            assert "later-mutation" not in latest.broker_order_ids
            journal.mark_fenced(records[-1].sequence)
            assert all(journal.oms_group_for_record(record.record_id) is None
                       for record in records)
            assert all(journal.oms_admission_for_record(record.record_id) is None
                       for record in records)
            next_at = at + timedelta(milliseconds=100)
            last_us = int(next_at.timestamp() * 1_000_000) - 1
            executions = await broker.on_liquidity_bar({
                "ticker": "AAA", "resolution_ms": 100,
                "bucket_index": 311, "event_count": 3,
                "last_event_us": last_us, "quote_valid": 1,
                "quote_timestamp_us": last_us - 10_000,
                "bid_int": 99_900, "ask_int": 100_100,
                "bid_size": 100, "ask_size": 100,
                "price_valid": 1, "extremes_valid": 1,
                "close_int": 100_100, "low_int": 99_800,
                "high_int": 100_200, "execution_volume": 100,
            }, at=next_at)
            assert executions, "The approved Strategy 1 entry did not fill"
            fill_runtime = object.__new__(TradingRuntime)
            fill_runtime.run_id = run_id
            fill_runtime.journal = journal
            fill_runtime._record_executions(executions)
            await manager.reconcile()
            fill_records = journal.unfenced_records()
            assert any((row.category, row.entity_type) == ("execution", "fill")
                       for row in fill_records)
            fill_units = project_pending_backtest_v4_prefix(
                journal, attempt_id=str(UUID(int=14)),
                run_month=date(2026, 8, 1),
                prior_sequence=records[-1].sequence,
                prior_batch_id=publisher._batch_id,
                source_cursor=publisher._source_cursor,
                expected_config={"strategy_id": "strategy-1",
                                 "strategy_revision": 1},
                published_sources=publisher._committed_strategy_intents,
                committed_order_lineage=publisher._committed_order_lineage,
                through_sequence=fill_records[0].sequence)
            assert len(fill_units) == 1
            assert fill_units[0].executions[0]["strategy_id"] == "strategy-1"
            assert fill_units[0].executions[0]["signal_price"] == "10.0100000000"
            tampered = replace(fill_records[0], payload={
                **fill_records[0].payload, "canonical_strategy_revision": 2,
            })
            with pytest.raises(ValueError, match="Fill lineage differs"):
                project_journal_record(
                    tampered, run_month=date(2026, 8, 1),
                    attempt_id=str(UUID(int=14)), batch_id=str(UUID(int=99)),
                    prior_batch_id=publisher._batch_id,
                    source_cursor=publisher._source_cursor,
                    expected_mode="backtest",
                    committed_order_lineage=publisher._committed_order_lineage)
            full_suffix = project_pending_backtest_v4_prefix(
                journal, attempt_id=str(UUID(int=14)),
                run_month=date(2026, 8, 1),
                prior_sequence=records[-1].sequence,
                prior_batch_id=publisher._batch_id,
                source_cursor=publisher._source_cursor,
                expected_config={"strategy_id": "strategy-1",
                                 "strategy_revision": 1},
                published_sources=publisher._committed_strategy_intents,
                committed_order_lineage=publisher._committed_order_lineage,
                through_sequence=fill_records[-1].sequence)
            assert sum(len((unit.base if hasattr(unit, "base") else unit).events)
                       for unit in full_suffix) == len(fill_records)
            reconciliation = next(row for row in fill_records
                                  if row.entity_type == "protection_reconciliation")
            frozen_reconciliation = next(
                unit for unit in full_suffix
                if isinstance(unit, V4ProtectionReconciliationBatch))
            with pytest.raises(TypeError):
                frozen_reconciliation.replies[0]["order_status"] = "Mutated"
            original_reply = frozen_reconciliation.replies[0]["order_status"]
            mutable_reply = dict(frozen_reconciliation.replies[0])
            copied = replace(frozen_reconciliation, replies=(mutable_reply,))
            mutable_reply["order_status"] = "Mutated"
            assert copied.replies[0]["order_status"] == original_reply
            bad_action = {**reconciliation.payload["actions"][0], "opaque": {"x": 1}}
            with pytest.raises(ValueError, match="unmodeled fields"):
                project_protection_reconciliation_v4(
                    replace(reconciliation, payload={
                        **reconciliation.payload, "actions": [bad_action]}),
                    attempt_id=str(UUID(int=14)), batch_id=str(UUID(int=101)))
            suffix_receipt = await publisher.enqueue_pending()
            assert suffix_receipt.last_sequence == fill_records[-1].sequence
            assert journal.pending_record_count == 0
            assert load_verified_v4_prefix(
                writer.client, run_id).last_sequence == fill_records[-1].sequence
            replies = writer.client.tables["trading_protection_reconciliation_reply_v4"]
            assert len(replies) == 2
            replies[0]["order_status"] = "Tampered"
            with pytest.raises(RuntimeError, match="typed detail differs"):
                load_verified_v4_prefix(writer.client, run_id)
            return group, records, frozen
        finally:
            await manager.close()
            journal.close()

    group, records, frozen = asyncio.run(exercise())
    assert {(record.category, record.entity_type) for record in records} == {
        ("strategy", "strategy_intent"),
        ("broker", "order_acknowledgement"),
        ("command", "order"),
        ("order_management", "order_group_state"),
        ("portfolio_management", "portfolio_decision"),
        ("portfolio_management", "portfolio_reservation"),
        ("protection", "protection_change"),
    }
    assert group.assignment_id == "assignment-1"
    assert group.broker_order_ids, group
    acknowledgements = [record for record in records
                        if (record.category, record.entity_type)
                        == ("broker", "order_acknowledgement")]
    assert acknowledgements
    for record in acknowledgements:
        projected_ack = project_broker_acknowledgement_v4(
            record, attempt_id=str(UUID(int=15)), batch_id=str(UUID(int=14)))
        assert projected_ack.detail["broker_order_id"] in group.broker_order_ids
    protection_records = [record for record in records
                          if (record.category, record.entity_type)
                          == ("protection", "protection_change")]
    assert protection_records
    for record in protection_records:
        projected_protection = protection_change_batch_v4(
            record, run_month=date(2026, 8, 1),
            attempt_id=str(UUID(int=16)), batch_id=str(UUID(int=17)),
            prior_batch_id=str(UUID(int=0)), source_cursor="2026-08-18:31000")
        assert projected_protection.change["order_group_id"] == group.group_id
    assert frozen and all(item is not None and item.group_id == group.group_id
                          for item in frozen)
    assert any(record.category == "command" and record.entity_type == "order"
               for record in records)
    group_transition = next(record for record in records
                            if record.category == "order_management"
                            and record.entity_type == "order_group_state")
    # The direct OMS path is real, but a flattened transition is insufficient
    # to recover its approved quantity, assignment, and keyed order state.
    # Keep publication gated until a normalized OMS admission revision exists.
    with pytest.raises(ValueError, match="lacks a typed projection"):
        project_journal_record(
            group_transition, run_month=date(2026, 8, 1),
            attempt_id=str(UUID(int=12)), batch_id=str(UUID(int=13)),
            prior_batch_id=str(UUID(int=0)), source_cursor="2026-08-18:31000",
            expected_mode="backtest",
            expected_config={"strategy_id": "strategy-1", "strategy_revision": 1},
        )
