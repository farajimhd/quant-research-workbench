import asyncio
from dataclasses import replace

import pytest

from src.backend.program_runtime_recovery import build_recovered_program_runtime
from src.backend.backtest_journal_memory import BacktestMemoryJournal
from src.trading_runtime import arte_portfolio_recovery as reader
from src.trading_runtime.arte_portfolio_snapshot import publish_portfolio_snapshot
from src.trading_runtime.arte_oms_actor_restore import TypedOmsActorImage
from src.trading_runtime.columnar_program_actor import AssignedProgramStrategy
from src.trading_runtime.domain import TradingMode, InstrumentContract
from src.trading_runtime.portfolio import profiles_for_runtime, PortfolioSyncState
from src.trading_runtime.runtime import RunConfig, RunMode
from src.trading_runtime.simulated_broker import SimulatedBrokerAdapter, SimulationConfig
from src.trading_runtime.strategy_orders import RuntimeIbkrStrategyOrderPlanner
from tests.test_arte_portfolio_snapshot_persistence import SnapshotClient, _state
from tests.test_arte_portfolio_recovery import AT
from tests.test_program_controller_runtime import definition_and_actor


@pytest.mark.parametrize("resolution", [100, 1000])
def test_shared_runtime_initializes_from_real_normalized_cold_portfolio(monkeypatch, resolution, tmp_path):
    definition, template = definition_and_actor()
    assignment = replace(template.assignments()[0], account_id="account-id")
    strategy = AssignedProgramStrategy(template.program_release, (assignment,), {"assignment": "listing"})
    profile = replace(profiles_for_runtime(("account-id",), mode="backtest")[0], account_key="account-key")
    client = SnapshotClient()
    raw = _state()
    raw.update(disabled_strategy_allocations=[], pending_operational_commands=[])
    publish_portfolio_snapshot(client, run_id="cold-run", account_id="account-id",
        state_revision=7, snapshot_at=AT, state=raw)
    monkeypatch.setattr(reader, "load_typed_run_context", lambda *_: {"account_ids": ("account-id",)})
    cold = reader.recover_portfolio_engine_state(client, run_id="cold-run", profiles=(profile,),
        state_revisions={"account-id": 7}, cutoff_at=AT)
    journal = BacktestMemoryJournal(run_id="cold-run", initial_sequence=7)
    config = RunConfig(RunMode.BACKTEST, strategy.strategy_id, strategy.revision,
        ("account-id",), AT.date(), run_id="cold-run", write_progress_checkpoints=False)
    simulation = SimulationConfig(initial_cash=1000., liquidity_resolution_ms=resolution)
    broker = SimulatedBrokerAdapter(["account-id"], simulation, mode=TradingMode.BACKTEST,
        initial_time=AT, fixed_bar_mode=True)
    oms = TypedOmsActorImage("cold-run", strategy.strategy_id, strategy.revision,
        {}, {}, {}, {}, frozenset())
    planner = RuntimeIbkrStrategyOrderPlanner({"ABC": InstrumentContract("listing", 1, "ABC", "STK", "USD")},
        strategy_id=strategy.strategy_id, strategy_revision=strategy.revision, run_id="cold-run")
    kwargs = dict(portfolio_recovery=cold, oms_image=oms, broker_image=broker.checkpoint_state(),
        simulation_config=simulation, completed_at=AT, intent_planner=planner, strategy_configuration={})
    with pytest.raises(ValueError, match="financial scope"):
        build_recovered_program_runtime(replace(config, run_id="foreign"), strategy, journal, **kwargs)
    runtime = build_recovered_program_runtime(config, strategy, journal, **kwargs)
    async def run():
        from src.backend.replay_run_service import ReplayRunController
        controller = ReplayRunController(replace(definition, session_date=AT.date()),
            run_id="cold-run", runtime_root=tmp_path, program_strategy=runtime.strategy,
            program_liquidity_resolution_ms=resolution)
        controller._runtime, controller._journal, controller._planner = runtime, journal, runtime.intent_planner
        before_stage = journal.command_checkpoint()
        candidate = await controller.stage_program_financial_runtime(portfolio_recovery=cold,
            oms_image=oms, broker_image=kwargs["broker_image"], completed_at=AT)
        assert candidate is not runtime and candidate.portfolio is not runtime.portfolio
        assert controller._runtime is runtime
        assert candidate.journal is not journal
        assert journal.command_checkpoint() == before_stage
        assert candidate._canonical_session is None
        assert candidate.strategy is not runtime.strategy
        assert candidate.strategy.assignments()[0] is not runtime.strategy.assignments()[0]
        candidate.strategy.assignments()[0].parameters["staging-only"] = True
        assert "staging-only" not in runtime.strategy.assignments()[0].parameters
        candidate.strategy.execution_state.maximum_executions = 123
        assert runtime.strategy.execution_state.maximum_executions != 123
        candidate.strategy._selection_clocks["staging-only"] = AT
        assert "staging-only" not in runtime.strategy._selection_clocks
        assert candidate.order_manager.planner is not runtime.order_manager.planner
        assert candidate.intent_planner is not runtime.intent_planner
        candidate.strategy.assignments()[0].parameters.pop("staging-only")
        candidate.strategy.execution_state.maximum_executions = runtime.strategy.execution_state.maximum_executions
        candidate.strategy._selection_clocks.pop("staging-only")
        try:
            await runtime.initialize(record_lifecycle=False)
            state = runtime.portfolio.states["account-id"]
            assert state.sync_state is PortfolioSyncState.SYNCHRONIZED
            assert state.ledger.cashbalance == 1000.
            assert state.snapshot_id == "broker-1"
            assert runtime.order_manager is not None and runtime._typed_oms_recovered
            assert not runtime.order_manager._groups
            assert cold.states["account-id"].ledger is None
            assert cold.states["account-id"].sync_state is PortfolioSyncState.ENTRIES_BLOCKED
            from src.backend.program_financial_recovery import qualify_program_financial_runtime
            from src.backend.backtest_market_data import market_day_boundary
            from src.trading_runtime.arte_journal_commit_v4 import V4CommittedPrefix
            from src.trading_runtime.strategy_one_broker_match_snapshot import project_broker_match_snapshot
            boundary_ms = int((AT - market_day_boundary(AT.date(), 0)).total_seconds() * 1000)
            broker_rows = project_broker_match_snapshot(run_id="cold-run", session_date=AT.date(),
                checkpoint_sequence=7, boundary_ms=boundary_ms, state=kwargs["broker_image"])
            prefix = V4CommittedPrefix("cold-run", 7, "batch", "cursor", "running", ("batch",))
            comparison = qualify_program_financial_runtime(runtime, cold, prefix, broker_rows, oms_image=oms)
            assert comparison.states["account-id"].ledger == state.ledger
            assert comparison.states["account-id"] is not state
            from src.backend import rule_decision_reselection as decision_join
            from src.backend.program_policy_recovery import RecoveredProgramPolicy, _RECOVERY_PROOF, program_policy_fingerprint
            from types import MappingProxyType
            packet = RecoveredProgramPolicy(strategy, 7, 0, 0, MappingProxyType({}), "cold-run",
                ("a" * 64,), program_policy_fingerprint(strategy), _RECOVERY_PROOF)
            from tests.test_rule_decision_basis_projection import values
            from src.trading_runtime.rule_decision_basis_projection import project_rule_decision_basis
            decision_parent, _ = project_rule_decision_basis({**values("wait"), "run_id": "cold-run",
                "basis_sequence": 7, "decision_sequence": 8, "boundary_at": AT,
                "account_id": "account-id"}, [])
            calls = []
            def selected(parent, children, records, *, packet, observation):
                assert parent is decision_parent and children == records == ()
                assert observation.account_id == "account-id" and observation.assignment_id == "assignment"
                assert observation.observed_at == AT and observation.working_orders == ()
                calls.append(packet)
                return "synthetic-selection"
            with monkeypatch.context() as join_patch:
                join_patch.setattr(decision_join, "reselect_recovered_rule_decision", selected)
                qualification = await decision_join.qualify_rule_decision_runtime(decision_parent, (), (),
                    runtime=runtime, cold=cold, prefix=prefix, broker=broker_rows, oms_image=oms,
                    packet=packet)
                assert qualification.selection == "synthetic-selection"
                assert qualification.run_id == "cold-run" and qualification.basis_sequence == 7
                from uuid import uuid4
                from src.trading_runtime.signals import StrategySignal
                from src.trading_runtime.journal_contract import JournalRecord
                from src.trading_runtime.arte_journal_projection import project_journal_record
                from src.trading_runtime.rule_decision_basis_projection import RuleDecisionBasisBatch
                from src.trading_runtime.rule_decision_publication import prepare_qualified_rule_decision_commit
                signal = StrategySignal(decision_parent["decision_id"], "program_boundary_completed", "ABC", AT,
                    "wait", "neutral", 0., 1., "program:wait", working_timeframe="1s",
                    metadata=dict(assignment_id="assignment", reference_price=10., status="completed",
                        reason_code="program:wait", reason_detail="wait",
                        correlation_id=decision_parent["decision_id"], causation_id=decision_parent["source_commit_hash"]))
                completion = JournalRecord(decision_parent["record_id"], "cold-run", 8, AT, AT,
                    "strategy_decision", "signal", decision_parent["decision_id"], "account-id",
                    {**signal.payload(), "strategy_id": strategy.strategy_id, "strategy_revision": strategy.revision,
                        "correlation_id": decision_parent["decision_id"], "causation_id": decision_parent["source_commit_hash"]})
                base = project_journal_record(completion, run_month=AT.date().replace(day=1), attempt_id=str(uuid4()),
                    batch_id=decision_parent["batch_id"], prior_batch_id=str(uuid4()),
                    source_cursor="source")
                unit = RuleDecisionBasisBatch(base, decision_parent, ())
                prepared = prepare_qualified_rule_decision_commit(unit, qualification, committed_at=AT)
                assert prepared.commit["first_sequence"] == 8
                for altered in (replace(qualification, semantic_hash="e" * 64),
                        replace(qualification, _proof=object()), replace(qualification, basis_sequence=8)):
                    with pytest.raises(ValueError, match="matching native qualification"):
                        prepare_qualified_rule_decision_commit(unit, altered, committed_at=AT)
                with pytest.raises(ValueError, match="historical basis"):
                    await decision_join.qualify_rule_decision_runtime({**decision_parent, "basis_sequence": 8}, (), (),
                        runtime=runtime, cold=cold, prefix=prefix, broker=broker_rows, oms_image=oms, packet=packet)
            assert calls == [packet] and runtime.journal.pending_record_count == 0
            later_prefix = replace(prefix, last_sequence=9)
            historical_comparison = qualify_program_financial_runtime(runtime, cold, later_prefix,
                broker_rows, oms_image=oms, through_sequence=7)
            assert historical_comparison == comparison
            assert later_prefix.last_sequence == 9
            with pytest.raises(ValueError, match="exact prefix"):
                qualify_program_financial_runtime(runtime, cold, later_prefix,
                    broker_rows, oms_image=oms, through_sequence=8)
            with pytest.raises(ValueError, match="historical basis"):
                qualify_program_financial_runtime(runtime, cold, later_prefix,
                    broker_rows, oms_image=oms, through_sequence=10)
            await candidate.initialize(record_lifecycle=False)
            staged_comparison = await controller.qualify_program_financial_candidate(candidate,
                cold=cold, prefix=prefix, broker=broker_rows, oms_image=oms)
            assert staged_comparison.states["account-id"].ledger == state.ledger
            assert controller._runtime is runtime
            with pytest.raises(ValueError, match="idle prefix"):
                await controller.qualify_program_financial_candidate(runtime,
                    cold=cold, prefix=prefix, broker=broker_rows, oms_image=oms)
            prior_ledger = state.ledger
            state.ledger = replace(state.ledger, cashbalance=state.ledger.cashbalance + 1)
            try:
                with pytest.raises(ValueError, match="synchronized broker state changed"):
                    qualify_program_financial_runtime(runtime, cold, prefix, broker_rows, oms_image=oms)
            finally:
                state.ledger = prior_ledger
            prior_peak = state.peak_net_liquidation
            state.peak_net_liquidation += 1
            try:
                with pytest.raises(ValueError, match="durable Portfolio image changed"):
                    qualify_program_financial_runtime(runtime, cold, prefix, broker_rows, oms_image=oms)
            finally:
                state.peak_net_liquidation = prior_peak
            prior_cash = runtime.broker._cash["account-id"]
            runtime.broker._cash["account-id"] += 1
            try:
                with pytest.raises(ValueError, match="broker image changed"):
                    qualify_program_financial_runtime(runtime, cold, prefix, broker_rows, oms_image=oms)
            finally:
                runtime.broker._cash["account-id"] = prior_cash
        finally:
            await candidate.finish()
            await runtime.finish()
    asyncio.run(run())
