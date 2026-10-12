"""Main app controller constructs the shared program/Portfolio/OMS runtime."""
import asyncio
from dataclasses import replace
from datetime import date, time, datetime, UTC, timedelta

import pytest

from src.backend.backtest_journal_memory import BacktestMemoryJournal
from src.backend.backtest_v4_run_context import historical_simulated_account_ids
from src.backend.replay_run_service import ReplayRunController, ReplayRunDefinition, _backtest_launch_blocker
from src.trading_runtime.columnar_program_actor import AssignedProgramStrategy
from src.trading_runtime.runtime import RunMode
from src.trading_runtime.execution_policies import ExecutionPolicy, ExecutionEnvelope, ProtectionProfile, ProtectionSlice, StopRule
from src.trading_runtime.strategy_engine import StrategyAssignment, StrategyPermissions, AssignmentStatus
from tests.test_columnar_program_coordinator import inputs
from tests.test_replay_run_service import approved_configuration
from tests.test_broker_liquidity_resolution import second_bar


def definition_and_actor():
    release, _, _, _ = inputs(entry=True)
    configuration = approved_configuration()
    configuration["payload"]["strategy"] = dict(strategy_id=release.header["strategy_id"],
        revision=release.header["strategy_number"], parameters={})
    account, = historical_simulated_account_ids(mode=RunMode.BACKTEST,
        configuration=configuration["payload"])
    assignment = StrategyAssignment("assignment", release.header["strategy_id"],
        release.header["strategy_number"], account, "ABC", 1,
        AssignmentStatus.WATCHING, StrategyPermissions(enter=True, add=True), {})
    actor = AssignedProgramStrategy(release, (assignment,), {"assignment": "listing"})
    definition = ReplayRunDefinition(session_date=date(2026, 10, 12), start_time=time(4),
        mode=RunMode.BACKTEST, configuration_revision=configuration, execution_interval="100ms",
        market_data_plan=dict(token="synthetic-plan", execution_interval=dict(milliseconds=100)))
    return definition, actor


@pytest.mark.parametrize("resolution", [100, 1000])
def test_controller_builds_program_actor_on_existing_runtime_without_legacy_factory(tmp_path, resolution, monkeypatch):
    definition, actor = definition_and_actor()
    controller = ReplayRunController(definition, runtime_root=tmp_path, program_strategy=actor,
        program_liquidity_resolution_ms=resolution)
    controller._journal = BacktestMemoryJournal(run_id=controller.run_id)
    async def run():
        await controller._initialize_runtime(record_configuration=False, record_lifecycle=False)
        try:
            assert controller._strategy is actor
            assert controller._runtime.strategy is actor
            assert controller._strategy_registration is None
            assert controller._runtime.order_manager is not None
            assert controller._runtime.portfolio is not None
            assert controller._runtime.broker.config.commission_per_share == .005
            assert controller._runtime.broker.config.minimum_commission == 1.
            assert controller._runtime.broker.config.market_slippage_bps == 5.
            assert controller._runtime.broker.config.liquidity_resolution_ms == resolution
            assert actor.assignments()[0].account_id in controller.account_ids
            assert not actor.execution_state.positions
            # Construction does not admit an unqualified operational run.
            assert _backtest_launch_blocker(definition)
            at = datetime(2026, 10, 12, 14, tzinfo=UTC)
            _, boundary, _, management = inputs(entry=True)
            boundary = replace(boundary, boundary_at=at, available_at=at)
            management = replace(management, observed_at=at)
            row = second_bar(at)
            row.update(ticker="ABC", ask_int=105000, bid_int=104900, close_int=105000,
                high_int=105100, low_int=104800)
            if resolution == 100:
                row.update(resolution_ms=100, bucket_index=(row["bucket_index"] + 1) * 10 - 1)
            runtime = controller._runtime
            await runtime.process_liquidity_boundary((row,), at=at)
            proposal, = actor.proposals(boundary, assignment_id="assignment", management=management)
            result = await runtime.submit_program_proposal(proposal,
                account_id=actor.assignments()[0].account_id, assignment_id="assignment",
                execution_policy=ExecutionPolicy(envelope=ExecutionEnvelope(deadline_ms=2000)),
                protection_profile=ProtectionProfile("test", 1,
                    (ProtectionSlice("one", 1., StopRule(price=9.)),)), outside_rth=False)
            assert result[0]["decision"]["status"] in {"approved", "resized"}
            # Cold normalized admission must reproduce the actual Portfolio
            # approved intent, including any quantity resize and attribution.
            from dataclasses import asdict
            from src.trading_runtime.program_oms_admission import restore_program_admitted_intent
            from src.trading_runtime.arte_intent_projection import RecoveredIntent
            group = next(iter(runtime.order_manager._groups.values()))
            approved = group.intent
            source_intent = proposal.to_intent(
                execution_policy=approved.execution_policy,
                protection_profile=approved.protection_profile, outside_rth=approved.outside_rth)
            reservation = {**asdict(runtime.portfolio.reservations[
                approved.metadata["portfolio_reservation_id"]]), "run_id": controller.run_id}
            decision = {**result[0]["decision"], "run_id": controller.run_id}
            source = RecoveredIntent(1, actor.assignments()[0].account_id,
                "record", "batch", source_intent)
            kwargs = dict(run_id=controller.run_id, account_id=source.account_id,
                assignment_id="assignment", reservation=reservation, decision=decision,
                execution_policy=approved.execution_policy,
                protection_profile=approved.protection_profile, outside_rth=approved.outside_rth)
            restored = restore_program_admitted_intent(actor.program_release, proposal, source, **kwargs)
            assert restored == approved
            from src.trading_runtime.program_oms_lineage import reconstruct_program_oms_lineage
            from src.trading_runtime.arte_oms_projection import RecoveredOmsGroupState
            from src.trading_runtime.arte_journal_reader import CompleteProtectionHistory
            flat_orders = tuple(replace(order, raw={}) for order in group.orders)
            state = RecoveredOmsGroupState(2, source.record_id,
                dict(run_id=controller.run_id, group_id=group.group_id, batch_id="batch",
                    strategy_id=actor.strategy_id, strategy_revision=actor.revision,
                    account_id=source.account_id, strategy_intent_id=source_intent.intent_id),
                flat_orders, tuple(0 for _ in flat_orders), tuple("" for _ in flat_orders),
                tuple(dict(broker_order_id=identity, request_index=index, terminal=False)
                    for identity, index in group.broker_order_request_indexes.items()), (), ())
            history = CompleteProtectionHistory(controller.run_id, 2, ("batch",), ())
            lineage_kwargs = dict(release=actor.program_release, proposal=proposal,
                assignment_id="assignment", reservation=reservation, decision=decision,
                execution_policy=approved.execution_policy,
                protection_profile=approved.protection_profile, outside_rth=approved.outside_rth)
            lineage = reconstruct_program_oms_lineage(state, source, history, **lineage_kwargs)
            assert lineage.approved_intent == group.intent
            assert lineage.orders == tuple(group.orders)
            # Reader orchestration uses normalized fixture rows; actual source
            # reselection and order reconstruction use the same live flat
            # position/management state that generated this initial proposal.
            from src.backend import program_oms_recovery as cold
            from src.trading_runtime.arte_journal_commit_v4 import V4CommittedPrefix
            from types import SimpleNamespace
            closed = []
            client = SimpleNamespace(close=lambda: closed.append(True))
            prefix = V4CommittedPrefix(controller.run_id, 2, "batch", "cursor", "running", ("batch",))
            monkeypatch.setattr(cold, "load_verified_v4_prefix", lambda *_: prefix)
            monkeypatch.setattr(cold, "load_latest_committed_oms_groups", lambda *a, **k: (state,))
            monkeypatch.setattr(cold, "load_complete_typed_protection_history", lambda *a, **k: history)
            monkeypatch.setattr(cold, "load_committed_strategy_intent_page", lambda *a, **k: (source,))
            monkeypatch.setattr(cold, "load_committed_oms_admission_page", lambda *a, **k: {2: reservation})
            monkeypatch.setattr(cold, "load_committed_oms_decision_page", lambda *a, **k: {2: decision})
            def reselect(*facts):
                assert facts == (state, source, reservation, history)
                selected, = actor.proposals(boundary, assignment_id="assignment", management=management)
                return selected
            recovered_lineages = await cold.recover_program_oms_lineages_async(lambda: client, prefix,
                release=actor.program_release, account_ids=(source.account_id,), reselect=reselect,
                execution_policy=approved.execution_policy,
                protection_for=lambda _: approved.protection_profile, outside_rth=approved.outside_rth)
            assert recovered_lineages == (lineage,)
            assert closed == [True]
            from src.backend import program_broker_recovery as broker_cold
            from src.backend.backtest_market_data import market_day_boundary
            from src.backend.backtest_v4_broker_quote_restore import CompletedBrokerQuote
            from src.trading_runtime.strategy_one_broker_match_snapshot import project_broker_match_snapshot
            native_state = runtime.broker.checkpoint_state()
            boundary_ms = int((at - market_day_boundary(at.date(), 0)).total_seconds() * 1000)
            broker_rows = project_broker_match_snapshot(run_id=controller.run_id, session_date=at.date(),
                checkpoint_sequence=2, boundary_ms=boundary_ms, state=native_state)
            quote = native_state["quotes_by_ticker"]["ABC"]
            pinned_quotes = {"ABC": CompletedBrokerQuote("ABC", boundary_ms,
                quote["raw"]["quote_timestamp_us"], quote["bid_price"], quote["ask_price"],
                quote["bid_size"], quote["ask_size"])}
            monkeypatch.setattr(broker_cold, "load_verified_v4_prefix", lambda *_: prefix)
            monkeypatch.setattr(broker_cold, "load_v4_broker_executions", lambda *a, **k: [])
            broker_image = await broker_cold.recover_program_broker_async(lambda: client, prefix,
                broker=broker_rows, quotes=pinned_quotes, lineages=recovered_lineages,
                simulation_config=runtime.broker.config)
            from src.trading_runtime.simulated_broker import SimulatedBrokerAdapter
            restored_broker = SimulatedBrokerAdapter(list(runtime.config.account_ids),
                config=runtime.broker.config, mode=runtime.broker.mode,
                initial_time=runtime.broker.initial_time, fixed_bar_mode=True)
            restored_broker.restore_checkpoint_state(broker_image)
            actual_broker_image = restored_broker.checkpoint_state()
            assert actual_broker_image["orders"] == native_state["orders"]
            assert actual_broker_image["cash"] == native_state["cash"]
            assert actual_broker_image["quotes_by_ticker"]["ABC"]["source"] == runtime.broker.config.liquidity_source
            assert closed == [True, True]
            monkeypatch.setattr(cold, "load_committed_strategy_intent_page", lambda *a, **k: ())
            with pytest.raises(ValueError, match="source join is incomplete"):
                await cold.recover_program_oms_lineages_async(lambda: client, prefix,
                    release=actor.program_release, account_ids=(source.account_id,), reselect=reselect,
                    execution_policy=approved.execution_policy,
                    protection_for=lambda _: approved.protection_profile, outside_rth=approved.outside_rth)
            assert closed == [True, True, True]
            with pytest.raises(ValueError, match="scope or flat"):
                reconstruct_program_oms_lineage(replace(state, orders=tuple(group.orders)),
                    source, history, **lineage_kwargs)
            with pytest.raises(ValueError, match="source or normalized"):
                restore_program_admitted_intent(actor.program_release, proposal,
                    replace(source, intent=replace(source_intent, reference_price=999.)), **kwargs)
            with pytest.raises(ValueError, match="quantity differs"):
                restore_program_admitted_intent(actor.program_release, proposal, source,
                    **{**kwargs, "decision": {**decision, "approved_quantity": approved.quantity + 1}})
            assert not actor.position_for("assignment").position_open
            observed = controller.program_order_observation(assignment_id="assignment", at=at)
            assert observed.pending_acquisition and not observed.pending_reduction
            # Submission ACK alone has no processed broker-order fingerprint.
            assert observed.unobserved_order_ids and not observed.working_orders
            with pytest.raises(ValueError, match="completed app runtime"):
                controller.program_order_observation(assignment_id="assignment", at=at + timedelta(seconds=1))
            at += timedelta(seconds=1)
            row.update(last_event_us=int(at.timestamp() * 1_000_000) - 1,
                quote_timestamp_us=int(at.timestamp() * 1_000_000) - 10001,
                bucket_index=row["bucket_index"] + 1000 // resolution)
            await runtime.process_liquidity_boundary((row,), at=at)
            position = actor.position_for("assignment")
            assert position.position_open and position.fees > 0
            assert float(position.quantity) == runtime.broker.position_quantity(
                actor.assignments()[0].account_id, 1, "ABC")
            observed = controller.program_order_observation(assignment_id="assignment", at=at)
            assert not observed.pending_acquisition and not observed.pending_reduction
            assert observed.high_water_price > 0
            assert any(order.role == "protective_stop" and order.remaining_quantity == float(position.quantity)
                and order.stop_price == 9. for order in observed.working_orders)
            stop = next(order for order in observed.working_orders if order.role == "protective_stop")
            manager = runtime.order_manager
            group = manager._groups[manager._group_by_broker_id[stop.broker_order_id]]
            request_index = group.broker_order_request_indexes[stop.broker_order_id]
            request = group.orders[request_index]
            group.orders[request_index] = replace(request, auxPrice=123., quantity=1.)
            try:
                assert controller.program_order_observation(assignment_id="assignment", at=at) == observed
            finally:
                group.orders[request_index] = request
            fingerprint = group.broker_order_state_fingerprints[stop.broker_order_id]
            group.broker_order_state_fingerprints[stop.broker_order_id] = fingerprint[:-1]
            try:
                with pytest.raises(ValueError, match="unknown codec"):
                    controller.program_order_observation(assignment_id="assignment", at=at)
            finally:
                group.broker_order_state_fingerprints[stop.broker_order_id] = fingerprint
            updated_at = group.updated_at
            group.updated_at = at + timedelta(seconds=1)
            try:
                with pytest.raises(ValueError, match="future order state"):
                    controller.program_order_observation(assignment_id="assignment", at=at)
            finally:
                group.updated_at = updated_at
        finally:
            await controller._runtime.finish()
    asyncio.run(run())


@pytest.mark.parametrize("change", [dict(resume_state={}), dict(fixed_v4_runtime_image=object())])
def test_program_binding_rejects_other_recovery_authorities_before_runtime(tmp_path, change):
    definition, actor = definition_and_actor()
    with pytest.raises(ValueError, match="fresh actor"):
        ReplayRunController(definition, runtime_root=tmp_path, program_strategy=actor,
            program_liquidity_resolution_ms=100, **change)


def test_program_controller_rejects_untyped_journal_and_account_substitution(tmp_path):
    definition, actor = definition_and_actor()
    controller = ReplayRunController(definition, runtime_root=tmp_path, program_strategy=actor,
        program_liquidity_resolution_ms=100)
    with pytest.raises(RuntimeError, match="normalized Backtest journal"):
        asyncio.run(controller._initialize_runtime(record_configuration=False, record_lifecycle=False))
    assignment = replace(actor.assignments()[0], account_id="other-account")
    other = AssignedProgramStrategy(actor.program_release, (assignment,), {"assignment": "listing"})
    controller = ReplayRunController(definition, runtime_root=tmp_path, program_strategy=other,
        program_liquidity_resolution_ms=100)
    controller._journal = BacktestMemoryJournal(run_id=controller.run_id)
    with pytest.raises(ValueError, match="resolved Backtest accounts"):
        asyncio.run(controller._initialize_runtime(record_configuration=False, record_lifecycle=False))


@pytest.mark.parametrize("resolution", [None, True, 500])
def test_program_liquidity_requires_explicit_supported_selection(tmp_path, resolution):
    definition, actor = definition_and_actor()
    with pytest.raises(ValueError, match="fresh actor"):
        ReplayRunController(definition, runtime_root=tmp_path, program_strategy=actor,
            program_liquidity_resolution_ms=resolution)
