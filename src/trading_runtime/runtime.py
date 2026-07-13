from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import date, datetime, timezone
from enum import StrEnum
from typing import Protocol
from uuid import uuid4

from src.market_engine.events import MarketEvent
from src.trading_runtime.broker import BrokerAdapter
from src.trading_runtime.ibkr_schema import OrderRequest
from src.trading_runtime.journal import TradingJournal
from src.trading_runtime.risk import RiskAuthority


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

    async def on_event(self, event: MarketEvent, account_id: str) -> list[OrderRequest]: ...


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
            requests = await self.strategy.on_event(event, account_id)
            for request in requests:
                if request.acctId != account_id:
                    raise ValueError("Strategy emitted an order for a different account")
                self.journal.append(
                    run_id=self.run_id, category="command", entity_type="order", entity_id=request.cOID,
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
