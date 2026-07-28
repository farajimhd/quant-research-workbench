from __future__ import annotations

import asyncio
from dataclasses import asdict, dataclass
from datetime import date, datetime, timezone
from enum import StrEnum
from typing import Protocol
from uuid import uuid4

from src.market_engine.events import MarketEvent, QuoteEvent
from src.trading_runtime.broker import BrokerAdapter
from src.trading_runtime.canonical_session import CanonicalBrokerSession
from src.trading_runtime.domain import BrokerProvider, TradingMode
from src.trading_runtime.journal import TradingJournal
from src.trading_runtime.execution_policies import (
    ExecutionMarketDataProvider,
    ExecutionMarketSnapshot,
)
from src.trading_runtime.order_management import OrderManagementEngine, OrderManagementState
from src.trading_runtime.portfolio import PortfolioManagementEngine
from src.trading_runtime.portfolio_config import configured_portfolio_profiles_for_runtime
from src.trading_runtime.risk import RiskAuthority
from src.trading_runtime.risk_supervisor import ContinuousRiskSupervisor, RiskEvaluation
from src.trading_runtime.signals import (
    MarketSignal,
    StrategyEvaluation,
    StrategyIntent,
    normalize_strategy_evaluation,
)
from src.trading_runtime.strategy_engine import StrategyObservation
from src.trading_runtime.strategy_orders import StrategyOrderPlan


class RunMode(StrEnum):
    LIVE = "live"
    PAPER = "paper"
    REPLAY = "replay"
    BACKTEST = "backtest"
    BACKTEST_DEBUG = "backtest_debug"


class AutomaticStrategy(Protocol):
    strategy_id: str
    revision: int
    automatic: bool

    async def on_event(
        self, event: MarketEvent, account_id: str
    ) -> StrategyEvaluation: ...


class RuntimeIntentPlanner(Protocol):
    def plan(
        self,
        *,
        intent: StrategyIntent,
        account_id: str,
        event: MarketEvent | None,
    ) -> StrategyOrderPlan: ...

    def should_cancel_strategy_protection(self, intent: StrategyIntent) -> bool: ...

    def protective_order_prefix(self) -> str: ...


@dataclass(frozen=True, slots=True)
class RunConfig:
    mode: RunMode
    strategy_id: str
    strategy_revision: int
    account_ids: tuple[str, ...]
    anchor_date: date
    run_id: str = ""

    def resolved_run_id(self) -> str:
        return self.run_id or str(uuid4())


class TradingRuntime:
    """One event/order/portfolio lifecycle for live, paper, replay, and backtest."""

    def __init__(
        self,
        config: RunConfig,
        broker: BrokerAdapter,
        strategy: AutomaticStrategy,
        journal: TradingJournal,
        risk: RiskAuthority | None = None,
        intent_planner: RuntimeIntentPlanner | None = None,
        portfolio: PortfolioManagementEngine | None = None,
    ) -> None:
        if config.strategy_id != strategy.strategy_id or config.strategy_revision != strategy.revision:
            raise ValueError("Run strategy identity does not match loaded strategy revision")
        if config.mode in {RunMode.BACKTEST, RunMode.BACKTEST_DEBUG} and not strategy.automatic:
            raise ValueError("Only automatic strategies can be backtested")
        self.config = config
        self.run_id = config.resolved_run_id()
        self.broker = broker
        self.strategy = strategy
        self.journal = journal
        self.risk = risk or RiskAuthority()
        self.intent_planner = intent_planner
        if portfolio is None:
            profiles, groups = configured_portfolio_profiles_for_runtime(
                config.account_ids,
                mode=config.mode.value,
            )
            portfolio = PortfolioManagementEngine(
                profiles,
                journal=journal,
                run_id=self.run_id,
                strategy_id=config.strategy_id,
                strategy_revision=config.strategy_revision,
                groups=groups,
            )
        self.portfolio = portfolio
        self.execution_market_data = ExecutionMarketDataProvider()
        self.order_manager = (
            OrderManagementEngine(
                broker=broker,
                planner=lambda intent, account_id, event: intent_planner.plan(
                    intent=intent,
                    account_id=account_id,
                    event=event,
                ),
                risk=self.risk,
                journal=journal,
                run_id=self.run_id,
                strategy_id=config.strategy_id,
                strategy_revision=config.strategy_revision,
                shortability_provider=broker if hasattr(broker, "shortability") else None,
                fill_callback=self._on_order_group_fill,
                state_callback=self._on_order_group_state,
                execution_market_data=self.execution_market_data,
                enforce_wall_clock_quote_freshness=(
                    config.mode in {RunMode.LIVE, RunMode.PAPER}
                    and bool(getattr(broker, "requires_fresh_execution_state", False))
                ),
            )
            if intent_planner is not None
            else None
        )
        self.risk_supervisor = ContinuousRiskSupervisor(
            self.portfolio,
            journal=journal,
            run_id=self.run_id,
            emergency_callback=self._on_emergency_risk,
        )
        self.last_event_time: datetime | None = None
        self.processed_events = 0
        self._broker_stream_task: asyncio.Task[None] | None = None
        self._risk_refresh_task: asyncio.Task[None] | None = None
        self._canonical_session: CanonicalBrokerSession | None = None

    async def initialize(self) -> None:
        if hasattr(self.broker, "canonical_accounts"):
            self._canonical_session = CanonicalBrokerSession(
                self.broker,  # type: ignore[arg-type]
                mode=TradingMode(self.config.mode.value),
                provider=(
                    BrokerProvider.SIMULATED
                    if self.config.mode in {RunMode.REPLAY, RunMode.BACKTEST, RunMode.BACKTEST_DEBUG}
                    else BrokerProvider.IBKR_CPAPI
                ),
            )
            await self._canonical_session.bootstrap()
            canonical_snapshot = self._canonical_session.projector.snapshot()
            available = {
                row.account_id
                for row in canonical_snapshot.accounts
                if row.can_view or row.can_trade
            }
            self.portfolio.synchronize_canonical(canonical_snapshot)
        else:
            await self.broker.initialize()
            available = set(await self.broker.accounts())
            await self.portfolio.synchronize(self.broker)
        missing = set(self.config.account_ids) - available
        if missing:
            raise ValueError(f"Broker does not expose configured accounts: {', '.join(sorted(missing))}")
        await self.risk.prime(self.broker, self.config.account_ids)
        for account_id in self.config.account_ids:
            await self.risk_supervisor.evaluate(account_id, reason="runtime_initialize")
        if self.order_manager is not None:
            await self.order_manager.configure_broker_session()
            await self.order_manager.recover()
            if hasattr(self.broker, "stream_broker_messages"):
                self._broker_stream_task = asyncio.create_task(self._consume_broker_stream())
                self._risk_refresh_task = asyncio.create_task(self._refresh_live_risk())
        self.journal.append(
            run_id=self.run_id,
            category="lifecycle",
            entity_type="run",
            entity_id=self.run_id,
            payload={"status": "running", "config": asdict(self.config)},
        )

    async def process_event(self, event: MarketEvent) -> None:
        if self.last_event_time is not None and event.ts < self.last_event_time:
            raise ValueError("Market events must be processed in non-decreasing timestamp order")
        self.last_event_time = event.ts
        self.processed_events += 1
        if isinstance(event, QuoteEvent) and event.bid_price > 0 and event.ask_price >= event.bid_price:
            tick_size = float(event.raw.get("tick_size") or 0.01)
            snapshot = ExecutionMarketSnapshot(
                ticker=event.ticker,
                bid=event.bid_price,
                ask=event.ask_price,
                tick_size=tick_size,
                observed_at=event.ts,
                source=event.source,
                volatility=float(event.raw.get("volatility") or 0),
                upper_price_band=(
                    float(event.raw["upper_price_band"])
                    if event.raw.get("upper_price_band") is not None
                    else None
                ),
                lower_price_band=(
                    float(event.raw["lower_price_band"])
                    if event.raw.get("lower_price_band") is not None
                    else None
                ),
            )
            self.execution_market_data.update(snapshot)
            if self.order_manager is not None:
                self.order_manager.on_market_snapshot(snapshot)
        executions = await self.broker.on_market_event(event)
        for execution in executions:
            self.journal.append(
                run_id=self.run_id, category="execution", entity_type="fill", entity_id=execution.execution_id,
                account_id=execution.account, event_time=execution.trade_time, payload=execution.to_cpapi(),
            )
        if executions and self.order_manager is not None:
            await self.order_manager.reconcile()
        if executions and self._canonical_session is not None:
            await self._canonical_session.reconcile()
            self.portfolio.synchronize_canonical(self._canonical_session.projector.snapshot())
        for account_id in self.config.account_ids:
            evaluation = normalize_strategy_evaluation(
                await self.strategy.on_event(event, account_id)
            )
            self._record_strategy_signals(evaluation, account_id)
            await self._execute_intents(evaluation, account_id, event)
        cursor = f"{event.ts.astimezone(timezone.utc).isoformat()}|{event.sequence}|{event.kind}"
        self.journal.save_checkpoint(self.run_id, cursor, {"processed_events": self.processed_events}, event.ts)

    async def process_market_signal(self, signal: MarketSignal) -> None:
        """Deliver one causal reusable signal without coupling QMD to order routing."""
        handler = getattr(self.strategy, "on_market_signal", None)
        if handler is None:
            return
        for account_id in self.config.account_ids:
            evaluation = normalize_strategy_evaluation(await handler(signal, account_id))
            self._record_strategy_signals(evaluation, account_id)
            await self._execute_intents(evaluation, account_id, None)
        self._persist_strategy_assignments(signal.effective_at)

    async def process_strategy_observation(self, observation: StrategyObservation) -> None:
        """Evaluate one normalized causal observation from the indicator/signal bus."""
        if self.last_event_time is not None and observation.observed_at < self.last_event_time:
            raise ValueError("Strategy observations must not move behind the runtime clock")
        handler = getattr(self.strategy, "on_observation", None)
        if handler is None:
            return
        self.last_event_time = observation.observed_at
        for account_id in self.config.account_ids:
            evaluation = normalize_strategy_evaluation(await handler(observation, account_id))
            self._record_strategy_signals(evaluation, account_id)
            await self._execute_intents(evaluation, account_id, None)
        self._persist_strategy_assignments(observation.observed_at)

    def _record_strategy_signals(
        self, evaluation: StrategyEvaluation, account_id: str
    ) -> None:
        for signal in evaluation.signals:
            self.journal.append(
                run_id=self.run_id,
                category="strategy_decision",
                entity_type="signal",
                entity_id=signal.signal_id,
                account_id=account_id,
                event_time=signal.event_time,
                payload={
                    **signal.payload(),
                    "strategy_id": self.config.strategy_id,
                    "strategy_revision": self.config.strategy_revision,
                },
            )

    async def _execute_intents(
        self,
        evaluation: StrategyEvaluation,
        account_id: str,
        event: MarketEvent | None,
    ) -> None:
        if evaluation.intents and self.intent_planner is None:
            raise ValueError("Strategy emitted semantic intents but the runtime has no intent planner")
        if evaluation.intents and self.order_manager is None:
            raise ValueError("Strategy emitted semantic intents but the runtime has no order manager")
        for intent in evaluation.intents:
            self.journal.append(
                run_id=self.run_id,
                category="strategy",
                entity_type="strategy_intent",
                entity_id=intent.intent_id,
                account_id=account_id,
                event_time=intent.event_time,
                payload={
                    **intent.payload(),
                    "strategy_id": self.config.strategy_id,
                    "strategy_revision": self.config.strategy_revision,
                },
            )
            decision, approved_intent = await self.portfolio.approve(intent, account_id=account_id)
            if approved_intent is None:
                continue
            try:
                await self.order_manager.submit_intent(
                    approved_intent,
                    account_id=account_id,
                    event=event,
                )
            except Exception:
                snapshot = self.order_manager.snapshot_for_intent(approved_intent.intent_id)
                if snapshot is None or snapshot.state != OrderManagementState.OUTCOME_UNKNOWN:
                    self.portfolio.release_intent(
                        approved_intent.intent_id,
                        reason="order_management_submission_failed",
                    )
                raise

    def _persist_strategy_assignments(self, event_time: datetime) -> None:
        assignments = getattr(self.strategy, "assignments", None)
        if assignments is None:
            return
        for assignment in assignments():
            self.journal.save_strategy_assignment(assignment.payload())
            self.journal.append(
                run_id=self.run_id,
                category="strategy",
                entity_type="strategy_assignment_state",
                entity_id=assignment.assignment_id,
                account_id=assignment.account_id,
                event_time=event_time,
                payload={
                    "event": "assignment_state_saved",
                    "assignment_id": assignment.assignment_id,
                    "strategy_id": assignment.strategy_id,
                    "strategy_revision": assignment.strategy_revision,
                    "ticker": assignment.ticker,
                    "status": assignment.status.value,
                    "state": assignment.state,
                },
            )
    async def snapshot_portfolios(self) -> None:
        event_time = self.last_event_time or datetime.now(timezone.utc)
        for account_id in self.config.account_ids:
            summary = await self.broker.account_summary(account_id)
            self.journal.append(
                run_id=self.run_id, category="snapshot", entity_type="portfolio", entity_id=account_id,
                account_id=account_id, event_time=event_time, payload=summary.to_cpapi(),
            )
            for position in await self.broker.positions(account_id):
                self.journal.append(
                    run_id=self.run_id, category="snapshot", entity_type="position", entity_id=str(position.conid),
                    account_id=account_id, event_time=event_time, payload=position.to_cpapi(),
                )

    async def finish(self, status: str = "completed") -> None:
        tasks = [task for task in (self._broker_stream_task, self._risk_refresh_task) if task]
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        if self.order_manager is not None:
            await self.order_manager.close()
        await self.snapshot_portfolios()
        self.journal.append(
            run_id=self.run_id, category="lifecycle", entity_type="run", entity_id=self.run_id,
            event_time=self.last_event_time, payload={"status": status, "processed_events": self.processed_events},
        )

    async def _consume_broker_stream(self) -> None:
        stream_provider = getattr(self.broker, "stream_broker_messages")
        while True:
            try:
                async for message in stream_provider():
                    self.risk_supervisor.set_broker_connected(True)
                    if self.order_manager is not None:
                        await self.order_manager.on_broker_message(message)
                    if self._canonical_session is not None:
                        self._canonical_session.apply_websocket_message(message)
                        self.portfolio.synchronize_canonical(
                            self._canonical_session.projector.snapshot()
                        )
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                self.risk_supervisor.set_broker_connected(False)
                if self.order_manager is not None:
                    self.order_manager.set_connection_state(False, reason=str(exc))
                self.journal.append(
                    run_id=self.run_id,
                    category="broker",
                    entity_type="connection_state",
                    entity_id=self.run_id,
                    payload={"status": "disconnected", "error": str(exc), "entries_frozen": True},
                )
                for account_id in self.config.account_ids:
                    await self.risk_supervisor.evaluate(
                        account_id,
                        reason="broker_stream_disconnected",
                    )
                await asyncio.sleep(1.0)

    async def _refresh_live_risk(self) -> None:
        while True:
            try:
                await asyncio.sleep(5.0)
                await self.risk.prime(self.broker, self.config.account_ids)
                for account_id in self.config.account_ids:
                    await self._consume_operational_commands(account_id)
                if self._canonical_session is not None:
                    await self._canonical_session.reconcile()
                    self.portfolio.synchronize_canonical(
                        self._canonical_session.projector.snapshot()
                    )
                else:
                    await self.portfolio.synchronize(self.broker)
                for account_id in self.config.account_ids:
                    await self.risk_supervisor.evaluate(
                        account_id,
                        reason="periodic_authoritative_refresh",
                    )
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                self.journal.append(
                    run_id=self.run_id,
                    category="risk",
                    entity_type="risk_snapshot",
                    entity_id=self.run_id,
                    payload={"status": "stale", "error": str(exc), "entries_frozen": True},
                )

    async def _consume_operational_commands(self, account_id: str) -> None:
        if self.order_manager is None:
            return
        persisted = self.journal.portfolio_states().get(account_id)
        if not persisted:
            return
        self.portfolio.apply_persisted_operational_state(account_id, persisted)
        commands = list(persisted.get("pending_operational_commands") or ())
        changed = False
        for command in commands:
            if str(command.get("status") or "") != "pending":
                continue
            normalized = str(command.get("command") or "")
            try:
                if normalized == "kill_entries":
                    await self.order_manager.kill_entries(
                        account_id,
                        reason=str(command.get("reason") or "operator"),
                    )
                elif normalized == "emergency_flatten":
                    await self.order_manager.emergency_flatten(
                        account_id,
                        reason=str(command.get("reason") or "operator"),
                    )
                elif normalized == "resume_entries":
                    await self.risk_supervisor.resume(
                        account_id,
                        reason=str(command.get("reason") or "operator"),
                    )
                else:
                    continue
            except Exception as exc:
                command["status"] = "failed"
                command["error"] = str(exc)
            else:
                command["status"] = "completed"
                command["completed_at"] = datetime.now(timezone.utc).isoformat()
            changed = True
        if changed:
            # A risk-resume command persists the newly enabled control through
            # PortfolioManagementEngine. Reload before writing command status
            # so the stale pre-command snapshot cannot overwrite that result.
            persisted = self.journal.portfolio_states().get(account_id) or persisted
            persisted["pending_operational_commands"] = commands
            self.portfolio.states[account_id].pending_operational_commands = [
                dict(command) for command in commands
            ][-100:]
            self.journal.save_portfolio_state(account_id, persisted)

    async def _on_order_group_fill(self, snapshot) -> None:
        handler = getattr(self.strategy, "on_order_group_update", None)
        if handler is not None:
            await handler(snapshot)
            self._persist_strategy_assignments(snapshot.updated_at)

    async def _on_order_group_state(self, snapshot) -> None:
        self.portfolio.on_order_group_update(snapshot)
        await self.risk_supervisor.evaluate(
            snapshot.account_id,
            reason=f"order_group:{snapshot.state.value}",
            protection_required=float(snapshot.protection_required_quantity),
            protection_coverage=float(snapshot.protection_coverage_quantity),
            internal_reaction_ms=snapshot.internal_reaction_ms,
        )

    async def _on_emergency_risk(self, evaluation: RiskEvaluation) -> None:
        if self.order_manager is None:
            return
        state = self.portfolio.states[evaluation.account_id]
        policy = state.policy_override or state.profile.policy
        await self.order_manager.kill_entries(
            evaluation.account_id,
            reason="continuous_risk_emergency",
        )
        if policy.allow_emergency_auto_liquidation:
            await self.order_manager.emergency_flatten(
                evaluation.account_id,
                reason="continuous_risk_emergency",
            )
