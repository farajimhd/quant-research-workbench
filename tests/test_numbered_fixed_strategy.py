"""Number 2 shares financial rules but owns a separate causal session policy."""
import asyncio
from dataclasses import replace
from datetime import date, datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from src.trading_runtime.numbered_fixed_strategy import numbered_fixed_strategy, is_numbered_fixed_strategy
from src.trading_runtime.numbered_session_exit import numbered_session_exit_intent
from src.trading_runtime.runtime import TradingRuntime, RunMode
from src.backend.backtest_strategy_one_scheduler import StrategyOneBoundaryScheduler
from src.backend.backtest_strategy_one_financial import read_strategy_one_financial_views
from src.backend.backtest_market_data import market_day_boundary


@pytest.mark.parametrize("boundary,entry,cancel,exit_due", [
    (19_499_900, True, False, False), (19_500_000, False, True, False),
    (19_739_900, False, True, False), (19_740_000, False, True, True),
    (19_800_000, False, True, True), (43_200_000, False, False, False),
    (43_200_100, True, False, False), (56_999_900, True, False, False),
    (57_000_000, False, True, False), (57_300_000, False, True, True),
])
def test_separate_session_policy(boundary, entry, cancel, exit_due):
    contract = numbered_fixed_strategy(2)
    assert contract.entry_allowed(boundary) is entry
    assert contract.acquisition_cutoff(boundary) is cancel
    assert contract.liquidation_due(boundary) is exit_due
    baseline = numbered_fixed_strategy(1)
    assert baseline.entry_allowed(boundary)
    assert not baseline.acquisition_cutoff(boundary)
    assert not baseline.liquidation_due(boundary)


@pytest.mark.parametrize("number", [True, 0, 6, 2.0, "2"])
def test_registry_rejects_uninstalled_or_ambiguous_numbers(number):
    assert not is_numbered_fixed_strategy("early-squeeze-strategy", number)
    with pytest.raises(ValueError):
        numbered_fixed_strategy(number)


def test_policy_clock_has_no_market_or_synthetic_liquidity():
    scheduler = StrategyOneBoundaryScheduler(
        session_date="2026-08-18", candidate_rows=iter(()),
        active_source=lambda *_: pytest.fail("Policy clock read fake market"),
        start_after_boundary_ms=43_200_000)
    scheduler.install_session_clocks((57_000_000, 57_300_000, 57_600_000))
    for boundary in (57_000_000, 57_300_000, 57_600_000):
        work = scheduler.pop_next()
        assert work.boundary_ms == boundary
        assert work.broker_rows == work.candidate_rows == work.activation_rows == ()
    assert scheduler.pop_next() is None
    scheduler.close()


def test_liquidation_is_typed_scalar_exit_and_requires_fresh_source():
    runtime = object.__new__(TradingRuntime)
    runtime.config = SimpleNamespace(strategy_id="early-squeeze-strategy", strategy_revision=2,
                                     mode=RunMode.BACKTEST, anchor_date=date(2026, 8, 18))
    runtime._execute_intents = AsyncMock(return_value=[])
    financial = SimpleNamespace(account_id="DU1", assignment_id="A1", ticker="AAA",
                                position_quantity=7., pending_entry=False, pending_exit=False)
    boundary = 57_300_000
    at = market_day_boundary(date(2026, 8, 18), boundary)
    row = dict(ticker="AAA", boundary_ms=boundary, quote_valid=1, bid_int=100_000,
               ask_int=100_100, quote_timestamp_us=int(at.timestamp() * 1_000_000))
    asyncio.run(runtime.submit_numbered_session_exit(financial, {100: row}, boundary))
    args = runtime._execute_intents.call_args
    intent = args.args[0].intents[0]
    assert intent.action == "exit" and intent.quantity == 7.
    assert intent.metadata == {} and intent.reference_price == 10.
    assert args.kwargs == {"numbered_exit_assignment_id": "A1"}
    runtime._execute_intents.reset_mock()
    financial.pending_exit = True
    asyncio.run(runtime.submit_numbered_session_exit(financial, {100: row}, boundary))
    financial.pending_exit = False
    row["quote_timestamp_us"] -= 1_000_001
    asyncio.run(runtime.submit_numbered_session_exit(financial, {100: row}, boundary))
    runtime._execute_intents.assert_not_called()


def test_liquidation_identity_preserves_assignment_and_attempt_boundary():
    args = dict(session_date=date(2026, 8, 18), account_id="DU1", assignment_id="A1",
                ticker="AAA", boundary_ms=57_300_000, quantity=3., bid=10.)
    first = numbered_session_exit_intent(**args)
    assert first == numbered_session_exit_intent(**args)
    assert first.intent_id != numbered_session_exit_intent(**{**args, "assignment_id": "A2"}).intent_id
    assert first.intent_id != numbered_session_exit_intent(**{**args, "boundary_ms": 57_300_100}).intent_id


def test_cutoff_scan_occurs_once_and_is_safe_to_repeat_after_recovery():
    runtime = object.__new__(TradingRuntime)
    runtime.config = SimpleNamespace(strategy_id="early-squeeze-strategy", strategy_revision=2,
                                     mode=RunMode.BACKTEST, anchor_date=date(2026, 8, 18))
    runtime.order_manager = SimpleNamespace(cancel_numbered_session_acquisitions=AsyncMock())
    async def exercise():
        for boundary in range(19_500_000, 19_501_000, 100):
            await runtime.advance_numbered_session_clock(boundary)
        assert runtime.order_manager.cancel_numbered_session_acquisitions.await_count == 1
        del runtime._numbered_completed_cutoffs
        await runtime.advance_numbered_session_clock(19_501_000)
        assert runtime.order_manager.cancel_numbered_session_acquisitions.await_count == 2
    asyncio.run(exercise())


def test_numbered_projection_proof_includes_session_lane():
    from src.backend.backtest_fixed_v4_certification import certify_numbered_fixed_v4_projection
    proofs = [certify_numbered_fixed_v4_projection(number) for number in (1, 2, 3, 4, 5)]
    assert all(len(proof) == 64 for proof in proofs) and len(set(proofs)) == 5


@pytest.mark.parametrize("number", [4, 5])
def test_no_add_contract_blocks_submission_before_journal_and_portfolio(number):
    from src.backend.backtest_journal_memory import BacktestMemoryJournal
    from src.trading_runtime.strategy_one_add import StrategyOneAddProposal
    from src.trading_runtime.signals import StrategyIntent, StrategyEvaluation
    runtime = object.__new__(TradingRuntime)
    runtime.config = SimpleNamespace(strategy_id="early-squeeze-strategy", strategy_revision=number,
        mode=RunMode.BACKTEST, account_ids=("DU1",), anchor_date=date(2026, 8, 18))
    runtime.journal = BacktestMemoryJournal(run_id="test-four")
    proposal = StrategyOneAddProposal("DU1", "A1", "AAA", 31_000, "B1", 10., 10.01, 9.8, 12., 2, number)
    async def run():
        with pytest.raises(ValueError, match="forbids add"):
            await runtime.submit_strategy_one_add(proposal)
        with pytest.raises(ValueError, match="forbids add"):
            await runtime._execute_intents(StrategyEvaluation(intents=(StrategyIntent(
                intent_id="forbidden", ticker="AAA", event_time=market_day_boundary(date(2026, 8, 18), 31_000),
                action="add_long", quantity=1., reference_price=10.),)), "DU1", None)
    asyncio.run(run())
    assert runtime.journal.pending_record_count == 0


def test_pending_semantic_exit_blocks_duplicate_liquidation():
    from src.trading_runtime.strategy_engine import StrategyAssignment, AssignmentStatus, StrategyPermissions
    from src.trading_runtime.order_management import OrderManagementState
    assignment = StrategyAssignment("A1", "early-squeeze-strategy", 2, "DU1", "AAA", 123,
                                    AssignmentStatus.MANAGING, StrategyPermissions(enter=True), {})
    broker = SimpleNamespace(positions=AsyncMock(return_value=[]),
                             position_quantity=lambda *_: 7.)
    oms = SimpleNamespace(snapshots=lambda: [SimpleNamespace(
        group_id="EXIT", account_id="DU1", assignment_id="A1", ticker="AAA",
        action="exit", state=OrderManagementState.WORKING, filled_quantity=0.)])
    view = asyncio.run(read_strategy_one_financial_views((assignment,), broker, oms))[0]
    assert view.pending_exit and view.position_quantity == 7.


def test_flat_fill_cleanup_keeps_actual_liquidation_clock():
    from tests.test_backtest_strategy_one_management import _Runtime, _proposal, _financial
    from src.backend.backtest_strategy_one_management import StrategyOneManagementRunner
    from src.trading_runtime.strategy_one_position import ProtectionState
    runtime = _Runtime()
    runtime.config = SimpleNamespace(strategy_revision=2)
    runtime.submit_numbered_session_exit = AsyncMock()
    evidence = SimpleNamespace(management_evidence=AsyncMock())
    manager = StrategyOneManagementRunner(runtime=runtime, evidence=evidence, tick_for_ticker=lambda _: .01)
    key = ("DU1", "A1", "AAA")
    manager._submitted[key] = replace(_proposal(), strategy_number=2)
    manager._positions[key] = ProtectionState(19_740_000, 9.69, 10.3)
    manager._position_highs[key] = 101_000
    asyncio.run(manager.on_management(_financial(held=0), {}, 19_740_100))
    assert not manager._submitted and not manager._positions
    assert manager.last_closed_position(_financial()).closed_boundary_ms == 19_740_100
    runtime.submit_numbered_session_exit.assert_not_called()


def test_empty_liquidity_tail_cannot_claim_flat_terminal_success():
    import numpy as np
    from tests.test_backtest_strategy_one_execution import _Runtime, _Evidence
    from src.backend.backtest_strategy_one_execution import run_strategy_one_fixed_session
    from src.backend.backtest_strategy_one_management import StrategyOneManagementRunner
    from src.backend.backtest_strategy_one_entry_store import CertifiedEntryEvidencePlan
    from src.backend.backtest_strategy_one_static_gate import StrategyOneStaticGate
    from src.trading_runtime.strategy_engine import StrategyAssignment, AssignmentStatus, StrategyPermissions
    runtime = _Runtime([])
    runtime.config.strategy_revision = 2
    runtime.advance_numbered_session_clock = AsyncMock()
    runtime.submit_numbered_session_exit = AsyncMock()
    evidence = _Evidence([])
    manager = StrategyOneManagementRunner(runtime=runtime, evidence=evidence, tick_for_ticker=lambda _: .01)
    scheduler = StrategyOneBoundaryScheduler(session_date="2026-08-18", candidate_rows=iter(()),
        active_source=lambda *_: iter(()), start_after_boundary_ms=57_300_000)
    scheduler.install_session_clocks((57_600_000,))
    assignment = StrategyAssignment("A1", "early-squeeze-strategy", 2, "DU1", "AAA", 123,
        AssignmentStatus.MANAGING, StrategyPermissions(enter=True), {})
    finished = AsyncMock()
    with pytest.raises(RuntimeError, match="residual exposure/orders"):
        asyncio.run(run_strategy_one_fixed_session(scheduler,
            CertifiedEntryEvidencePlan("b" * 16, "2026-08-18", (), (), (), "e" * 64),
            evidence, manager, runtime=runtime,
            static_gate=StrategyOneStaticGate((), np.array([], dtype=np.uint8), np.array([], dtype=np.int64)),
            assignments=(assignment,), before_boundary=AsyncMock(), finish_boundary=finished))
    finished.assert_awaited_once()
    runtime.submit_numbered_session_exit.assert_not_awaited()
    assert runtime.broker.held
    scheduler.close()


@pytest.mark.parametrize("number", [2, 3, 4, 5])
def test_real_oms_session_exit_cancels_protection_and_fills_only_later_liquidity(number):
    from uuid import UUID
    from src.backend.backtest_journal_memory import BacktestMemoryJournal
    from src.trading_runtime.domain import TradingMode, InstrumentContract
    from src.trading_runtime.execution_policies import ExecutionMarketSnapshot
    from src.trading_runtime.ibkr_schema import AccountSummary, AccountLedger
    from src.trading_runtime.order_management import OrderManagementEngine, BrokerCommunicationPolicy
    from src.trading_runtime.portfolio import PortfolioManagementEngine, PortfolioAccountProfile, PortfolioPolicy
    from src.trading_runtime.risk import RiskAuthority
    from src.trading_runtime.simulated_broker import SimulatedBrokerAdapter
    from src.trading_runtime.strategy_orders import RuntimeIbkrStrategyOrderPlanner
    from src.trading_runtime.strategy_one_intent import strategy_one_entry_intent
    from src.trading_runtime.strategy_one_stateful import StrategyOneEntryProposal

    async def exercise():
        at = market_day_boundary(date(2026, 8, 18), 19_499_800)
        run_id = str(UUID(int=222))
        journal = BacktestMemoryJournal(run_id=run_id)
        broker = SimulatedBrokerAdapter(["DU1"], mode=TradingMode.BACKTEST,
                                        initial_time=at, fixed_bar_mode=True)
        await broker.initialize()
        risk = RiskAuthority()
        await risk.prime(broker, ["DU1"])
        policy = PortfolioPolicy(maximum_position_fraction=1., maximum_ticker_fraction=1.,
                                 maximum_planned_risk_fraction=.5, maximum_open_risk_fraction=.5,
                                 allow_outside_rth=True)
        portfolio = PortfolioManagementEngine(
            [PortfolioAccountProfile("cash", "DU1", "backtest", "simulated", policy)],
            journal=journal, run_id=run_id, strategy_id="early-squeeze-strategy",
            strategy_revision=number, event_clock=lambda: at)
        portfolio.synchronize_snapshot("DU1", summary=AccountSummary(
            account_id="DU1", netliquidation=9000, totalcashvalue=9000, buyingpower=9000,
            grosspositionvalue=0, availablefunds=9000, excessliquidity=9000, timestamp=at),
            ledger=AccountLedger(acctId="DU1", cashbalance=9000, settledcash=9000,
                stockmarketvalue=0, netliquidationvalue=9000, realizedpnl=0, unrealizedpnl=0,
                timestamp=at), positions=[])
        planner = RuntimeIbkrStrategyOrderPlanner(
            {"AAA": InstrumentContract("AAA", 123, "AAA", "STK", "USD")},
            strategy_id="early-squeeze-strategy", strategy_revision=number, run_id=run_id)
        manager = OrderManagementEngine(broker=broker,
            planner=lambda item, account_id, event: planner.plan(account_id=account_id, intent=item, event=event),
            risk=risk, journal=journal, run_id=run_id, strategy_id="early-squeeze-strategy",
            strategy_revision=number, policy=BrokerCommunicationPolicy(), causal_execution_clock=True)
        def bar(boundary, volume):
            at_bar = market_day_boundary(date(2026, 8, 18), boundary)
            last_us = int(at_bar.timestamp() * 1_000_000) - 1
            return dict(ticker="AAA", resolution_ms=100, bucket_index=144_000 + boundary // 100 - 1,
                event_count=3, last_event_us=last_us, quote_valid=1, quote_timestamp_us=last_us - 10,
                bid_int=100_000, ask_int=100_100, bid_size=100, ask_size=100,
                price_valid=1, extremes_valid=1, close_int=100_100, low_int=99_900,
                high_int=100_200, execution_volume=volume), at_bar
        try:
            manager.on_market_snapshot(ExecutionMarketSnapshot("AAA", 10., 10.01, .01, at, "qmd-history"))
            proposal = StrategyOneEntryProposal("A1", "DU1", "AAA", 19_499_800, 19_499_000,
                                                10.01, 9.89, 12., "R4", .5, 19_499_000, "S1", number)
            intent = strategy_one_entry_intent(proposal, session_date=date(2026, 8, 18))
            journal.append_strategy_one_intent(intent=intent, proposal=proposal, session_date=date(2026, 8, 18),
                account_id="DU1", strategy_id="early-squeeze-strategy", strategy_revision=number)
            _, approved = await portfolio.approve(intent, account_id="DU1", assignment_id="A1")
            assert approved is not None
            await manager.submit_intent(approved, account_id="DU1", event=None)
            row, at = bar(19_500_000, 40)
            assert await broker.on_liquidity_bar(row, at=at)
            await manager.reconcile()
            held = broker.position_quantity("DU1", 123, "AAA")
            assert held > 0
            await manager.cancel_numbered_session_acquisitions(at=at)
            for snapshot in manager.snapshots():
                portfolio.on_order_group_update(snapshot)
            assert not any(not group.entry_submission_closed for group in manager.snapshots()
                           if group.action in {"enter_long", "add_long"})
            row, at = bar(19_740_000, 0)
            await broker.on_liquidity_bar(row, at=at)
            manager.on_market_snapshot(ExecutionMarketSnapshot("AAA", 10., 10.01, .01, at, "qmd-history"))
            portfolio.synchronize_snapshot("DU1", summary=await broker.account_summary("DU1"),
                ledger=await broker.account_ledger("DU1"), positions=await broker.positions("DU1"))
            exit_intent = numbered_session_exit_intent(session_date=date(2026, 8, 18),
                account_id="DU1", assignment_id="A1", ticker="AAA", boundary_ms=19_740_000,
                quantity=held, bid=10., strategy_number=number)
            journal.append_numbered_session_exit_intent(intent=exit_intent,
                account_id="DU1", strategy_id="early-squeeze-strategy", strategy_revision=number)
            decision, exit_approved = await portfolio.approve(exit_intent, account_id="DU1", assignment_id="A1")
            assert exit_approved is not None, decision.reasons
            await manager.submit_intent(exit_approved, account_id="DU1", event=None)
            assert broker.position_quantity("DU1", 123, "AAA") == held
            live = await broker.live_orders()
            assert not any(order.orderType in {"STP", "STOP_LIMIT"} and order.remainingQuantity > 0
                           and str(order.order_status) in {"Submitted", "PreSubmitted"} for order in live)
            row, at = bar(19_740_100, 4)
            row["bid_size"] = 4
            assert await broker.on_liquidity_bar(row, at=at)
            await manager.reconcile()
            remaining = broker.position_quantity("DU1", 123, "AAA")
            assert 0 < remaining < held
            from src.backend.backtest_typed_projection import project_pending_backtest_v4_prefix
            units = project_pending_backtest_v4_prefix(
                journal, attempt_id=str(UUID(int=223)), run_month=date(2026, 8, 1),
                prior_sequence=0, prior_batch_id=str(UUID(int=0)), source_cursor="2026-08-18:19740100",
                expected_config={"strategy_id": "early-squeeze-strategy", "strategy_revision": number},
                through_sequence=journal.records(run_id)[-1].sequence)
            scalar_exits = [intent for unit in units
                for intent in (unit.base if hasattr(unit, "base") else unit).intents
                if intent["action"] == "exit"]
            assert len(scalar_exits) == 1 and scalar_exits[0]["reason"] == ("strategy_two_session_exit" if number == 2 else "strategy_three_session_exit" if number == 3 else "strategy_four_session_exit" if number == 4 else "strategy_five_session_exit")
            from tests.test_arte_journal_commit_v4 import attached_v4_client
            from src.trading_runtime.arte_journal_commit_v4 import _publish_typed_batch_v4, load_verified_v4_prefix
            from src.trading_runtime.arte_journal_compound_v4 import _publication_kwargs
            from src.trading_runtime.arte_oms_projection import load_recovered_strategy_one_oms_lineage
            from src.trading_runtime.arte_command_recovery import load_committed_strategy_one_command_page
            client = attached_v4_client()
            for unit in units:
                _publish_typed_batch_v4(client, unit.base if hasattr(unit, "base") else unit,
                                        **_publication_kwargs(unit))
            prefix = load_verified_v4_prefix(client, run_id)
            recovered = load_recovered_strategy_one_oms_lineage(
                client, prefix, allowed_accounts=frozenset({"DU1"}), strategy_number=number)
            exits = [item for item in recovered if item.source_intent.intent.action == "exit"]
            assert len(exits) == 1
            assert float(exits[0].state.group["remaining_quantity"]) == remaining
            assert all(order.raw["canonical_strategy_revision"] == number for order in exits[0].orders)
            commands = load_committed_strategy_one_command_page(client, prefix)
            assert commands and all(item.request.raw["canonical_strategy_revision"] == number for item in commands)
            row, at = bar(19_740_200, 0)
            row["bid_size"] = 0
            assert not await broker.on_liquidity_bar(row, at=at)
            assert broker.position_quantity("DU1", 123, "AAA") == remaining
            row, at = bar(19_740_300, 100_000)
            assert await broker.on_liquidity_bar(row, at=at)
            await manager.reconcile()
            assert broker.position_quantity("DU1", 123, "AAA") == 0
        finally:
            await manager.close()
            journal.close()
    asyncio.run(exercise())


@pytest.mark.parametrize("number", [2, 3, 4, 5])
def test_residual_failure_completes_real_controller_cursor_and_terminal_journal(monkeypatch, number):
    import numpy as np
    from src.backend import backtest_strategy_one_execution as execution
    from src.backend.replay_run_service import ReplayRunController
    from src.backend.backtest_journal_memory import BacktestMemoryJournal
    from src.backend.backtest_market_data import CertifiedMarketDayPlan, ExecutionInterval
    from src.backend.backtest_strategy_one_management import StrategyOneManagementRunner
    from src.backend.backtest_strategy_one_entry_store import CertifiedEntryEvidencePlan
    from src.backend.backtest_strategy_one_static_gate import StrategyOneStaticGate
    from src.trading_runtime.strategy_one_runtime import AssignedStrategyOne
    from src.trading_runtime.strategy_engine import StrategyAssignment, AssignmentStatus, StrategyPermissions
    from src.trading_runtime.arte_journal_projection import backtest_cursor_record_fields
    from tests.test_backtest_strategy_one_execution import _Runtime, _Evidence

    day = "2026-08-18"
    controller = object.__new__(ReplayRunController)
    controller.run_id = "00000000-0000-0000-0000-000000000333"
    controller.definition = SimpleNamespace(execution_interval="100ms", session_date=day,
        requested_start=market_day_boundary(day, 43_200_000), session_end=market_day_boundary(day, 57_600_000))
    controller._journal = BacktestMemoryJournal(run_id=controller.run_id)
    assignment = StrategyAssignment("A1", "early-squeeze-strategy", number, "DU1", "AAA", 123,
        AssignmentStatus.MANAGING, StrategyPermissions(enter=True), {"execution": {"tick_size": .01}})
    controller._strategy = AssignedStrategyOne([assignment])
    runtime = _Runtime([])
    runtime.config.strategy_revision = number
    runtime.advance_numbered_session_clock = AsyncMock()
    runtime.submit_numbered_session_exit = AsyncMock()
    controller._runtime = runtime
    controller._resume_state = None
    controller._stop_requested = False
    controller.processed_events = 0
    controller._source_cursor = {}
    controller._frame_cursor = {}
    controller._data_authority = {}
    controller._record_data_authority = lambda *_: None
    controller._fixed_through_boundary_ms = lambda: 57_600_000
    controller._publish = AsyncMock()
    controller._after_event = AsyncMock()
    controller._wait_until_active = AsyncMock()
    controller._runtime_finished = False
    controller._account_map = {"primary": "DU1"}
    controller._record_stage_time = lambda *_: None
    published = []
    publisher = SimpleNamespace(writer=SimpleNamespace(journal_profile="backtest_v4"), _source_cursor=None)
    controller._journal_publisher = publisher
    async def checkpoint(at, **kwargs):
        assert kwargs == {"checkpoint_status": "running"}
        publisher._source_cursor, _ = backtest_cursor_record_fields(
            controller._source_cursor, controller._frame_cursor, completed_at=at)
    controller._save_restart_checkpoint_responsive = checkpoint
    async def runtime_finish(*, status):
        controller._journal.append(run_id=controller.run_id, category="lifecycle", entity_type="run",
            entity_id=controller.run_id, payload={"status": status}, event_time=controller.current_time)
    runtime.finish = runtime_finish
    runtime.portfolio = SimpleNamespace(capture_recovery_snapshot=lambda *args, **kwargs: kwargs)
    def enqueue_terminal(captures):
        async def publish():
            published.extend(controller._journal.unfenced_records())
            assert captures[0]["snapshot_at"] == controller.definition.session_end
        return asyncio.create_task(publish())
    publisher.enqueue_terminal = enqueue_terminal
    async def execute(**kwargs):
        evidence = _Evidence([])
        manager = StrategyOneManagementRunner(runtime=runtime, evidence=evidence, tick_for_ticker=lambda _: .01)
        kwargs["manager_ready"](manager)
        scheduler = StrategyOneBoundaryScheduler(session_date=day, candidate_rows=iter(()),
            active_source=lambda *_: iter(()), start_after_boundary_ms=57_300_000)
        scheduler.install_session_clocks((57_600_000,))
        try:
            await execution.run_strategy_one_fixed_session(scheduler,
                CertifiedEntryEvidencePlan("b" * 16, day, (), (), (), "e" * 64), evidence, manager,
                runtime=runtime, static_gate=StrategyOneStaticGate((), np.array([], dtype=np.uint8), np.array([], dtype=np.int64)),
                assignments=(assignment,), before_boundary=kwargs["before_boundary"], finish_boundary=kwargs["finish_boundary"])
        finally:
            scheduler.close()
    monkeypatch.setattr(execution, "run_certified_strategy_one_session", execute)
    market = CertifiedMarketDayPlan(ExecutionInterval.fixed(100), "build-1", "a" * 64,
        (day,), ("AAA",), (), (100, 1000, 30000), "b" * 64)
    async def run():
        with pytest.raises(RuntimeError, match="residual exposure/orders"):
            await controller._run_strategy_one_fixed_days(market=market, execution_market=market,
                candidates=object(), activations=object(), pivots=object(), hod=object(), seeds=object(),
                v7_intervals=object(), entry=object(), prices=object())
        assert controller._source_cursor["boundary_ms"] == 57_600_000
        assert controller.current_time == controller.definition.session_end
        await controller._finish_fixed_v4("failed")
    asyncio.run(run())
    assert controller._runtime_finished
    assert published[-1].payload["status"] == "failed"
    assert runtime.broker.held  # Failure retains exposure; no synthetic flattening.
