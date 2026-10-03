"""Independent Strategy 43 controller using the app's native V4 authority."""
import asyncio
from contextlib import closing
from types import SimpleNamespace
from uuid import NAMESPACE_URL, uuid5

from .replay_run_service import ReplayRunController
from .backtest_market_data import readonly_clickhouse_client, project_market_day_plan
from .backtest_strategy_forty_three_configuration import validate_definition_sources
from .backtest_strategy_forty_three_plan import certify_source_plan
from .backtest_strategy_forty_three_source import certify_history
from .backtest_liquidity_price import certify_price_level_plan
from .backtest_strategy_forty_three_execution import run_session
from .backtest_strategy_forty_three_native import StrategyFortyThreeNativePort


def reader_factory():
    return readonly_clickhouse_client(market_stream=True, v3_read_principal=True)


def certify_inputs(configuration, session):
    with closing(reader_factory()) as reader:
        plan = certify_source_plan(session=session.isoformat(),
            build=configuration["market_day_build_id"], reader=reader)
        history = certify_history(market=plan.market, identity=plan.identity, structure=plan.structure,
            candidate_tickers=plan.tickers, snapshot_hash=plan.snapshot_hash,
            signal_query_hash=plan.signal_query_hash, session_end_ms=plan.session_end_ms, reader=reader)
        execution = project_market_day_plan(plan.market, plan.tickers)
        prices = certify_price_level_plan(execution, reader, read_client_factory=reader_factory)
    return plan, history, execution, prices


class StrategyFortyThreeController(ReplayRunController):
    def __init__(self, definition, **kwargs):
        validate_definition_sources(definition)
        if any(kwargs.get(key) is not None for key in (
                "resume_state", "resume_journal_prefix", "fixed_v4_runtime_image")):
            raise ValueError("Strategy 43 public resume is not qualified")
        super().__init__(definition, **kwargs)
        self._forty_three_inputs = None

    async def command(self, command, **kwargs):
        normalized = command.strip().lower()
        if normalized not in {"play", "pause", "stop"}:
            raise ValueError("Strategy 43 comparison supports play, pause and stop at its fixed native clock")
        async with self._condition:
            if self.status in {"completed", "failed", "stopped"}:
                raise ValueError(f"Backtest is already {self.status}")
            if normalized == "stop":
                self._stop_requested = True
            elif normalized == "pause":
                self.status = "paused"
            else:
                self.status = "running"
            self._condition.notify_all()
        await self._publish(force=True)
        return self.stream_snapshot()

    async def _fixed_strategy_one_plans(self):
        # This override supplies only common bootstrap parents, never Strategy 1 inputs.
        if self._forty_three_inputs is None:
            self._preparation_stage = "strategy_43_source_certification"
            inputs = await asyncio.to_thread(certify_inputs,
                self.definition.configuration_revision["payload"], self.definition.session_date)
            plan, history, execution, prices = inputs
            expected = self.definition.market_data_plan
            actual = dict(token=plan.market.token, strategy_forty_three_history_token=history.token,
                strategy_forty_three_identity_token=plan.identity.token,
                strategy_forty_three_structure_token=plan.structure.token,
                strategy_forty_three_price_token=prices.token)
            if any(expected.get(key) != value for key, value in actual.items()):
                raise RuntimeError("Strategy 43 launch source changed after preflight")
            self._forty_three_inputs = inputs
        plan, _, execution, _ = self._forty_three_inputs
        return SimpleNamespace(market=plan.market, execution_market=execution)

    async def _initialize_runtime(self):
        from src.backend.backtest_v4_run_context import historical_runtime_config
        from src.backend.backtest_v4_run_context import historical_strategy_one_portfolio_profiles
        from src.trading_runtime.domain import InstrumentContract, TradingMode
        from src.trading_runtime.portfolio import PortfolioManagementEngine
        from src.trading_runtime.runtime import TradingRuntime
        from src.trading_runtime.simulated_broker import SimulatedBrokerAdapter, SimulationConfig
        from src.trading_runtime.strategy_engine import StrategyAssignment, StrategyPermissions, AssignmentStatus
        from src.trading_runtime.strategy_forty_three_runtime import AssignedStrategyFortyThree
        from src.trading_runtime.strategy_forty_three_orders import StrategyFortyThreeOrderPlanner

        configuration = self.definition.configuration_revision["payload"]
        plan = self._forty_three_inputs[0]
        account_ids = self._fixed_v4_account_ids
        self._account_map = {"strategy-43": account_ids[0]}
        assignments = [StrategyAssignment(str(uuid5(NAMESPACE_URL,
            f"strategy43-assignment:{self.run_id}:{ticker}")), "squeeze-grid-strategy", 43,
            account_ids[0], ticker, plan.identity.conid_for(ticker), AssignmentStatus.WATCHING,
            StrategyPermissions(enter=True), {}) for ticker in plan.tickers]
        self._strategy = AssignedStrategyFortyThree(assignments)
        contracts = {ticker: InstrumentContract(f"conid:{plan.identity.conid_for(ticker)}",
            plan.identity.conid_for(ticker), ticker, "STK", "SMART", "USD") for ticker in plan.tickers}
        self._planner = StrategyFortyThreeOrderPlanner(contracts, run_id=self.run_id)
        broker = SimulatedBrokerAdapter(account_ids,
            SimulationConfig(initial_cash=self.definition.initial_cash), mode=TradingMode.BACKTEST,
            initial_time=self.definition.session_start, fixed_bar_mode=True)
        profiles, groups = historical_strategy_one_portfolio_profiles(configuration)
        portfolio = PortfolioManagementEngine(profiles, groups=groups, journal=self._journal,
            run_id=self.run_id, strategy_id="squeeze-grid-strategy", strategy_revision=43,
            event_clock=lambda: self._runtime.last_event_time)
        self._runtime = TradingRuntime(historical_runtime_config(mode=self.definition.mode,
            configuration=configuration, account_ids=account_ids,
            anchor_date=self.definition.session_date, run_id=self.run_id), broker,
            strategy=self._strategy, journal=self._journal, portfolio=portfolio,
            intent_planner=self._planner)
        self._runtime.last_event_time = self.definition.session_start
        await self._runtime.initialize()
        self._runtime.persist_strategy_assignments(self.definition.session_start, record_events=False)
        self._runtime_inputs_ready = True

    async def _finish_fixed_v4(self, status):
        from src.trading_runtime.arte_journal_projection import backtest_cursor_record_fields
        from src.backend.backtest_market_data import market_day_boundary
        cursor = self._source_cursor or dict(session_date=self.definition.session_date,
                                            boundary_ms=0, sequence=0)
        at = self.current_time or market_day_boundary(self.definition.session_date, 0)
        identity, payload = backtest_cursor_record_fields(cursor, {}, completed_at=at)
        self._journal.append(run_id=self.run_id,
            category="checkpoint", entity_type="market_boundary", entity_id=identity,
            event_time=at, payload=payload)
        self._journal_publisher.enqueue_pending()
        await self._journal_publisher.await_fence()
        await self._runtime.finish(status=status)
        self._runtime_finished = True
        terminal = self._journal.unfenced_records()[-1]
        captures = tuple(self._runtime.portfolio.capture_recovery_snapshot(account_id,
            state_revision=terminal.sequence, snapshot_at=terminal.event_time)
            for account_id in sorted(self.account_ids))
        await asyncio.shield(self._journal_publisher.enqueue_terminal(captures))

    async def _run_engine(self):
        self.status = "preparing"
        try:
            await self._open_fixed_journal()
            await self._initialize_runtime()
            plan, history, execution, prices = self._forty_three_inputs
            from .backtest_fixed_market_authority import fixed_market_authority_payload
            self.current_time = self.definition.session_start
            authority = fixed_market_authority_payload(plan.market, execution)
            self._record_data_authority("fixed_market_data", {
                key: value for key, value in authority.items() if key != "source_key"})
            self.status = "running"
            self._preparation_stage = "strategy_43_native_session"
            port = StrategyFortyThreeNativePort(runtime=self._runtime,
                publisher=self._journal_publisher, source_token=history.token,
                source_fact_id=history.fact_id, session_end_ms=plan.session_end_ms)
            async def before(boundary, at):
                async with self._condition:
                    while self.status == "paused" and not self._stop_requested:
                        await self._condition.wait()
                if self._stop_requested:
                    raise asyncio.CancelledError()
                self.current_time = at
                self.processed_events = boundary // 100
                self._source_cursor = dict(session_date=self.definition.session_date,
                    boundary_ms=boundary, sequence=self.processed_events)
            async def completed(boundary, at, coordinator):
                self._journal_publisher.enqueue_pending()
                # Keep at most one normalized publisher task in flight.
                await self._journal_publisher.await_fence()
                if boundary % 10_000 == 0:
                    await self._publish()
            self.comparison_receipt = await run_session(plan=plan, history=history, prices=prices,
                runtime=self._runtime, port=port, client_factory=reader_factory,
                before_boundary=before, finish_boundary=completed)
            await self._finish_fixed_v4("completed")
            self.status = "completed"
        except asyncio.CancelledError:
            self.status = "stopped"
            if self._runtime is not None and not self._runtime_finished:
                await self._finish_fixed_v4("stopped")
        except Exception as exc:
            self.error = str(exc)
            self.status = "failed"
            if self._runtime is not None and not self._runtime_finished:
                try:
                    await self._finish_fixed_v4("failed")
                except Exception as terminal_error:
                    self.error += f"; terminal publication: {terminal_error}"
        finally:
            if self._runtime is not None and self._runtime.order_manager is not None:
                await self._runtime.order_manager.close()
            await self._close_fixed_journal()
            await self._publish()
