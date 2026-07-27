from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import date, datetime, timezone
from enum import StrEnum
from typing import Protocol
from uuid import uuid4

from src.market_engine.events import MarketEvent
from src.trading_runtime.broker import BrokerAdapter
from src.trading_runtime.ibkr_schema import OPEN_ORDER_STATUSES, OrderRequest
from src.trading_runtime.journal import TradingJournal
from src.trading_runtime.risk import RiskAuthority
from src.trading_runtime.signals import (
    MarketSignal,
    StrategyEvaluation,
    StrategyIntent,
    normalize_strategy_evaluation,
)
from src.trading_runtime.strategy_engine import StrategyObservation


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
    ) -> StrategyEvaluation | list[OrderRequest]: ...


class RuntimeIntentPlanner(Protocol):
    def plan(
        self,
        *,
        intent: StrategyIntent,
        account_id: str,
        event: MarketEvent | None,
    ) -> list[OrderRequest]: ...


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
        self.last_event_time: datetime | None = None
        self.processed_events = 0

    async def initialize(self) -> None:
        await self.broker.initialize()
        available = set(await self.broker.accounts())
        missing = set(self.config.account_ids) - available
        if missing:
            raise ValueError(f"Broker does not expose configured accounts: {', '.join(sorted(missing))}")
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
        executions = await self.broker.on_market_event(event)
        for execution in executions:
            self.journal.append(
                run_id=self.run_id, category="execution", entity_type="fill", entity_id=execution.execution_id,
                account_id=execution.account, event_time=execution.trade_time, payload=execution.to_cpapi(),
            )
        for account_id in self.config.account_ids:
            evaluation = normalize_strategy_evaluation(
                await self.strategy.on_event(event, account_id)
            )
            self._record_strategy_signals(evaluation, account_id)
            requests = await self._orders_for_evaluation(evaluation, account_id, event)
            for request in requests:
                if request.acctId != account_id:
                    raise ValueError("Strategy emitted an order for a different account")
                self.journal.append(
                    run_id=self.run_id, category="command", entity_type="order", entity_id=_order_entity_id(request),
                    account_id=account_id, event_time=event.ts, payload=request.to_cpapi(),
                )
            if requests:
                await self.risk.validate(self.broker, account_id, requests)
                responses = await self.broker.place_orders(account_id, requests)
                for response in responses:
                    self.journal.append(
                        run_id=self.run_id, category="broker", entity_type="order", entity_id=str(response.get("order_id") or ""),
                        account_id=account_id, event_time=event.ts, payload=response,
                    )
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
            requests = await self._orders_for_evaluation(evaluation, account_id, None)
            for request in requests:
                if request.acctId != account_id:
                    raise ValueError("Strategy emitted an order for a different account")
                self.journal.append(
                    run_id=self.run_id,
                    category="command",
                    entity_type="order",
                    entity_id=_order_entity_id(request),
                    account_id=account_id,
                    event_time=signal.effective_at,
                    payload=request.to_cpapi(),
                )
            if requests:
                await self.risk.validate(self.broker, account_id, requests)
                responses = await self.broker.place_orders(account_id, requests)
                for response in responses:
                    self.journal.append(
                        run_id=self.run_id,
                        category="broker",
                        entity_type="order",
                        entity_id=str(response.get("order_id") or ""),
                        account_id=account_id,
                        event_time=signal.effective_at,
                        payload=response,
                    )
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
            requests = await self._orders_for_evaluation(evaluation, account_id, None)
            for request in requests:
                if request.acctId != account_id:
                    raise ValueError("Strategy emitted an order for a different account")
                self.journal.append(
                    run_id=self.run_id,
                    category="command",
                    entity_type="order",
                    entity_id=_order_entity_id(request),
                    account_id=account_id,
                    event_time=observation.observed_at,
                    payload=request.to_cpapi(),
                )
            if requests:
                await self.risk.validate(self.broker, account_id, requests)
                responses = await self.broker.place_orders(account_id, requests)
                for response in responses:
                    self.journal.append(
                        run_id=self.run_id,
                        category="broker",
                        entity_type="order",
                        entity_id=str(response.get("order_id") or ""),
                        account_id=account_id,
                        event_time=observation.observed_at,
                        payload=response,
                    )
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

    async def _orders_for_evaluation(
        self,
        evaluation: StrategyEvaluation,
        account_id: str,
        event: MarketEvent | None,
    ) -> list[OrderRequest]:
        requests = list(evaluation.orders)
        if evaluation.intents and self.intent_planner is None:
            raise ValueError("Strategy emitted semantic intents but the runtime has no intent planner")
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
            should_cancel = getattr(self.intent_planner, "should_cancel_strategy_protection", None)
            if should_cancel is not None and should_cancel(intent):
                await self._cancel_strategy_protection(
                    account_id=account_id,
                    ticker=intent.ticker,
                    event_time=intent.event_time,
                )
            requests.extend(self.intent_planner.plan(intent=intent, account_id=account_id, event=event))
        return requests

    async def _cancel_strategy_protection(
        self,
        *,
        account_id: str,
        ticker: str,
        event_time: datetime,
    ) -> None:
        prefix_provider = getattr(self.intent_planner, "protective_order_prefix", None)
        if prefix_provider is None:
            raise ValueError("Intent planner cannot identify its broker-held protection")
        prefix = str(prefix_provider())
        live_orders = await self.broker.live_orders()
        protective_orders = [
            order
            for order in live_orders
            if order.account == account_id
            and order.ticker.upper() == ticker.upper()
            and order.side.upper() == "SELL"
            and order.order_status in OPEN_ORDER_STATUSES
            and (
                str(order.parentId or "").startswith(prefix)
                or str(order.cOID or "").startswith(prefix)
            )
        ]
        for order in protective_orders:
            self.journal.append(
                run_id=self.run_id,
                category="command",
                entity_type="order_cancel",
                entity_id=str(order.orderId),
                account_id=account_id,
                event_time=event_time,
                payload={
                    "strategy_id": self.config.strategy_id,
                    "strategy_revision": self.config.strategy_revision,
                    "ticker": ticker.upper(),
                    "reason": "replace_protection_with_exit_oca",
                },
            )
            response = await self.broker.cancel_order(account_id, str(order.orderId))
            self.journal.append(
                run_id=self.run_id,
                category="broker",
                entity_type="order_cancel",
                entity_id=str(order.orderId),
                account_id=account_id,
                event_time=event_time,
                payload=response,
            )

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
        await self.snapshot_portfolios()
        self.journal.append(
            run_id=self.run_id, category="lifecycle", entity_type="run", entity_id=self.run_id,
            event_time=self.last_event_time, payload={"status": status, "processed_events": self.processed_events},
        )


def _order_entity_id(request: OrderRequest) -> str:
    return request.cOID or f"{request.parentId or 'standalone'}:{request.orderType}"
