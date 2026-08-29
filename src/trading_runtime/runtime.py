from __future__ import annotations

import asyncio
from dataclasses import asdict, dataclass, replace
from datetime import date, datetime, timedelta, timezone
from enum import StrEnum
from typing import Any, Mapping, Protocol, Sequence
from uuid import uuid4

from src.market_engine.events import MarketEvent, QuoteEvent
from src.trading_runtime.broker import BrokerAdapter
from src.trading_runtime.canonical_session import CanonicalBrokerSession
from src.trading_runtime.control_plane import TradingControlPlane, shared_trading_control_plane
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
    StrategySignal,
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


def _wait_decision_signature(signal: StrategySignal) -> tuple[Any, ...]:
    """Identify a materially distinct non-action decision for transition logging.

    Numeric market evidence changes on every causal observation. Journaling every
    such refresh makes historical runs IO-bound without adding a new strategy
    decision. The failed-condition identities, structural trigger/protection
    anchors, and assignment state capture the points at which the explanation a
    user sees actually changes.
    """

    metadata = dict(signal.metadata or {})
    failed_conditions: list[tuple[str, str, str]] = []
    for stage_name in ("confirmation", "trigger", "veto"):
        stage = dict(dict(metadata.get("entry_rules") or {}).get(stage_name) or {})
        for group_id, rows in dict(stage.get("condition_evidence") or {}).items():
            for row in rows or []:
                passed = bool(row.get("passed"))
                failed = not passed if stage_name != "veto" else passed
                if failed:
                    failed_conditions.append(
                        (
                            stage_name,
                            str(group_id),
                            str(row.get("condition_id") or row.get("left_source_id") or "condition"),
                        )
                    )
    return (
        str(signal.action.value if hasattr(signal.action, "value") else signal.action),
        str(metadata.get("reason_code") or signal.reason),
        str(metadata.get("status") or ""),
        tuple(sorted(failed_conditions)),
        metadata.get("trigger_threshold_price"),
        signal.invalidation_price,
    )


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
    run_plan_id: str = ""
    safety_supervisor_enabled: bool = True
    checkpoint_interval_events: int = 1

    def __post_init__(self) -> None:
        if self.checkpoint_interval_events <= 0:
            raise ValueError("checkpoint_interval_events must be positive")
        if self.mode in {RunMode.LIVE, RunMode.PAPER} and not self.safety_supervisor_enabled:
            raise ValueError("Trading Safety Supervisor cannot be disabled for Live or Paper")

    def resolved_run_id(self) -> str:
        return self.run_id or str(uuid4())


class TradingRuntime:
    """One event/order/portfolio lifecycle for live, paper, replay, and backtest."""

    def __init__(
        self,
        config: RunConfig,
        broker: BrokerAdapter,
        strategy: AutomaticStrategy | None,
        journal: TradingJournal,
        risk: RiskAuthority | None = None,
        intent_planner: RuntimeIntentPlanner | None = None,
        portfolio: PortfolioManagementEngine | None = None,
        portfolio_configuration: Mapping[str, Any] | None = None,
        control_plane: TradingControlPlane | None = None,
    ) -> None:
        if strategy is not None and (
            config.strategy_id != strategy.strategy_id
            or config.strategy_revision != strategy.revision
        ):
            raise ValueError("Run strategy identity does not match loaded strategy revision")
        if strategy is None and (config.strategy_id or config.strategy_revision):
            raise ValueError("Manual runs cannot declare a Strategy identity")
        if config.mode in {RunMode.BACKTEST, RunMode.BACKTEST_DEBUG} and (
            strategy is None or not strategy.automatic
        ):
            raise ValueError("Backtest and Debug require an automatic Strategy")
        self.config = config
        self.run_id = config.resolved_run_id()
        self.broker = broker
        self.strategy = strategy
        self.journal = journal
        self._persisted_assignment_versions: dict[str, tuple[str, str]] = {}
        self._persisted_assignment_times: dict[str, datetime] = {}
        self._last_wait_decision_signatures: dict[tuple[str, str], tuple[Any, ...]] = {}
        self.control_plane = control_plane or shared_trading_control_plane(broker)
        self.control_plane.campaigns.bind_durable_authority(
            journal,
            session_key=config.anchor_date.isoformat(),
        )
        bind_campaigns = getattr(strategy, "bind_campaign_registry", None)
        if bind_campaigns is not None:
            bind_campaigns(self.control_plane.campaigns)
        self.risk = risk or RiskAuthority()
        self.intent_planner = intent_planner
        if portfolio is None:
            profiles, groups = configured_portfolio_profiles_for_runtime(
                config.account_ids,
                mode=config.mode.value,
                configuration=portfolio_configuration,
            )
            portfolio = PortfolioManagementEngine(
                profiles,
                journal=journal,
                run_id=self.run_id,
                strategy_id=config.strategy_id,
                strategy_revision=config.strategy_revision,
                groups=groups,
                control_plane=self.control_plane,
                allocation_identity=config.run_plan_id or config.strategy_id,
            )
        else:
            portfolio.allocation_identity = config.run_plan_id or config.strategy_id
            portfolio.bind_control_plane(self.control_plane)
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
                control_plane=self.control_plane,
            )
            if intent_planner is not None
            else None
        )
        self.risk_supervisor = ContinuousRiskSupervisor(
            self.portfolio,
            journal=journal,
            run_id=self.run_id,
            emergency_callback=self._on_emergency_risk,
            mode=config.mode.value,
            enabled=config.safety_supervisor_enabled,
            control_plane=self.control_plane,
        )
        self.last_event_time: datetime | None = None
        self.processed_events = 0
        self._latest_checkpoint_cursor = ""
        self._broker_stream_task: asyncio.Task[None] | None = None
        self._risk_refresh_task: asyncio.Task[None] | None = None
        self._canonical_session: CanonicalBrokerSession | None = None
        self._review_only = False

    async def initialize(
        self,
        *,
        record_lifecycle: bool = True,
        review_only: bool = False,
    ) -> None:
        self._review_only = review_only
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
            self.portfolio.synchronize_canonical(
                canonical_snapshot,
                persist=not review_only,
            )
        else:
            await self.broker.initialize()
            available = set(await self.broker.accounts())
            await self.portfolio.synchronize(self.broker)
        missing = set(self.config.account_ids) - available
        if missing:
            raise ValueError(f"Broker does not expose configured accounts: {', '.join(sorted(missing))}")
        if not review_only:
            await self.risk.prime(self.broker, self.config.account_ids)
        for account_id in self.config.account_ids if not review_only else ():
            await self.risk_supervisor.evaluate(account_id, reason="runtime_initialize")
        if self.order_manager is not None and not review_only:
            await self.order_manager.configure_broker_session()
            await self.order_manager.recover()
            if hasattr(self.broker, "stream_broker_messages"):
                self._broker_stream_task = asyncio.create_task(self._consume_broker_stream())
                self._risk_refresh_task = asyncio.create_task(self._refresh_live_risk())
        if record_lifecycle and not review_only:
            self.journal.append(
                run_id=self.run_id,
                category="lifecycle",
                entity_type="run",
                entity_id=self.run_id,
                payload={"status": "running", "config": asdict(self.config)},
            )

    async def process_event(
        self,
        event: MarketEvent,
        *,
        evaluate_strategy: bool = True,
    ) -> None:
        self._record_market_event_state(event)
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
            self.portfolio.synchronize_canonical(
                self._canonical_session.projector.snapshot(),
                persist=not self._review_only,
            )
        if self.strategy is not None and evaluate_strategy:
            for account_id in self.config.account_ids:
                evaluation = normalize_strategy_evaluation(
                    await self.strategy.on_event(event, account_id)
                )
                self._record_strategy_signals(evaluation, account_id)
                await self._execute_intents(evaluation, account_id, event)
        self._record_market_cursor(event)

    def process_passive_market_event(self, event: MarketEvent) -> None:
        """Advance market state when no order can match and strategy evaluation is external."""

        if bool(getattr(self.broker, "has_orders", True)):
            raise RuntimeError("Passive market processing requires an empty broker order book")
        observe = getattr(self.broker, "observe_market_event", None)
        if observe is None:
            raise RuntimeError("Broker does not support passive market observation")
        self._record_market_event_state(event)
        observe(event)
        self._record_market_cursor(event)

    def process_passive_market_events(self, events: Sequence[MarketEvent]) -> None:
        """Coalesce an order-free event burst while preserving its causal cursor.

        With no working order, intermediate quotes and trades cannot create an
        execution. Only the latest event of each kind per ticker is needed for
        broker marks and the execution snapshot; the full event count and final
        cursor remain authoritative for progress and restart accounting.
        """

        if not events:
            return
        if bool(getattr(self.broker, "has_orders", True)):
            raise RuntimeError("Passive market batching requires an empty broker order book")
        observe = getattr(self.broker, "observe_market_event", None)
        if observe is None:
            raise RuntimeError("Broker does not support passive market observation")
        previous_time = self.last_event_time
        for event in events:
            if previous_time is not None and event.ts < previous_time:
                raise ValueError("Market events must be processed in non-decreasing timestamp order")
            previous_time = event.ts
        latest: dict[tuple[str, str], MarketEvent] = {}
        for event in events:
            latest[(event.ticker, event.kind)] = event
        for event in sorted(latest.values(), key=lambda row: (row.ts, row.sequence, row.kind)):
            self._observe_market_event_state(event)
            observe(event)
        prior_count = self.processed_events
        self.processed_events += len(events)
        self.last_event_time = events[-1].ts
        self._set_market_cursor(events[-1])
        interval = self.config.checkpoint_interval_events
        if prior_count // interval < self.processed_events // interval:
            self.journal.save_checkpoint(
                self.run_id,
                self._latest_checkpoint_cursor,
                {"processed_events": self.processed_events},
                events[-1].ts,
            )

    def _record_market_event_state(self, event: MarketEvent) -> None:
        if self.last_event_time is not None and event.ts < self.last_event_time:
            raise ValueError("Market events must be processed in non-decreasing timestamp order")
        self.last_event_time = event.ts
        self.processed_events += 1
        self._observe_market_event_state(event)

    def _observe_market_event_state(self, event: MarketEvent) -> None:
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

    def _record_market_cursor(self, event: MarketEvent) -> None:
        self._set_market_cursor(event)
        if self.processed_events % self.config.checkpoint_interval_events == 0:
            self.journal.save_checkpoint(
                self.run_id,
                self._latest_checkpoint_cursor,
                {"processed_events": self.processed_events},
                event.ts,
            )

    def _set_market_cursor(self, event: MarketEvent) -> None:
        cursor = f"{event.ts.astimezone(timezone.utc).isoformat()}|{event.sequence}|{event.kind}"
        self._latest_checkpoint_cursor = cursor

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
        self._update_execution_market_from_observation(observation)
        self.last_event_time = observation.observed_at
        for account_id in self.config.account_ids:
            evaluation = normalize_strategy_evaluation(await handler(observation, account_id))
            self._record_strategy_signals(evaluation, account_id)
            await self._execute_intents(evaluation, account_id, None)
        self._persist_strategy_assignments(observation.observed_at)

    async def process_account_strategy_observation(
        self,
        observation: StrategyObservation,
        account_id: str,
    ) -> None:
        """Evaluate one normalized observation against exactly one account boundary."""
        if account_id not in self.config.account_ids:
            raise ValueError(f"Strategy observation account is outside this run: {account_id}")
        if self.last_event_time is not None and observation.observed_at < self.last_event_time:
            raise ValueError("Strategy observations must not move behind the runtime clock")
        handler = getattr(self.strategy, "on_observation", None)
        if handler is None:
            return
        self._update_execution_market_from_observation(observation)
        self.last_event_time = observation.observed_at
        evaluation = normalize_strategy_evaluation(await handler(observation, account_id))
        self._record_strategy_signals(evaluation, account_id)
        await self._execute_intents(evaluation, account_id, None)
        self.persist_strategy_assignments(
            observation.observed_at,
            account_id=account_id,
            ticker=observation.ticker,
        )

    def _update_execution_market_from_observation(
        self, observation: StrategyObservation
    ) -> None:
        """Make the occurrence's causal quote available to OMS without a raw-event detour."""

        if observation.bid <= 0 or observation.ask < observation.bid:
            return
        snapshot = ExecutionMarketSnapshot(
            ticker=observation.ticker,
            bid=observation.bid,
            ask=observation.ask,
            tick_size=float(
                observation.source_values.get("market.tick_size", {}).get("value", 0.01)
                if isinstance(observation.source_values.get("market.tick_size"), Mapping)
                else 0.01
            ),
            observed_at=observation.observed_at,
            source="signal_stream_occurrence",
            volatility=float(observation.volatility or 0),
            upper_price_band=observation.upper_luld_price,
            lower_price_band=None,
        )
        self.execution_market_data.update(snapshot)
        if self.order_manager is not None:
            self.order_manager.on_market_snapshot(snapshot)

    def _record_strategy_signals(
        self, evaluation: StrategyEvaluation, account_id: str
    ) -> None:
        for signal in evaluation.signals:
            action = str(signal.action.value if hasattr(signal.action, "value") else signal.action)
            if action in {"wait", "hold"} and not evaluation.intents:
                decision_key = (account_id, signal.ticker.upper())
                signature = _wait_decision_signature(signal)
                if self._last_wait_decision_signatures.get(decision_key) == signature:
                    continue
                self._last_wait_decision_signatures[decision_key] = signature
            else:
                decision_key = (account_id, signal.ticker.upper())
                self._last_wait_decision_signatures.pop(decision_key, None)
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
    ) -> list[dict[str, Any]]:
        if evaluation.intents and self.intent_planner is None:
            raise ValueError("Strategy emitted semantic intents but the runtime has no intent planner")
        if evaluation.intents and self.order_manager is None:
            raise ValueError("Strategy emitted semantic intents but the runtime has no order manager")
        results: list[dict[str, Any]] = []
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
                results.append({"decision": decision.payload(), "order_group": None})
                continue
            assignment = self._assignment_for_intent(approved_intent)
            opening_entry = str(approved_intent.action) in {"enter_long", "enter_short"}
            if opening_entry and assignment is not None:
                try:
                    self.control_plane.campaigns.reserve(assignment)
                except ValueError:
                    self.portfolio.release_intent(
                        approved_intent.intent_id,
                        reason="ticker_owned_by_competing_strategy",
                    )
                    results.append({
                        "decision": {
                            **decision.payload(),
                            "status": "rejected",
                            "reason": "ticker_owned_by_competing_strategy",
                        },
                        "order_group": None,
                    })
                    continue
            try:
                order_group = await self.order_manager.submit_intent(
                    approved_intent,
                    account_id=account_id,
                    event=event,
                )
                results.append({
                    "decision": decision.payload(),
                    "order_group": asdict(order_group),
                })
            except Exception:
                snapshot = self.order_manager.snapshot_for_intent(approved_intent.intent_id)
                if snapshot is None or snapshot.state != OrderManagementState.OUTCOME_UNKNOWN:
                    self.portfolio.release_intent(
                        approved_intent.intent_id,
                        reason="order_management_submission_failed",
                    )
                    if opening_entry and assignment is not None:
                        self.control_plane.campaigns.release_reservation(assignment)
                raise
        return results

    def _assignment_for_intent(self, intent: StrategyIntent):
        assignment_id = str(intent.metadata.get("assignment_id") or "")
        assignments = getattr(self.strategy, "assignments", None)
        if not assignment_id or assignments is None:
            return None
        return next(
            (
                assignment
                for assignment in assignments()
                if assignment.assignment_id == assignment_id
            ),
            None,
        )

    async def submit_external_intent(
        self,
        intent: StrategyIntent,
        *,
        account_id: str,
        proposal_id: str,
        proposal_authority: str,
    ) -> dict[str, Any]:
        """Route a confirmed manual/semi-auto proposal through Portfolio and OMS.

        This is deliberately broker-neutral. Callers cannot pass an order; they
        provide one semantic intent whose proposal evidence is journaled before
        normal Portfolio admission and OMS planning run.
        """

        if proposal_authority not in {"manual", "semi_automatic"}:
            raise ValueError("External proposal authority must be manual or semi_automatic")
        if not proposal_id:
            raise ValueError("External proposal_id is required")
        if account_id not in self.config.account_ids:
            raise ValueError("External proposal account is outside this run")
        if self.last_event_time is not None and intent.event_time < self.last_event_time:
            raise ValueError("External proposal cannot move behind the runtime clock")
        self.journal.append(
            run_id=self.run_id,
            category="trade_proposal",
            entity_type="trade_proposal_confirmed",
            entity_id=proposal_id,
            account_id=account_id,
            event_time=intent.event_time,
            payload={
                "proposal_id": proposal_id,
                "authority": proposal_authority,
                "status": "confirmed",
                "intent": intent.payload(),
            },
        )
        try:
            results = await self._execute_intents(
                StrategyEvaluation(intents=(intent,)),
                account_id,
                None,
            )
        except Exception as exc:
            self.journal.append(
                run_id=self.run_id,
                category="trade_proposal",
                entity_type="trade_proposal_result",
                entity_id=proposal_id,
                account_id=account_id,
                event_time=intent.event_time,
                payload={
                    "proposal_id": proposal_id,
                    "authority": proposal_authority,
                    "status": "failed",
                    "error": str(exc),
                },
            )
            raise
        result = results[0]
        self.journal.append(
            run_id=self.run_id,
            category="trade_proposal",
            entity_type="trade_proposal_result",
            entity_id=proposal_id,
            account_id=account_id,
            event_time=intent.event_time,
            payload={
                "proposal_id": proposal_id,
                "authority": proposal_authority,
                "status": str(result["decision"].get("status") or "rejected"),
                **result,
            },
        )
        return {"proposal_id": proposal_id, **result}

    def persist_strategy_assignments(
        self,
        event_time: datetime,
        *,
        record_events: bool = True,
        account_id: str = "",
        ticker: str = "",
    ) -> None:
        """Persist changed campaign state without one transaction per ticker."""

        assignments = getattr(self.strategy, "assignments", None)
        if assignments is None:
            return
        normalized_ticker = ticker.strip().upper()
        selected = (
            assignment
            for assignment in assignments()
            if (not account_id or assignment.account_id == account_id)
            and (not normalized_ticker or assignment.ticker.upper() == normalized_ticker)
        )
        changed: list[tuple[Any, dict[str, Any], tuple[str, str]]] = []
        for assignment in selected:
            payload = assignment.payload()
            version = (
                str(payload.get("status") or ""),
                str(payload.get("updated_at") or ""),
            )
            if self._persisted_assignment_versions.get(assignment.assignment_id) == version:
                continue
            previous = self._persisted_assignment_versions.get(assignment.assignment_id)
            last_persisted_at = self._persisted_assignment_times.get(
                assignment.assignment_id
            )
            status_changed = previous is None or previous[0] != version[0]
            if (
                not status_changed
                and last_persisted_at is not None
                and event_time < last_persisted_at + timedelta(seconds=5)
            ):
                continue
            changed.append((assignment, payload, version))
        if not changed:
            return
        self.journal.save_strategy_assignments(
            [payload for _, payload, _ in changed]
        )
        if record_events:
            self.journal.append_many([
                {
                    "run_id": self.run_id,
                    "category": "strategy",
                    "entity_type": "strategy_assignment_state",
                    "entity_id": assignment.assignment_id,
                    "account_id": assignment.account_id,
                    "event_time": event_time,
                    "payload": {
                        "event": "assignment_state_saved",
                        "assignment_id": assignment.assignment_id,
                        "strategy_id": assignment.strategy_id,
                        "strategy_revision": assignment.strategy_revision,
                        "ticker": assignment.ticker,
                        "status": assignment.status.value,
                        "state": assignment.state,
                    },
                }
                for assignment, _, _ in changed
            ])
        for assignment, _, version in changed:
            self._persisted_assignment_versions[assignment.assignment_id] = version
            self._persisted_assignment_times[assignment.assignment_id] = event_time

    def _persist_strategy_assignments(self, event_time: datetime) -> None:
        self.persist_strategy_assignments(event_time)
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

    async def canonical_snapshot(self, *, as_of: datetime | None = None):
        """Return the freshest canonical broker projection for UI and recovery consumers."""
        if self._canonical_session is not None:
            await self._canonical_session.reconcile()
            snapshot = self._canonical_session.projector.snapshot()
            self.portfolio.synchronize_canonical(
                snapshot,
                persist=not self._review_only,
            )
            return replace(snapshot, as_of=as_of) if as_of is not None else snapshot
        raise RuntimeError("The configured broker does not expose canonical Replay state")

    async def finish(self, status: str = "completed") -> None:
        if (
            self.last_event_time is not None
            and self._latest_checkpoint_cursor
            and self.processed_events % self.config.checkpoint_interval_events != 0
        ):
            self.journal.save_checkpoint(
                self.run_id,
                self._latest_checkpoint_cursor,
                {"processed_events": self.processed_events},
                self.last_event_time,
            )
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
        if str(snapshot.action) in {"enter_long", "enter_short"}:
            assignment = self._assignment_for_snapshot(snapshot)
            if assignment is not None:
                self.control_plane.campaigns.claim(assignment)
        handler = getattr(self.strategy, "on_order_group_update", None)
        if handler is not None:
            await handler(snapshot)
            self._persist_strategy_assignments(snapshot.updated_at)

    async def _on_order_group_state(self, snapshot) -> None:
        if snapshot.state in {
            OrderManagementState.CANCELLED,
            OrderManagementState.REJECTED,
            OrderManagementState.POLICY_BLOCKED,
        }:
            assignment = self._assignment_for_snapshot(snapshot)
            if assignment is not None:
                self.control_plane.campaigns.release_reservation(assignment)
        self.portfolio.on_order_group_update(snapshot)
        await self.risk_supervisor.evaluate(
            snapshot.account_id,
            reason=f"order_group:{snapshot.state.value}",
            protection_required=float(snapshot.protection_required_quantity),
            protection_coverage=float(snapshot.protection_coverage_quantity),
            internal_reaction_ms=snapshot.internal_reaction_ms,
        )

    def _assignment_for_snapshot(self, snapshot):
        assignment_id = str(getattr(snapshot, "assignment_id", "") or "")
        assignments = getattr(self.strategy, "assignments", None)
        if not assignment_id or assignments is None:
            return None
        return next(
            (
                assignment
                for assignment in assignments()
                if assignment.assignment_id == assignment_id
            ),
            None,
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
