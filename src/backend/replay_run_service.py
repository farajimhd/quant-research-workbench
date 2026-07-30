from __future__ import annotations

import asyncio
import hashlib
import json
import os
import re
import time
import urllib.parse
import urllib.request
from copy import deepcopy
from dataclasses import dataclass, field, replace
from datetime import UTC, date, datetime, time as clock_time, timedelta
from pathlib import Path
from typing import Any
from uuid import uuid4
from zoneinfo import ZoneInfo

import websockets

from src.backend.canonical_trading_service import trading_state_payload
from src.backend.trading_runtime_service import (
    historical_day_coverage,
    historical_gateway_base_url,
    historical_gateway_snapshot,
)
from src.backend.trading_configuration_service import (
    merged_assignment_parameters,
    replay_configuration_snapshot,
)
from src.market_engine.events import MarketEvent, QuoteEvent
from src.market_engine.historical_source import QmdHistoricalEventSource
from src.trading_runtime.domain import InstrumentContract, TradingMode
from src.trading_runtime.journal import TradingJournal
from src.trading_runtime.portfolio import (
    PortfolioAccountProfile,
    PortfolioGroupPolicy,
    PortfolioManagementEngine,
    portfolio_policy_from_payload,
)
from src.trading_runtime.runtime import RunConfig, RunMode, TradingRuntime
from src.trading_runtime.simulated_broker import SimulatedBrokerAdapter, SimulationConfig
from src.trading_runtime.strategy_engine import (
    AssignmentStatus,
    AssignedLongMomentumStrategy,
    StrategyAssignment,
    StrategyObservation,
    StrategyPermissions,
    entry_rule_timeframes,
    strategy_observation_source_values,
)
from src.trading_runtime.strategy_orders import RuntimeIbkrStrategyOrderPlanner
from src.trading_runtime.strategy_campaign import campaign_state


NEW_YORK = ZoneInfo("America/New_York")
DEFAULT_REPLAY_ROOT = Path(r"D:\TradingML\runtimes\trading\replay")
REPLAY_STATUSES = {
    "created",
    "warming",
    "ready",
    "running",
    "paused",
    "fast_forwarding",
    "completed",
    "stopped",
    "failed",
}
TERMINAL_REPLAY_STATUSES = {"completed", "stopped", "failed"}
PLAYBACK_SPEEDS = (1.0, 5.0, 30.0, 120.0, 0.0)


@dataclass(frozen=True, slots=True)
class ReplayRunDefinition:
    session_date: date
    start_time: clock_time
    initial_cash: float = 100_000.0
    assignment_ids: tuple[str, ...] = ()
    tickers: tuple[str, ...] = ()
    configuration_revision: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not 4 <= self.start_time.hour <= 20:
            raise ValueError("Replay start time must be inside the 04:00-20:00 New York session")
        if self.start_time.hour == 20 and (
            self.start_time.minute or self.start_time.second or self.start_time.microsecond
        ):
            raise ValueError("Replay start time cannot be later than 20:00 New York")
        if not 1_000 <= self.initial_cash <= 1_000_000_000:
            raise ValueError("Replay initial cash must be between 1,000 and 1,000,000,000")
        if len(self.tickers) > 100:
            raise ValueError("Replay supports at most 100 explicitly configured symbols")
        if not self.configuration_revision.get("revision_id"):
            raise ValueError("Replay requires an approved trading configuration revision")

    @property
    def session_start(self) -> datetime:
        return datetime.combine(self.session_date, clock_time(4, 0), tzinfo=NEW_YORK)

    @property
    def session_end(self) -> datetime:
        return datetime.combine(self.session_date, clock_time(20, 0), tzinfo=NEW_YORK)

    @property
    def requested_start(self) -> datetime:
        return datetime.combine(self.session_date, self.start_time, tzinfo=NEW_YORK)

    def payload(self) -> dict[str, Any]:
        approved = self.configuration_revision
        configuration = dict(approved.get("payload") or {})
        canvas = dict(configuration.get("canvas") or {})
        return {
            "session_date": self.session_date.isoformat(),
            "start_time": self.start_time.isoformat(timespec="seconds"),
            "session_start": self.session_start.isoformat(),
            "session_end": self.session_end.isoformat(),
            "requested_start": self.requested_start.isoformat(),
            "initial_cash": self.initial_cash,
            "assignment_ids": list(self.assignment_ids),
            "tickers": list(self.tickers),
            "configuration_revision_id": approved.get("revision_id", ""),
            "configuration_revision": approved.get("revision", 0),
            "configuration_label": approved.get("label", ""),
            "configuration_content_hash": approved.get("content_hash", ""),
            "canvas_revision": canvas.get("revision", ""),
            "canvas_profile": canvas.get("profile") or {},
        }


@dataclass(slots=True)
class ReplayDerivedFrame:
    as_of: datetime
    bar: dict[str, Any]
    indicator: dict[str, Any]
    sequence: int
    ticker: str
    timeframe: str
    signals: dict[str, float] = field(default_factory=dict)


class ReplayRunController:
    """One durable event-time Replay run over the shared trading runtime."""

    def __init__(
        self,
        definition: ReplayRunDefinition,
        *,
        run_id: str | None = None,
        runtime_root: Path | None = None,
    ) -> None:
        self.definition = definition
        self.run_id = run_id or str(uuid4())
        self.runtime_root = (runtime_root or replay_runtime_root()).resolve()
        self.run_dir = (self.runtime_root / self.run_id).resolve()
        if self.runtime_root != self.run_dir and self.runtime_root not in self.run_dir.parents:
            raise ValueError("Replay run directory escaped the configured runtime root")
        self.status = "created"
        self.error = ""
        self.created_at = datetime.now(UTC)
        self.updated_at = self.created_at
        self.current_time: datetime | None = None
        self.speed = 30.0
        self.processed_events = 0
        self.warmup_events = 0
        self._task: asyncio.Task[None] | None = None
        self._condition = asyncio.Condition()
        self._stop_requested = False
        self._step_until: datetime | None = None
        self._fast_forward_until: datetime | None = None
        self._subscribers: set[asyncio.Queue[dict[str, Any]]] = set()
        self._last_publish_monotonic = 0.0
        self._runtime: TradingRuntime | None = None
        self._strategy: AssignedLongMomentumStrategy | None = None
        self._planner: RuntimeIbkrStrategyOrderPlanner | None = None
        self._journal: TradingJournal | None = None
        self._account_map: dict[str, str] = {}
        self._quotes: dict[str, QuoteEvent] = {}
        self._previous_vwap: dict[tuple[str, str], tuple[datetime, float]] = {}
        self._strategy_source_values: dict[str, dict[str, Any]] = {}
        self._canvas_state_cache: tuple[float, dict[str, Any]] | None = None
        self._runtime_finished = False
        self._stream_tickers: tuple[str, ...] = ()
        self._pace_event_anchor: datetime | None = None
        self._pace_wall_anchor = 0.0
        self._pace_reset = True

    async def start(self) -> None:
        if self._task is not None:
            return
        self.run_dir.mkdir(parents=True, exist_ok=True)
        self._write_manifest()
        self._task = asyncio.create_task(self._run(), name=f"replay-run-{self.run_id}")

    async def command(
        self,
        command: str,
        *,
        speed: float | None = None,
        target_time: clock_time | None = None,
        step_seconds: float = 1.0,
    ) -> dict[str, Any]:
        normalized = command.strip().lower()
        if normalized not in {"play", "pause", "step", "set_speed", "fast_forward", "stop"}:
            raise ValueError(f"Unsupported Replay command: {command}")
        async with self._condition:
            if self.status in TERMINAL_REPLAY_STATUSES:
                raise ValueError(f"Replay run is already {self.status}")
            if normalized == "play":
                self.status = "running"
                self._step_until = None
                self._fast_forward_until = None
                self._pace_reset = True
            elif normalized == "pause":
                self.status = "paused"
                self._step_until = None
                self._fast_forward_until = None
            elif normalized == "step":
                if step_seconds <= 0 or step_seconds > 60:
                    raise ValueError("Replay step_seconds must be greater than zero and at most 60")
                base = self.current_time or self.definition.requested_start
                self._step_until = base + timedelta(seconds=step_seconds)
                self._fast_forward_until = None
                self.status = "running"
                self._pace_reset = True
            elif normalized == "set_speed":
                if speed is None or speed not in PLAYBACK_SPEEDS:
                    raise ValueError(
                        "Replay speed must be one of 1, 5, 30, 120, or 0 for maximum"
                    )
                self.speed = speed
                self._pace_reset = True
            elif normalized == "fast_forward":
                if target_time is None:
                    raise ValueError("Replay fast-forward requires a target_time")
                target = datetime.combine(
                    self.definition.session_date,
                    target_time,
                    tzinfo=NEW_YORK,
                )
                if target <= (self.current_time or self.definition.requested_start):
                    raise ValueError("Replay fast-forward target must be after the current clock")
                if target > self.definition.session_end:
                    raise ValueError("Replay fast-forward target cannot exceed 20:00 New York")
                self._fast_forward_until = target
                self._step_until = None
                self.status = "fast_forwarding"
            else:
                self._stop_requested = True
                self.status = "stopped"
            self.updated_at = datetime.now(UTC)
            self._condition.notify_all()
        await self._publish(force=True)
        self._write_manifest()
        return self.snapshot()

    async def add_assignment(self, payload: dict[str, Any]) -> dict[str, Any]:
        if self._strategy is None:
            raise ValueError("Replay strategy runtime is not ready")
        source_account = str(payload.get("account_id") or "").strip()
        account_id = self._account_map.get(source_account, source_account)
        if account_id not in self.account_ids:
            raise ValueError("Replay assignment must use one of the run's simulated accounts")
        now = self.current_time or self.definition.requested_start
        strategy_config = dict(self.definition.configuration_revision["payload"]["strategy"])
        assignment = StrategyAssignment(
            assignment_id=str(payload.get("assignment_id") or uuid4()),
            strategy_id=str(payload.get("strategy_id") or strategy_config["strategy_id"]),
            strategy_revision=int(payload.get("strategy_revision") or strategy_config["revision"]),
            account_id=account_id,
            ticker=_ticker(payload.get("ticker")),
            conid=int(payload.get("conid") or 0),
            status=AssignmentStatus(str(payload.get("status") or AssignmentStatus.WATCHING)),
            permissions=StrategyPermissions(**dict(payload.get("permissions") or {})),
            parameters=merged_assignment_parameters(
                self.definition.configuration_revision["payload"],
                {"parameters": dict(payload.get("parameters") or {})},
            ),
            state=dict(payload.get("state") or {}),
            source=str(payload.get("source") or "replay_canvas"),
            created_at=now,
            updated_at=now,
        )
        if assignment.conid <= 0:
            raise ValueError("Replay assignment requires a positive point-in-time conid")
        if assignment.ticker not in self._stream_tickers:
            raise ValueError(
                f"{assignment.ticker} was not included in this run's approved historical stream"
            )
        self._strategy.upsert_assignment(assignment)
        if self._planner is None:
            raise ValueError("Replay order planner is not ready")
        self._planner.upsert_instrument(
            InstrumentContract(
                instrument_id=f"simulated:{assignment.conid}",
                conid=assignment.conid,
                symbol=assignment.ticker,
                security_type="STK",
                currency="USD",
                exchange="SMART",
            )
        )
        if self._journal is not None:
            self._journal.save_strategy_assignment(assignment.payload())
        self._write_manifest()
        await self._publish(force=True)
        return assignment.payload()

    async def command_assignment(
        self,
        assignment_id: str,
        command: str,
        detail: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        if self._strategy is None:
            raise ValueError("Replay strategy runtime is not ready")
        assignment = self._strategy.command_assignment(
            assignment_id,
            command,
            event_time=self.current_time or self.definition.requested_start,
            detail=detail,
        )
        if self._journal is not None:
            self._journal.save_strategy_assignment(assignment.payload())
        await self._publish(force=True)
        return assignment.payload()

    @property
    def account_ids(self) -> tuple[str, ...]:
        values = tuple(dict.fromkeys(self._account_map.values()))
        return values or ("SIM-REPLAY",)

    def snapshot(self) -> dict[str, Any]:
        current = self.current_time or self.definition.session_start
        duration = max(
            1.0,
            (self.definition.session_end - self.definition.requested_start).total_seconds(),
        )
        elapsed = max(0.0, (current - self.definition.requested_start).total_seconds())
        return {
            "schema_version": 1,
            "run_id": self.run_id,
            "status": self.status,
            "error": self.error,
            "created_at": self.created_at.isoformat(),
            "updated_at": self.updated_at.isoformat(),
            "current_time": current.isoformat(),
            "session_date": self.definition.session_date.isoformat(),
            "requested_start": self.definition.requested_start.isoformat(),
            "session_start": self.definition.session_start.isoformat(),
            "session_end": self.definition.session_end.isoformat(),
            "speed": self.speed,
            "processed_events": self.processed_events,
            "warmup_events": self.warmup_events,
            "progress": min(1.0, elapsed / duration),
            "account_ids": list(self.account_ids),
            "account_mapping": dict(self._account_map),
            "assignments": (
                [assignment.payload() for assignment in self._strategy.assignments()]
                if self._strategy is not None
                else []
            ),
            **{
                key: value
                for key, value in self.definition.payload().items()
                if key.startswith("configuration_") or key.startswith("canvas_")
            },
            "tickers": list(self.definition.tickers),
        }

    async def canvas_payload(self, symbol: str = "AAPL") -> dict[str, Any]:
        if self._runtime is None or self._journal is None:
            raise ValueError("Replay trading state is not ready")
        now = time.monotonic()
        if self._canvas_state_cache and now - self._canvas_state_cache[0] <= 0.2:
            trading = self._canvas_state_cache[1]
        else:
            trading = trading_state_payload(
                await self._runtime.canonical_snapshot(
                    as_of=self.current_time or self.definition.requested_start,
                )
            )
            self._canvas_state_cache = (now, trading)
        records = self._journal.recent_records(
            self.run_id,
            categories=("strategy", "strategy_decision", "order_management"),
            limit=5_000,
        )
        strategy_records = [
            {
                **record.payload,
                "event_time": record.event_time.isoformat(),
                "recorded_at": record.recorded_at.isoformat(),
                "category": record.category,
                "entity_type": record.entity_type,
                "entity_id": record.entity_id,
            }
            for record in records
            if record.category in {"strategy", "strategy_decision", "order_management"}
        ]
        assignments = (
            [assignment.payload() for assignment in self._strategy.assignments()]
            if self._strategy is not None
            else []
        )
        ticker = _ticker(symbol)
        ticker_assignments = [
            row for row in assignments if str(row.get("ticker") or "").upper() == ticker
        ]
        configuration = self.definition.configuration_revision["payload"]
        strategy_configuration = dict(configuration["strategy"])
        definition = {
            **strategy_configuration,
            "config": {"parameters": strategy_configuration.get("parameters") or {}},
        }
        strategy = {
            "fixture": False,
            "run_id": self.run_id,
            "strategy_id": strategy_configuration["strategy_id"],
            "name": strategy_configuration["name"],
            "revision": strategy_configuration["revision"],
            "profile_id": strategy_configuration.get("profile_id"),
            "profile_revision": strategy_configuration.get("profile_revision"),
            "deployment": deepcopy(configuration.get("deployment") or {}),
            "capabilities": deepcopy(strategy_configuration.get("capabilities") or []),
            "automatic": True,
            "state": ticker_assignments[0]["status"] if ticker_assignments else "not_assigned",
            "definition": definition,
            "assignment": ticker_assignments[0] if ticker_assignments else None,
            "assignments": assignments,
            "signals": [
                row
                for row in strategy_records
                if row["entity_type"] == "signal"
                and str(row.get("ticker") or "").upper() == ticker
            ],
            "order_management": [
                row for row in strategy_records if row["category"] == "order_management"
            ],
            "taxonomy": definition.get("taxonomy"),
            "historical_source": "replay_run_journal_only",
        }
        return {
            "as_of": trading["as_of"],
            "coverage": {},
            "chart": {"bars": [], "indicators": [], "symbol": ticker, "timeframe": "1m"},
            "errors": {},
            "fills": trading.get("executions", []),
            "journal": strategy_records,
            "news": [],
            "orders": trading.get("orders", []),
            "portfolio": trading.get("portfolio", {}),
            "preview_kind": "replay_run",
            "scanner": [],
            "scanner_meta": {"status": "run_clock", "row_count": 0},
            "sec": [],
            "strategy": strategy,
            "trading": trading,
            "xbrl": [],
            "run": self.snapshot(),
        }

    def subscribe(self) -> asyncio.Queue[dict[str, Any]]:
        queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue(maxsize=4)
        self._subscribers.add(queue)
        return queue

    def unsubscribe(self, queue: asyncio.Queue[dict[str, Any]]) -> None:
        self._subscribers.discard(queue)

    async def _run(self) -> None:
        try:
            self.status = "warming"
            self.updated_at = datetime.now(UTC)
            await self._publish(force=True)
            await self._initialize_runtime()
            frames = await self._load_strategy_frames()
            frame_index = 0
            self._stream_tickers = self._resolved_tickers()
            source = QmdHistoricalEventSource(
                historical_gateway_base_url(),
                start=self.definition.session_start,
                end=self.definition.session_end,
                tickers=list(self._stream_tickers),
                batch_size=1_000,
            )
            await source.health()
            async for batch in source.stream():
                for event_index, event in enumerate(batch.events, start=1):
                    if self._stop_requested:
                        await self._finish("stopped")
                        return
                    if event.ts >= self.definition.requested_start:
                        if self.status == "warming":
                            self.status = "ready"
                            self.current_time = self.definition.requested_start
                            self.updated_at = datetime.now(UTC)
                            await self._publish(force=True)
                            self._write_manifest()
                        await self._wait_until_active()
                        if self._stop_requested:
                            await self._finish("stopped")
                            return
                        await self._pace(event)
                    while frame_index < len(frames) and frames[frame_index].as_of <= event.ts:
                        frame = frames[frame_index]
                        if frame.as_of < self.definition.requested_start:
                            self._remember_strategy_frame(frame)
                        else:
                            await self._wait_until_active()
                            if self._stop_requested:
                                await self._finish("stopped")
                                return
                            await self._process_strategy_frame(frame)
                            await self._after_event(frame.as_of)
                        frame_index += 1
                    if event.ts >= self.definition.requested_start:
                        await self._wait_until_active()
                        if self._stop_requested:
                            await self._finish("stopped")
                            return
                    await self._process_market_event(event)
                    if event.ts < self.definition.requested_start:
                        self.warmup_events += 1
                    else:
                        self.processed_events += 1
                        await self._after_event(event.ts)
                    if event_index % 100 == 0:
                        await asyncio.sleep(0)
            while frame_index < len(frames):
                frame = frames[frame_index]
                if frame.as_of >= self.definition.requested_start:
                    await self._wait_until_active()
                    await self._process_strategy_frame(frame)
                    await self._after_event(frame.as_of)
                frame_index += 1
            await self._finish("completed")
        except asyncio.CancelledError:
            await self._finish("stopped")
            raise
        except Exception as exc:
            self.error = str(exc)
            await self._finish("failed")

    async def _initialize_runtime(self) -> None:
        configuration = self.definition.configuration_revision["payload"]
        strategy_configuration = dict(configuration["strategy"])
        source_assignments = self._selected_assignments()
        bindings = [
            dict(row)
            for row in configuration["accounts"]["bindings"]
            if bool(row.get("enabled", True)) and "replay" in list(row.get("modes") or [])
        ]
        simulated_by_key = {
            str(binding["account_key"]): f"SIM-{index + 1:02d}-{_slug(str(binding['account_key']))}"
            for index, binding in enumerate(bindings)
        }
        self._account_map = {
            str(binding.get("source_account_id") or binding["account_key"]): simulated_by_key[
                str(binding["account_key"])
            ]
            for binding in bindings
        }
        self._account_map.update(simulated_by_key)
        assignments = [
            _assignment_from_payload(
                row,
                account_id=simulated_by_key[str(row["account_key"])],
                source=f"replay:{row.get('source') or 'configured'}",
                configuration=configuration,
            )
            for row in source_assignments
        ]
        self._strategy = AssignedLongMomentumStrategy(assignments)
        instruments = {
            assignment.ticker: InstrumentContract(
                instrument_id=f"simulated:{assignment.conid}",
                conid=assignment.conid,
                symbol=assignment.ticker,
                security_type="STK",
                currency="USD",
                exchange="SMART",
            )
            for assignment in assignments
        }
        self._planner = RuntimeIbkrStrategyOrderPlanner(
            instruments,
            strategy_id=str(strategy_configuration["strategy_id"]),
            strategy_revision=int(strategy_configuration["revision"]),
            limit_offset_bps=float(configuration["oms"]["limit_offset_bps"]),
        )
        self._journal = TradingJournal(self.run_dir / "journal.sqlite3")
        self._journal.append(
            run_id=self.run_id,
            category="configuration",
            entity_type="approved_trading_configuration",
            entity_id=str(self.definition.configuration_revision["revision_id"]),
            payload=deepcopy(self.definition.configuration_revision),
            event_time=self.definition.requested_start,
        )
        policies = {
            str(row["policy_id"]): portfolio_policy_from_payload(dict(row))
            for row in configuration["portfolio"]["policies"]
        }
        portfolio_profiles = [
            PortfolioAccountProfile(
                account_key=str(binding["account_key"]),
                account_id=simulated_by_key[str(binding["account_key"])],
                mode="replay",
                account_class=str(binding.get("account_class") or "simulated"),
                policy=policies[str(binding["portfolio_policy_id"])],
                session_key=str(binding.get("session_key") or "replay"),
                enabled=bool(binding.get("enabled", True)),
                base_currency=str(binding.get("base_currency") or "USD"),
                strategy_allocations={
                    str(strategy_configuration["strategy_id"]): float(
                        binding.get("strategy_allocation", 1.0)
                    )
                },
                strategy_mandates={
                    str(strategy_configuration["strategy_id"]): next(
                        (
                            dict(row)
                            for row in configuration["portfolio"].get("mandates") or []
                            if str(row.get("account_key")) == str(binding["account_key"])
                        ),
                        {},
                    )
                },
            )
            for binding in bindings
        ]
        groups = [
            PortfolioGroupPolicy(
                group_id=str(row["group_id"]),
                account_keys=tuple(str(value) for value in row.get("account_keys") or ()),
                maximum_gross_exposure=float(row["maximum_gross_exposure"]),
                maximum_ticker_exposure=float(row["maximum_ticker_exposure"]),
            )
            for row in configuration["portfolio"].get("groups") or ()
        ]
        portfolio = PortfolioManagementEngine(
            portfolio_profiles,
            journal=self._journal,
            run_id=self.run_id,
            strategy_id=str(strategy_configuration["strategy_id"]),
            strategy_revision=int(strategy_configuration["revision"]),
            groups=groups,
        )
        broker = SimulatedBrokerAdapter(
            list(self.account_ids),
            SimulationConfig(initial_cash=self.definition.initial_cash),
            mode=TradingMode.REPLAY,
        )
        self._runtime = TradingRuntime(
            RunConfig(
                mode=RunMode.REPLAY,
                strategy_id=str(strategy_configuration["strategy_id"]),
                strategy_revision=int(strategy_configuration["revision"]),
                account_ids=self.account_ids,
                anchor_date=self.definition.session_date,
                run_id=self.run_id,
                checkpoint_interval_events=1_000,
            ),
            broker,
            self._strategy,
            self._journal,
            intent_planner=self._planner,
            portfolio=portfolio,
        )
        await self._runtime.initialize()

    async def _process_market_event(self, event: MarketEvent) -> None:
        if self._runtime is None:
            raise RuntimeError("Replay runtime was not initialized")
        if isinstance(event, QuoteEvent):
            self._quotes[event.ticker] = event
        await self._runtime.process_event(event)

    async def _process_strategy_frame(self, frame: ReplayDerivedFrame) -> None:
        if self._runtime is None or self._strategy is None:
            return
        quote = self._quotes.get(frame.ticker)
        indicator = frame.indicator
        bar = frame.bar
        previous = self._previous_vwap.get((frame.ticker, frame.timeframe))
        current_vwap = _positive(indicator.get("vwap"))
        slope = 0.0
        if previous and current_vwap:
            elapsed = max(0.001, (frame.as_of - previous[0]).total_seconds())
            slope = (current_vwap / previous[1] - 1.0) * 10_000 / elapsed
        if current_vwap:
            self._previous_vwap[(frame.ticker, frame.timeframe)] = (
                frame.as_of,
                current_vwap,
            )
        direction = int(indicator.get("structure_choch_direction") or 0)
        structure_event = "choch" if direction else ""
        if not direction:
            direction = int(indicator.get("structure_bos_direction") or 0)
            structure_event = "bos" if direction else ""
        observed_market_time = frame.as_of.astimezone(NEW_YORK).time()
        base = StrategyObservation(
            ticker=frame.ticker,
            observed_at=frame.as_of,
            price=float(indicator.get("close") or bar.get("close") or 0),
            bid=float(quote.bid_price if quote else 0),
            ask=float(quote.ask_price if quote else 0),
            previous_close=_optional_positive(
                indicator.get("previous_close") or indicator.get("prev_close")
            ),
            previous_high=_optional_positive(indicator.get("previous_high")),
            swing_high=_optional_positive(indicator.get("structure_swing_high")),
            swing_low=_optional_positive(indicator.get("structure_swing_low")),
            structure_event=structure_event,
            structure_direction="bullish" if direction > 0 else "bearish" if direction < 0 else "",
            vwap=current_vwap,
            vwap_slope_bps_per_second=slope,
            macd_line=_optional_number(indicator.get("macd_line")),
            macd_signal=_optional_number(indicator.get("macd_signal")),
            macd_histogram=_optional_number(indicator.get("macd_histogram")),
            qmd_score=float(indicator.get("flow_structure_composite_score") or 0),
            qmd_confidence=float(
                indicator.get("flow_structure_composite_confidence") or 0
            ),
            qmd_bias=str(
                indicator.get("flow_structure_composite_bias") or "neutral"
            ),
            price_volume_expansion_score=float(
                frame.signals.get(f"price_volume_expansion@{frame.timeframe}")
                or indicator.get("price_volume_expansion_score")
                or 0
            ),
            vwap_transition_score=float(
                frame.signals.get(f"vwap_transition@{frame.timeframe}")
                or indicator.get("vwap_transition_score")
                or 0
            ),
            flow_price_divergence_score=float(
                frame.signals.get(f"flow_price_divergence@{frame.timeframe}")
                or indicator.get("flow_price_divergence_score")
                or 0
            ),
            liquidity_dislocation_score=float(
                frame.signals.get(f"liquidity_dislocation@{frame.timeframe}")
                or indicator.get("liquidity_dislocation_score")
                or 0
            ),
            volatility=float(indicator.get("atr_14") or 0),
            upper_luld_price=_optional_positive(indicator.get("structure_luld_upper")),
            market_open=clock_time(9, 30) <= observed_market_time < clock_time(16, 0),
            source_signal_ids=(f"qmd-derived:{frame.ticker}:{frame.timeframe}:{frame.sequence}",),
            source_timeframe=frame.timeframe,
        )
        source_cache = self._strategy_source_values.setdefault(frame.ticker, {})
        source_cache.update(strategy_observation_source_values(base, frame.timeframe))
        base = replace(base, source_values=deepcopy(source_cache))
        for assignment in self._strategy.assignments():
            if assignment.ticker != frame.ticker:
                continue
            positions = await self._runtime.broker.positions(assignment.account_id)
            position = next(
                (row for row in positions if int(row.conid) == assignment.conid),
                None,
            )
            observation = replace(
                base,
                position_quantity=float(position.quantity if position else 0),
                average_price=float(position.avgPrice if position else 0),
                manual_entry_request=bool(
                    assignment.state.get("manual_entry_requested")
                ),
                force_entry=bool(assignment.state.get("force_entry_requested")),
            )
            await self._runtime.process_account_strategy_observation(
                observation,
                assignment.account_id,
            )

    def _remember_strategy_frame(self, frame: ReplayDerivedFrame) -> None:
        current_vwap = _positive(frame.indicator.get("vwap"))
        if current_vwap:
            self._previous_vwap[(frame.ticker, frame.timeframe)] = (
                frame.as_of,
                current_vwap,
            )

    async def _wait_until_active(self) -> None:
        async with self._condition:
            while (
                not self._stop_requested
                and self.status in {"ready", "paused"}
                and self._step_until is None
                and self._fast_forward_until is None
            ):
                await self._condition.wait()

    async def _pace(self, event: MarketEvent) -> None:
        if self.status == "fast_forwarding" or self.speed == 0:
            self._pace_reset = True
            return
        if self._pace_reset or self._pace_event_anchor is None:
            self._pace_event_anchor = event.ts
            self._pace_wall_anchor = time.monotonic()
            self._pace_reset = False
            return
        expected = max(
            0.0,
            (event.ts - self._pace_event_anchor).total_seconds(),
        ) / self.speed
        delay = self._pace_wall_anchor + expected - time.monotonic()
        if delay > 0.002:
            await asyncio.sleep(min(delay, 0.25))

    async def _after_event(self, event_time: datetime) -> None:
        self.current_time = event_time
        self.updated_at = datetime.now(UTC)
        transport_boundary = False
        if self._step_until is not None and event_time >= self._step_until:
            self._step_until = None
            self.status = "paused"
            transport_boundary = True
        if (
            self._fast_forward_until is not None
            and event_time >= self._fast_forward_until
        ):
            self._fast_forward_until = None
            self.status = "paused"
            transport_boundary = True
        await self._publish(force=transport_boundary)
        if transport_boundary:
            self._write_manifest()

    async def _finish(self, status: str) -> None:
        if self._runtime is not None and not self._runtime_finished:
            await self._runtime.finish(status=status)
            self._runtime_finished = True
        self.status = status
        self.updated_at = datetime.now(UTC)
        await self._publish(force=True)
        self._write_manifest()

    async def _publish(self, *, force: bool = False) -> None:
        now = time.monotonic()
        if not force and now - self._last_publish_monotonic < 0.1:
            return
        self._last_publish_monotonic = now
        payload = self.snapshot()
        for queue in tuple(self._subscribers):
            if queue.full():
                try:
                    queue.get_nowait()
                except asyncio.QueueEmpty:
                    pass
            queue.put_nowait(payload)

    def _selected_assignments(self) -> list[dict[str, Any]]:
        rows = [
            dict(row)
            for row in self.definition.configuration_revision["payload"]["assignments"]
            if str(row.get("status") or "") not in {"disabled", "completed", "error"}
        ]
        if self.definition.assignment_ids:
            selected = set(self.definition.assignment_ids)
            rows = [row for row in rows if str(row.get("assignment_id")) in selected]
            missing = selected - {str(row.get("assignment_id")) for row in rows}
            if missing:
                raise ValueError(
                    f"Replay assignments are unavailable or inactive: {', '.join(sorted(missing))}"
                )
        return rows

    def _resolved_tickers(self) -> tuple[str, ...]:
        assignment_tickers = (
            [assignment.ticker for assignment in self._strategy.assignments()]
            if self._strategy is not None
            else []
        )
        configuration = self.definition.configuration_revision["payload"]
        universe_tickers = [
            _ticker(symbol)
            for universe in configuration.get("universes") or []
            if bool(universe.get("enabled", True))
            and str(universe.get("source") or "") == "configured_symbols"
            for symbol in universe.get("symbols") or []
            if str(symbol or "").strip()
        ]
        canvas_tickers = _canvas_profile_tickers(
            dict(dict(configuration.get("canvas") or {}).get("profile") or {})
        )
        tickers = tuple(
            dict.fromkeys(
                [
                    *assignment_tickers,
                    *universe_tickers,
                    *sorted(canvas_tickers),
                    *(_ticker(value) for value in self.definition.tickers),
                ]
            )
        )
        if not tickers:
            raise ValueError(
                "Replay requires at least one configured Canvas symbol or strategy assignment"
            )
        return tickers

    async def _load_strategy_frames(self) -> list[ReplayDerivedFrame]:
        if self._strategy is None:
            return []
        requests = {
            (assignment.ticker, timeframe)
            for assignment in self._strategy.assignments()
            for timeframe in entry_rule_timeframes(assignment.parameters)
        }
        if not requests:
            return []
        groups = await asyncio.gather(
            *(
                _historical_derived_frames(
                    ticker=ticker,
                    timeframe=timeframe,
                    start=self.definition.session_start,
                    end=self.definition.session_end,
                )
                for ticker, timeframe in sorted(requests)
            )
        )
        signal_events = await asyncio.gather(
            *(
                _historical_signal_events(
                    ticker=ticker,
                    start=self.definition.session_start,
                    end=self.definition.session_end,
                )
                for ticker in sorted({ticker for ticker, _ in requests})
            )
        )
        events_by_ticker = {
            ticker: events
            for ticker, events in zip(
                sorted({ticker for ticker, _ in requests}),
                signal_events,
                strict=True,
            )
        }
        frames = [frame for group in groups for frame in group]
        for ticker in events_by_ticker:
            _attach_historical_signals(
                [frame for frame in frames if frame.ticker == ticker],
                events_by_ticker[ticker],
            )
        return sorted(
            frames,
            key=lambda frame: (frame.as_of, frame.ticker, frame.timeframe, frame.sequence),
        )

    def _write_manifest(self) -> None:
        if not self.run_dir.exists():
            return
        payload = {
            "schema_version": 1,
            "run": self.snapshot(),
            "definition": self.definition.payload(),
            "approved_configuration": self.definition.configuration_revision,
            "runtime_root": str(self.runtime_root),
            "journal_path": str(self.run_dir / "journal.sqlite3"),
        }
        target = self.run_dir / "manifest.json"
        temporary = self.run_dir / "manifest.json.tmp"
        temporary.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
        temporary.replace(target)


class ReplayRunService:
    def __init__(self, runtime_root: Path | None = None) -> None:
        self.runtime_root = (runtime_root or replay_runtime_root()).resolve()
        self._runs: dict[str, ReplayRunController] = {}
        self._lock = asyncio.Lock()

    async def create(self, definition: ReplayRunDefinition) -> ReplayRunController:
        controller = ReplayRunController(definition, runtime_root=self.runtime_root)
        async with self._lock:
            self._runs[controller.run_id] = controller
        await controller.start()
        return controller

    def get(self, run_id: str) -> ReplayRunController:
        normalized = str(run_id or "").strip()
        if not re.fullmatch(r"[0-9a-fA-F-]{36}", normalized):
            raise KeyError(run_id)
        controller = self._runs.get(normalized)
        if controller is None:
            raise KeyError(run_id)
        return controller

    def list(self) -> list[dict[str, Any]]:
        return [
            controller.snapshot()
            for controller in sorted(
                self._runs.values(),
                key=lambda item: item.created_at,
                reverse=True,
            )
        ]


def replay_runtime_root() -> Path:
    configured = os.environ.get("TRADING_REPLAY_ROOT", "").strip()
    if configured:
        return Path(configured)
    trading_root = Path(
        os.environ.get("TRADING_RUNTIME_ROOT", str(DEFAULT_REPLAY_ROOT.parent))
    )
    return trading_root / "replay"


def replay_preflight(
    *,
    session_date: date,
    start_time: clock_time,
    initial_cash: float,
    assignment_ids: tuple[str, ...] = (),
    tickers: tuple[str, ...] = (),
    configuration_revision: dict[str, Any] | None = None,
) -> dict[str, Any]:
    approved = configuration_revision or replay_configuration_snapshot()
    configuration = approved["payload"]
    canvas = dict(configuration["canvas"])
    configured_canvas_tickers = _canvas_profile_tickers(dict(canvas.get("profile") or {}))
    definition = ReplayRunDefinition(
        session_date=session_date,
        start_time=start_time,
        initial_cash=initial_cash,
        assignment_ids=assignment_ids,
        tickers=tickers,
        configuration_revision=approved,
    )
    gateway = historical_gateway_snapshot()
    coverage: dict[str, Any] = {}
    coverage_error = ""
    if gateway.get("ready"):
        try:
            coverage = historical_day_coverage(session_date)
        except Exception as exc:
            coverage_error = str(exc)
    assignments = [
        dict(row)
        for row in configuration["assignments"]
        if str(row.get("status") or "") not in {"disabled", "completed", "error"}
    ]
    if assignment_ids:
        selected = set(assignment_ids)
        assignments = [
            row for row in assignments if str(row.get("assignment_id")) in selected
        ]
    assignment_tickers = {
        str(row.get("ticker") or "").strip().upper() for row in assignments
    }
    universe_tickers = {
        _ticker(symbol)
        for universe in configuration.get("universes") or []
        if bool(universe.get("enabled", True))
        and str(universe.get("source") or "") == "configured_symbols"
        for symbol in universe.get("symbols") or []
        if str(symbol or "").strip()
    }
    resolved_tickers = sorted(
        {
            *assignment_tickers,
            *universe_tickers,
            *configured_canvas_tickers,
            *(_ticker(value) for value in tickers),
        }
    )
    storage_ready = False
    storage_evidence = str(replay_runtime_root())
    try:
        root = replay_runtime_root()
        root.mkdir(parents=True, exist_ok=True)
        probe = root / f".replay-preflight-{uuid4().hex}.tmp"
        probe.write_text("ready", encoding="utf-8")
        probe.unlink()
        storage_ready = True
    except Exception as exc:
        storage_evidence = str(exc)
    event_count = int(coverage.get("event_count") or 0)
    ticker_count = int(coverage.get("ticker_count") or 0)
    checks = [
        _check(
            "approved_configuration",
            "Approved configuration",
            bool(approved.get("revision_id") and approved.get("content_hash")),
            f"Revision {approved.get('revision')} · {approved.get('label')} is pinned for the full run."
            if approved.get("revision_id")
            else "No approved configuration revision is available.",
            str(approved.get("content_hash") or ""),
        ),
        _check(
            "historical_source",
            "QMD History",
            bool(gateway.get("ready")),
            "Historical source identity and readiness verified."
            if gateway.get("ready")
            else "QMD History is unavailable or returned the wrong service identity.",
            str(gateway.get("base_url") or ""),
        ),
        _check(
            "canonical_coverage",
            "Canonical event coverage",
            event_count > 0 and ticker_count > 0 and not coverage_error,
            f"{event_count:,} events across {ticker_count:,} symbols cover the selected session."
            if event_count > 0 and ticker_count > 0 and not coverage_error
            else coverage_error or "No canonical events cover the selected session.",
            str(coverage.get("coverage_table") or "QMD History coverage"),
        ),
        _check(
            "runtime_storage",
            "Replay runtime storage",
            storage_ready,
            "The run manifest and journal can be written outside the repository."
            if storage_ready
            else "The configured Replay runtime root is not writable.",
            storage_evidence,
        ),
        _check(
            "configured_symbols",
            "Replay symbols",
            bool(resolved_tickers),
            f"{len(resolved_tickers)} configured symbol(s): {', '.join(resolved_tickers[:8])}"
            if resolved_tickers
            else "No Canvas symbol or active strategy assignment is available.",
            "Approved Canvas link contexts plus approved strategy assignments",
        ),
        _check(
            "strategy_assignments",
            "Strategy assignments",
            not assignment_ids
            or len(assignments) == len(set(assignment_ids)),
            f"{len(assignments)} active assignment(s) will be cloned into explicit simulated accounts."
            if assignments
            else "No automatic assignment is selected; Replay remains available for market inspection.",
            ", ".join(
                f"{row.get('ticker')}@{row.get('account_key')}" for row in assignments
            )
            or "Optional for market-only Replay",
            required=bool(assignment_ids),
        ),
    ]
    ready = all(check["status"] == "ready" for check in checks if check["required"])
    bindings = [
        dict(row)
        for row in configuration["accounts"]["bindings"]
        if bool(row.get("enabled", True)) and "replay" in list(row.get("modes") or [])
    ]
    account_map = {
        str(row.get("source_account_id") or row["account_key"]):
            f"SIM-{index + 1:02d}-{_slug(str(row['account_key']))}"
        for index, row in enumerate(bindings)
    }
    return {
        "schema_version": 1,
        "ready": ready,
        "definition": definition.payload(),
        "checks": checks,
        "coverage": coverage,
        "gateway": gateway,
        "assignments": assignments,
        "account_mapping": account_map,
        "tickers": resolved_tickers,
        "configuration_revision_id": approved["revision_id"],
        "configuration_revision": approved["revision"],
        "configuration_label": approved["label"],
        "configuration_content_hash": approved["content_hash"],
        "canvas_revision": canvas["revision"],
        "canvas_profile": canvas["profile"],
    }


async def _historical_derived_frames(
    *,
    ticker: str,
    timeframe: str,
    start: datetime,
    end: datetime,
) -> list[ReplayDerivedFrame]:
    parsed = urllib.parse.urlsplit(historical_gateway_base_url())
    scheme = "wss" if parsed.scheme == "https" else "ws"
    path = f"{parsed.path.rstrip('/')}/stream/derived/{urllib.parse.quote(ticker)}"
    query = urllib.parse.urlencode(
        {
            "start": start.isoformat(),
            "end": end.isoformat(),
            "timeframe": timeframe,
            "emit": "full",
            "as_of": end.isoformat(),
            "updates_per_second": 0,
        }
    )
    url = urllib.parse.urlunsplit((scheme, parsed.netloc, path, query, ""))
    async with websockets.connect(
        url,
        ping_interval=20,
        ping_timeout=20,
        max_size=64 * 1024 * 1024,
    ) as socket:
        message = await socket.recv()
    payload = json.loads(message.decode("utf-8") if isinstance(message, bytes) else message)
    if payload.get("error"):
        raise RuntimeError(f"QMD derived stream failed for {ticker}: {payload['error']}")
    bars = list(payload.get("bars") or [])
    indicators = list(payload.get("indicators") or [])
    if len(bars) != len(indicators):
        raise RuntimeError(
            f"QMD derived stream misaligned bars and indicators for {ticker} {timeframe}"
        )
    return [
        ReplayDerivedFrame(
            as_of=_aware_datetime(
                indicator.get("bar_end") or bar.get("bar_end") or payload.get("as_of")
            ),
            bar=bar,
            indicator=indicator,
            sequence=index,
            ticker=_ticker(indicator.get("sym") or bar.get("sym") or ticker),
            timeframe=str(indicator.get("timeframe") or bar.get("timeframe") or timeframe),
        )
        for index, (bar, indicator) in enumerate(zip(bars, indicators, strict=True), start=1)
    ]


async def _historical_signal_events(
    *,
    ticker: str,
    start: datetime,
    end: datetime,
) -> list[dict[str, Any]]:
    query = urllib.parse.urlencode(
        {
            "as_of": end.isoformat(),
            "end": end.isoformat(),
            "start": start.isoformat(),
            "tickers": ticker,
        }
    )
    url = f"{historical_gateway_base_url().rstrip('/')}/snapshot/scanner-derived?{query}"

    def fetch() -> dict[str, Any]:
        with urllib.request.urlopen(url, timeout=120) as response:
            return json.loads(response.read().decode("utf-8"))

    payload = await asyncio.to_thread(fetch)
    if payload.get("error"):
        raise RuntimeError(f"QMD historical signal stream failed for {ticker}: {payload['error']}")
    return sorted(
        [
            dict(row)
            for row in payload.get("recent_signal_events") or []
            if str(row.get("ticker") or "").upper() == ticker.upper()
        ],
        key=lambda row: _aware_datetime(row.get("effective_at") or row.get("observed_at")),
    )


def _attach_historical_signals(
    frames: list[ReplayDerivedFrame],
    events: list[dict[str, Any]],
) -> None:
    active: dict[str, float] = {}
    event_index = 0
    ordered_frames = sorted(
        frames,
        key=lambda frame: (frame.as_of, frame.timeframe, frame.sequence),
    )
    for frame in ordered_frames:
        while event_index < len(events):
            event = events[event_index]
            effective_at = _aware_datetime(
                event.get("effective_at") or event.get("observed_at")
            )
            if effective_at > frame.as_of:
                break
            signal_key = str(event.get("signal_key") or "")
            working_timeframe = str(event.get("working_timeframe") or "")
            key = f"{signal_key}@{working_timeframe}" if signal_key and working_timeframe else ""
            if key:
                if str(event.get("state") or "") == "resolved":
                    active.pop(key, None)
                else:
                    active[key] = float(event.get("score") or 0)
            event_index += 1
        frame.signals = dict(active)


def _assignment_from_payload(
    payload: dict[str, Any],
    *,
    account_id: str,
    source: str,
    configuration: dict[str, Any],
) -> StrategyAssignment:
    strategy = {
        **dict(configuration["strategy"]),
        "strategy_id": str(
            payload.get("strategy_id")
            or dict(configuration["strategy"]).get("strategy_id")
            or ""
        ),
        "revision": int(
            payload.get("strategy_revision")
            or dict(configuration["strategy"]).get("revision")
            or 0
        ),
        "profile_id": str(
            payload.get("profile_id")
            or dict(configuration["strategy"]).get("profile_id")
            or ""
        ),
    }
    deployment = {
        **dict(configuration.get("deployment") or {}),
        "deployment_id": str(
            payload.get("deployment_id")
            or dict(configuration.get("deployment") or {}).get("deployment_id")
            or ""
        ),
        "book_id": str(
            payload.get("book_id")
            or dict(configuration.get("deployment") or {}).get("book_id")
            or "default"
        ),
        "universe_id": str(
            payload.get("universe_id")
            or dict(configuration.get("deployment") or {}).get("universe_id")
            or ""
        ),
    }
    ticker = _ticker(payload["ticker"])
    campaign_id = str(
        payload.get("campaign_id")
        or f"{deployment.get('deployment_id') or 'deployment'}:{ticker}"
    )
    state = campaign_state(
        campaign_id=f"replay:{campaign_id}",
        deployment_id=str(deployment.get("deployment_id") or ""),
        profile_id=str(strategy.get("profile_id") or ""),
        book_id=str(deployment.get("book_id") or "default"),
        universe_id=str(deployment.get("universe_id") or ""),
    )
    state["campaign_policy"] = deepcopy(
        dict(payload.get("campaign_policy") or configuration.get("campaign_policy") or {})
    )
    return StrategyAssignment(
        assignment_id=f"replay-{payload['assignment_id']}",
        strategy_id=str(strategy["strategy_id"]),
        strategy_revision=int(strategy["revision"]),
        account_id=account_id,
        ticker=ticker,
        conid=int(payload["conid"]),
        status=AssignmentStatus(str(payload["status"])),
        permissions=StrategyPermissions(**dict(payload.get("permissions") or {})),
        parameters=(
            dict(payload["resolved_parameters"])
            if isinstance(payload.get("resolved_parameters"), dict)
            else merged_assignment_parameters(configuration, payload)
        ),
        state=state,
        source=source,
        created_at=_aware_datetime(payload.get("created_at")),
        updated_at=_aware_datetime(payload.get("updated_at")),
    )


def _canvas_profile_tickers(profile: dict[str, Any]) -> set[str]:
    tickers: set[str] = set()
    active_instances = {
        str(instance_id)
        for state in dict(profile.get("workspaceStates") or {}).values()
        for instance_id in dict(state.get("instances") or {})
    }

    def visit(value: Any) -> None:
        if isinstance(value, dict):
            for key, item in value.items():
                if key == "symbol" and str(item or "").strip():
                    tickers.add(_ticker(item))
                else:
                    visit(item)
        elif isinstance(value, list):
            for item in value:
                visit(item)

    link_assignments = dict(profile.get("linkAssignments") or {})
    link_contexts = dict(profile.get("linkContexts") or {})
    instance_settings = dict(profile.get("instanceSettings") or {})
    for instance_id in active_instances:
        group = str(link_assignments.get(instance_id) or "")
        if group:
            visit(link_contexts.get(group) or {})
        visit(instance_settings.get(instance_id) or {})
    return tickers


def _check(
    check_id: str,
    label: str,
    ready: bool,
    summary: str,
    evidence: str,
    *,
    required: bool = True,
) -> dict[str, Any]:
    return {
        "id": check_id,
        "label": label,
        "status": "ready" if ready else "blocked",
        "summary": summary,
        "evidence": evidence,
        "required": required,
    }


def _ticker(value: Any) -> str:
    ticker = str(value or "").strip().upper()
    if not re.fullmatch(r"[A-Z][A-Z0-9.\-]{0,9}", ticker):
        raise ValueError(f"Invalid Replay ticker: {value}")
    return ticker


def _slug(value: str) -> str:
    normalized = re.sub(r"[^A-Za-z0-9]+", "-", value).strip("-").upper()
    return normalized[:24] or hashlib.sha256(value.encode("utf-8")).hexdigest()[:12].upper()


def _aware_datetime(value: Any) -> datetime:
    if isinstance(value, datetime):
        parsed = value
    elif value:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    else:
        parsed = datetime.now(UTC)
    if parsed.tzinfo is None:
        raise ValueError("Replay timestamps must include an explicit timezone")
    return parsed


def _optional_number(value: Any) -> float | None:
    if value in (None, ""):
        return None
    return float(value)


def _positive(value: Any) -> float:
    number = float(value or 0)
    return number if number > 0 else 0.0


def _optional_positive(value: Any) -> float | None:
    number = _positive(value)
    return number or None
