from __future__ import annotations

import asyncio
import hashlib
import json
import os
import re
import time
import urllib.parse
from copy import deepcopy
from dataclasses import dataclass, field, replace
from datetime import UTC, date, datetime, time as clock_time, timedelta
from pathlib import Path
from typing import Any, Callable
from uuid import uuid4
from zoneinfo import ZoneInfo

import websockets

from src.backend.canonical_trading_service import trading_state_payload
from src.backend.qmd_gateway_client import (
    QmdProductRequest,
    qmd_history_websocket_url,
    qmd_product_request,
)
from src.backend.trading_runtime_service import (
    historical_day_coverage,
    historical_gateway_base_url,
    historical_gateway_snapshot,
    historical_preflight,
)
from src.backend.trading_configuration_service import (
    merged_assignment_parameters,
    replay_configuration_snapshot,
)
from src.market_engine.events import MarketEvent, QuoteEvent, TradeEvent
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
from src.trading_runtime.signals import StrategyIntent
from src.trading_runtime.strategy_engine import (
    AssignmentStatus,
    AssignedLongMomentumStrategy,
    StrategyAssignment,
    StrategyObservation,
    StrategyPermissions,
    strategy_rule_timeframes,
    strategy_input_catalog,
    strategy_observation_source_values,
)
from src.trading_runtime.strategy_orders import RuntimeIbkrStrategyOrderPlanner
from src.trading_runtime.strategy_campaign import campaign_state


NEW_YORK = ZoneInfo("America/New_York")
DEFAULT_REPLAY_ROOT = Path(r"D:\TradingML\runtimes\trading\replay")
DEFAULT_BACKTEST_ROOT = Path(r"D:\TradingML\runtimes\trading\backtest")
DEFAULT_BACKTEST_DEBUG_ROOT = Path(r"D:\TradingML\runtimes\trading\backtest_debug")
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
DEFAULT_MAX_RESIDENT_RUNS = 32
DEFAULT_HISTORY_FETCH_CONCURRENCY = 8
MAX_DEBUG_FIXTURE_EVENTS = 20_000


class ReplayRunCapacityError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class HistoricalDebugFixture:
    fixture_id: str
    market_events: tuple[dict[str, Any], ...] = ()
    derived_frames: tuple[dict[str, Any], ...] = ()

    def __post_init__(self) -> None:
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}", self.fixture_id):
            raise ValueError("Debug fixture_id must be a stable 1-128 character identifier")
        if not self.market_events and not self.derived_frames:
            raise ValueError("Debug fixture requires market_events or derived_frames")
        if len(self.market_events) + len(self.derived_frames) > MAX_DEBUG_FIXTURE_EVENTS:
            raise ValueError(
                f"Debug fixture supports at most {MAX_DEBUG_FIXTURE_EVENTS:,} records"
            )

    @property
    def content_hash(self) -> str:
        canonical = json.dumps(
            {
                "fixture_id": self.fixture_id,
                "market_events": self.market_events,
                "derived_frames": self.derived_frames,
            },
            separators=(",", ":"),
            sort_keys=True,
            default=str,
        )
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()

    def payload(self, *, include_records: bool = False) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "fixture_id": self.fixture_id,
            "content_hash": self.content_hash,
            "market_event_count": len(self.market_events),
            "derived_frame_count": len(self.derived_frames),
        }
        if include_records:
            payload["market_events"] = [dict(row) for row in self.market_events]
            payload["derived_frames"] = [dict(row) for row in self.derived_frames]
        return payload


@dataclass(frozen=True, slots=True)
class ReplayRunDefinition:
    session_date: date
    start_time: clock_time
    initial_cash: float = 100_000.0
    assignment_ids: tuple[str, ...] = ()
    tickers: tuple[str, ...] = ()
    configuration_revision: dict[str, Any] = field(default_factory=dict)
    mode: RunMode = RunMode.REPLAY
    final_session_date: date | None = None
    debug_fixture: HistoricalDebugFixture | None = None

    def __post_init__(self) -> None:
        if self.mode not in {RunMode.REPLAY, RunMode.BACKTEST, RunMode.BACKTEST_DEBUG}:
            raise ValueError("Historical controller mode must be replay, backtest, or backtest_debug")
        if self.mode == RunMode.BACKTEST_DEBUG and self.debug_fixture is None:
            raise ValueError("Backtest Debug requires a deterministic fixture")
        if self.mode != RunMode.BACKTEST_DEBUG and self.debug_fixture is not None:
            raise ValueError("Debug fixtures may only be used by Backtest Debug")
        if self.mode == RunMode.REPLAY and self.final_session_date not in {None, self.session_date}:
            raise ValueError("Replay is limited to one exchange session")
        if self.final_session_date is not None and self.final_session_date < self.session_date:
            raise ValueError("Historical final session cannot precede the first session")
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
        if self.debug_fixture is not None:
            fixture_times = [
                *(_debug_time(row.get("ts")) for row in self.debug_fixture.market_events),
                *(_debug_time(row.get("as_of")) for row in self.debug_fixture.derived_frames),
            ]
            if any(
                event_time < self.session_start or event_time > self.session_end
                for event_time in fixture_times
            ):
                raise ValueError("Debug fixture records must stay inside the configured session")

    @property
    def session_start(self) -> datetime:
        return datetime.combine(self.session_date, clock_time(4, 0), tzinfo=NEW_YORK)

    @property
    def session_end(self) -> datetime:
        return datetime.combine(
            self.final_session_date or self.session_date,
            clock_time(20, 0),
            tzinfo=NEW_YORK,
        )

    @property
    def requested_start(self) -> datetime:
        return datetime.combine(self.session_date, self.start_time, tzinfo=NEW_YORK)

    def payload(self) -> dict[str, Any]:
        approved = self.configuration_revision
        configuration = dict(approved.get("payload") or {})
        canvas = dict(configuration.get("canvas") or {})
        return {
            "mode": self.mode.value,
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
            "debug_fixture": (
                self.debug_fixture.payload() if self.debug_fixture is not None else None
            ),
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
        if self.definition.mode in {RunMode.BACKTEST, RunMode.BACKTEST_DEBUG}:
            self.speed = 0.0
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
        self._historical_watchlist_cache: list[dict[str, Any]] | None = None
        self._historical_watchlist_timeline_cache: list[dict[str, Any]] | None = None
        self._historical_watchlist_timeline_index = 0
        self._active_historical_watchlist_tickers: set[str] = set()
        self._data_authority: dict[str, dict[str, Any]] = {}
        if self.definition.debug_fixture is not None:
            self._data_authority["market_events"] = {
                "authority": "backtest_debug_fixture",
                "fixture_id": self.definition.debug_fixture.fixture_id,
                "revision_token": self.definition.debug_fixture.content_hash,
                "source_plan_hash": self.definition.debug_fixture.content_hash,
            }

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

    async def submit_trade_proposal(self, payload: dict[str, Any]) -> dict[str, Any]:
        if self._runtime is None or self._planner is None:
            raise ValueError("Historical trading runtime is not ready")
        account_id = str(payload.get("account_id") or "").strip()
        if account_id not in self.account_ids:
            raise ValueError("Trade proposal account is outside this simulated run")
        ticker = _ticker(payload.get("ticker"))
        conid = int(payload.get("conid") or 0)
        if conid <= 0:
            raise ValueError("Trade proposal requires a positive point-in-time conid")
        market = dict(payload.get("market_snapshot") or {})
        observed_at = _aware_datetime(market.get("observed_at"))
        event_time = self.current_time or self.definition.requested_start
        if observed_at > event_time:
            raise ValueError("Trade proposal market snapshot is ahead of the run clock")
        if str(market.get("freshness") or "") != "ready":
            raise ValueError("Trade proposal requires a ready market snapshot")
        reference_price = float(market.get("reference_price") or 0)
        if reference_price <= 0:
            raise ValueError("Trade proposal requires a positive reference price")
        quantity = float(payload.get("quantity") or 0)
        if quantity <= 0:
            raise ValueError("Trade proposal quantity must be positive")
        action = str(payload.get("action") or "enter_long")
        if action not in {
            "enter_long", "add_long", "reduce_long", "take_profit", "exit",
            "enter_short", "add_short", "reduce_short", "cover",
        }:
            raise ValueError("Trade proposal action is unsupported")
        authority = str(payload.get("authority") or "manual")
        proposal_id = str(payload.get("proposal_id") or uuid4())
        self._planner.upsert_instrument(
            InstrumentContract(
                instrument_id=f"simulated:{conid}",
                conid=conid,
                symbol=ticker,
                security_type="STK",
                currency=str(payload.get("currency") or "USD"),
                exchange=str(payload.get("exchange") or "SMART"),
            )
        )
        intent = StrategyIntent(
            intent_id=f"proposal:{proposal_id}",
            ticker=ticker,
            event_time=event_time,
            action=action,
            quantity=quantity,
            reference_price=reference_price,
            invalidation_price=_optional_positive(payload.get("invalidation_price")),
            profit_target_price=_optional_positive(payload.get("profit_target_price")),
            trailing_amount=_optional_positive(payload.get("trailing_amount")),
            urgency=str(payload.get("urgency") or "aggressive_limit"),
            reason=str(payload.get("reason") or "Canvas trade proposal"),
            metadata={
                "origin": "canvas_trade_proposal",
                "proposal_id": proposal_id,
                "proposal_authority": authority,
                "market_snapshot": market,
                "identity_revision": str(payload.get("identity_revision") or ""),
                "bid": float(market.get("bid") or 0),
                "ask": float(market.get("ask") or 0),
                "tick_size": float(market.get("tick_size") or 0.01),
                "quote_observed_at": observed_at,
            },
        )
        result = await self._runtime.submit_external_intent(
            intent,
            account_id=account_id,
            proposal_id=proposal_id,
            proposal_authority=authority,
        )
        await self._publish(force=True)
        self._write_manifest()
        return {
            "schema_version": 1,
            "mode": self.definition.mode.value,
            "run_id": self.run_id,
            "proposal": {
                "proposal_id": proposal_id,
                "authority": authority,
                "account_id": account_id,
                "ticker": ticker,
                "conid": conid,
                "action": action,
                "quantity": quantity,
                "event_time": event_time.isoformat(),
                "market_snapshot": market,
                "invalidation_price": intent.invalidation_price,
                "profit_target_price": intent.profit_target_price,
            },
            **result,
        }

    @property
    def account_ids(self) -> tuple[str, ...]:
        values = tuple(dict.fromkeys(self._account_map.values()))
        return values or ("SIM-REPLAY",)

    def snapshot(self) -> dict[str, Any]:
        current = self.current_time or self.definition.session_start
        checkpoint = self._checkpoint_projection()
        duration = max(
            1.0,
            (self.definition.session_end - self.definition.requested_start).total_seconds(),
        )
        elapsed = max(0.0, (current - self.definition.requested_start).total_seconds())
        return {
            "schema_version": 1,
            "mode": self.definition.mode.value,
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
            "checkpoint": checkpoint,
            "progress": min(1.0, elapsed / duration),
            "account_ids": list(self.account_ids),
            "account_mapping": dict(self._account_map),
            "assignments": (
                [assignment.payload() for assignment in self._strategy.assignments()]
                if self._strategy is not None
                else []
            ),
            "historical_watchlists": {
                "active_tickers": sorted(self._active_historical_watchlist_tickers),
                "timeline_index": self._historical_watchlist_timeline_index,
                "timeline_count": len(self._historical_watchlist_timeline_cache or ()),
            },
            "data_authority": {
                "configuration": {
                    "revision_id": self.definition.configuration_revision.get("revision_id", ""),
                    "revision": self.definition.configuration_revision.get("revision", 0),
                    "content_hash": self.definition.configuration_revision.get("content_hash", ""),
                },
                "sources": deepcopy(self._data_authority),
            },
            **{
                key: value
                for key, value in self.definition.payload().items()
                if key.startswith("configuration_") or key.startswith("canvas_")
            },
            "tickers": list(self.definition.tickers),
            "debug_fixture": (
                self.definition.debug_fixture.payload()
                if self.definition.debug_fixture is not None
                else None
            ),
        }

    def _checkpoint_projection(self) -> dict[str, Any]:
        persisted = (
            self._journal.load_checkpoint(self.run_id)
            if self._journal is not None
            else None
        )
        if persisted is None:
            return {
                "status": "pending",
                "cursor": "",
                "event_time": None,
                "updated_at": None,
                "processed_events": 0,
                "interval_events": 1_000,
                "resume_supported": False,
            }
        state = dict(persisted.get("state") or {})
        return {
            "status": "available",
            "cursor": str(persisted.get("cursor") or ""),
            "event_time": persisted.get("event_time"),
            "updated_at": persisted.get("updated_at"),
            "processed_events": int(state.get("processed_events") or 0),
            "interval_events": (
                self._runtime.config.checkpoint_interval_events
                if self._runtime is not None
                else 1_000
            ),
            "resume_supported": False,
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
            "runtime_mode": self.definition.mode.value,
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
            "historical_source": f"{self.definition.mode.value}_run_journal_only",
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
            "preview_kind": f"{self.definition.mode.value}_run",
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
            async for events in self._market_event_batches():
                for event_index, event in enumerate(events, start=1):
                    if self._stop_requested:
                        await self._finish("stopped")
                        return
                    if event.ts >= self.definition.requested_start:
                        if self.status == "warming":
                            self.status = (
                                "running"
                                if self.definition.mode
                                in {RunMode.BACKTEST, RunMode.BACKTEST_DEBUG}
                                else "ready"
                            )
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
                            self._apply_historical_watchlist_membership(frame.as_of)
                            await self._wait_until_active()
                            if self._stop_requested:
                                await self._finish("stopped")
                                return
                            await self._process_strategy_frame(frame)
                            await self._after_event(frame.as_of)
                        frame_index += 1
                    if event.ts >= self.definition.requested_start:
                        self._apply_historical_watchlist_membership(event.ts)
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
            if (
                frame_index < len(frames)
                and self.status == "warming"
                and self.definition.mode == RunMode.BACKTEST_DEBUG
            ):
                self.status = "running"
                self.current_time = self.definition.requested_start
                self.updated_at = datetime.now(UTC)
                await self._publish(force=True)
                self._write_manifest()
            while frame_index < len(frames):
                frame = frames[frame_index]
                if frame.as_of >= self.definition.requested_start:
                    self._apply_historical_watchlist_membership(frame.as_of)
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

    async def _market_event_batches(self):
        if self.definition.mode == RunMode.BACKTEST_DEBUG:
            fixture = self.definition.debug_fixture
            if fixture is None:
                raise RuntimeError("Backtest Debug fixture disappeared before execution")
            events = _debug_market_events(fixture.market_events)
            for offset in range(0, len(events), 1_000):
                yield events[offset : offset + 1_000]
            return
        source = QmdHistoricalEventSource(
            historical_gateway_base_url(),
            start=self.definition.session_start,
            end=self.definition.session_end,
            tickers=list(self._stream_tickers),
            batch_size=1_000,
        )
        await source.health()
        async for batch in source.stream():
            if source.source_revision is None:
                raise RuntimeError("QMD historical event source omitted pinned authority")
            self._record_data_authority(
                "market_events",
                {
                    "authority": "qmd_history_events",
                    **source.source_revision,
                },
            )
            yield batch.events

    async def _initialize_runtime(self) -> None:
        configuration = self.definition.configuration_revision["payload"]
        strategy_configuration = dict(configuration["strategy"])
        source_assignments = self._selected_assignments()
        bindings = [
            dict(row)
            for row in configuration["accounts"]["bindings"]
            if bool(row.get("enabled", True))
            and self.definition.mode.value in list(row.get("modes") or [])
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
                source=f"{self.definition.mode.value}:{row.get('source') or 'configured'}",
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
                mode=self.definition.mode.value,
                account_class=str(binding.get("account_class") or "simulated"),
                policy=policies[str(binding["portfolio_policy_id"])],
                session_key=str(binding.get("session_key") or self.definition.mode.value),
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
            mode=TradingMode(self.definition.mode.value),
        )
        self._runtime = TradingRuntime(
            RunConfig(
                mode=self.definition.mode,
                strategy_id=str(strategy_configuration["strategy_id"]),
                strategy_revision=int(strategy_configuration["revision"]),
                account_ids=self.account_ids,
                anchor_date=self.definition.session_date,
                run_id=self.run_id,
                run_plan_id=str(
                    dict(configuration.get("run_plan") or {}).get("run_plan_id")
                    or dict(configuration.get("deployment") or {}).get("deployment_id")
                    or ""
                ),
                safety_supervisor_enabled=bool(
                    dict(
                        dict(
                            dict(configuration.get("run_plan") or {}).get(
                                "safety_supervisor"
                            )
                            or {}
                        ).get("enabled_by_environment")
                        or {}
                    ).get(self.definition.mode.value, True)
                ),
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
        changed_source_values = strategy_observation_source_values(base, frame.timeframe)
        source_cache.update(changed_source_values)
        evaluation_events = ["indicator_update", "bar_close"]
        changed_source_ids = [
            source_key
            for source_key in changed_source_values
            if not source_key.startswith("signal.")
        ]
        if frame.signals:
            evaluation_events.append("signal_event")
            for source in strategy_input_catalog():
                source_id = str(source["source_id"])
                if not source_id.startswith("signal."):
                    continue
                runtime_field = str(source["runtime_field"])
                signal_key = runtime_field.removesuffix("_score")
                if f"{signal_key}@{frame.timeframe}" in frame.signals:
                    changed_source_ids.append(f"{source_id}@{frame.timeframe}")
        base = replace(
            base,
            changed_source_ids=tuple(changed_source_ids),
            evaluation_events=tuple(evaluation_events),
            source_values=deepcopy(source_cache),
        )
        for assignment in self._strategy.assignments():
            if assignment.ticker != frame.ticker:
                continue
            positions = await self._runtime.broker.positions(assignment.account_id)
            position = next(
                (row for row in positions if int(row.conid) == assignment.conid),
                None,
            )
            if (
                "historical_watchlist" in assignment.source
                and assignment.ticker not in self._active_historical_watchlist_tickers
                and (position is None or float(position.quantity) == 0)
            ):
                continue
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
        configuration = self.definition.configuration_revision["payload"]
        account_keys = [
            str(row.get("account_key") or "")
            for row in dict(configuration.get("accounts") or {}).get("bindings") or []
            if bool(row.get("enabled", True))
            and self.definition.mode.value in set(row.get("modes") or [])
        ]
        allowed_account_keys = set(account_keys)
        rows = [
            dict(row)
            for row in self.definition.configuration_revision["payload"]["assignments"]
            if str(row.get("status") or "") not in {"disabled", "completed", "error"}
            and str(row.get("account_key") or "") in allowed_account_keys
        ]
        historical_members = self._historical_watchlist_members()
        existing = {
            (str(row.get("account_key") or ""), str(row.get("ticker") or "").upper())
            for row in rows
        }
        missing_identity = sorted(
            str(row.get("ticker") or "").upper()
            for row in historical_members
            if int(row.get("ibkr_conid") or 0) <= 0
        )
        if missing_identity:
            raise ValueError(
                "Historical Watchlist members require point-in-time conids: "
                + ", ".join(missing_identity)
            )
        for account_key in account_keys:
            for member in historical_members:
                ticker = str(member.get("ticker") or "").upper()
                if not ticker or (account_key, ticker) in existing:
                    continue
                rows.append(
                    {
                        "assignment_id": f"historical-watchlist:{account_key}:{ticker}",
                        "account_key": account_key,
                        "ticker": ticker,
                        "conid": int(member["ibkr_conid"]),
                        "status": "watching",
                        "permissions": {
                            "observe": True,
                            "enter": True,
                            "add": True,
                            "reduce": True,
                            "exit": True,
                            "reenter": True,
                        },
                        "parameters": {},
                        "source": "historical_watchlist",
                    }
                )
        if self.definition.assignment_ids:
            selected = set(self.definition.assignment_ids)
            rows = [row for row in rows if str(row.get("assignment_id")) in selected]
            missing = selected - {str(row.get("assignment_id")) for row in rows}
            if missing:
                raise ValueError(
                    f"Historical assignments are unavailable or inactive: {', '.join(sorted(missing))}"
                )
        return rows

    def _historical_watchlist_members(self) -> list[dict[str, Any]]:
        if self._historical_watchlist_cache is not None:
            return self._historical_watchlist_cache
        members: dict[str, dict[str, Any]] = {}
        conids: dict[str, int] = {}
        for snapshot in self._historical_watchlist_timeline():
            for row in snapshot["members"]:
                ticker = str(row.get("ticker") or "").upper()
                conid = int(row.get("ibkr_conid") or 0)
                if ticker in conids and conids[ticker] != conid:
                    raise ValueError(
                        f"Historical Watchlist identity changed for {ticker}: "
                        f"{conids[ticker]} -> {conid}; ticker-only assignment authority is unsafe"
                    )
                conids[ticker] = conid
                members[ticker] = dict(row)
        self._historical_watchlist_cache = [members[ticker] for ticker in sorted(members)]
        return self._historical_watchlist_cache

    def _historical_watchlist_timeline(self) -> list[dict[str, Any]]:
        if self._historical_watchlist_timeline_cache is None:
            self._historical_watchlist_timeline_cache = (
                _historical_watchlist_membership_timeline_for_configuration(
                    self.definition.configuration_revision,
                    start=self.definition.requested_start,
                    end=self.definition.session_end,
                )
            )
        return self._historical_watchlist_timeline_cache

    def _apply_historical_watchlist_membership(self, event_time: datetime) -> None:
        timeline = self._historical_watchlist_timeline()
        while self._historical_watchlist_timeline_index < len(timeline):
            snapshot = timeline[self._historical_watchlist_timeline_index]
            effective_at = snapshot["effective_at"]
            if effective_at > event_time:
                break
            current = {
                str(row.get("ticker") or "").upper()
                for row in snapshot["members"]
                if str(row.get("ticker") or "").strip()
            }
            added = sorted(current - self._active_historical_watchlist_tickers)
            removed = sorted(self._active_historical_watchlist_tickers - current)
            self._active_historical_watchlist_tickers = current
            self._historical_watchlist_timeline_index += 1
            if self._journal is not None:
                for ticker in added:
                    self._journal.append(
                        run_id=self.run_id,
                        category="watchlist_membership",
                        entity_type="historical_watchlist_member",
                        entity_id=ticker,
                        event_time=effective_at,
                        payload={
                            "event": "added",
                            "ticker": ticker,
                            "effective_at": effective_at.isoformat(),
                            "source": "causal_historical_watchlist",
                        },
                    )
                for ticker in removed:
                    self._journal.append(
                        run_id=self.run_id,
                        category="watchlist_membership",
                        entity_type="historical_watchlist_member",
                        entity_id=ticker,
                        event_time=effective_at,
                        payload={
                            "event": "removed",
                            "ticker": ticker,
                            "effective_at": effective_at.isoformat(),
                            "source": "causal_historical_watchlist",
                        },
                    )

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
        universe_tickers.extend(
            str(row.get("ticker") or "").upper()
            for row in self._historical_watchlist_members()
        )
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
                "Historical run requires at least one configured Canvas symbol or strategy assignment"
            )
        return tickers

    async def _load_strategy_frames(self) -> list[ReplayDerivedFrame]:
        if self.definition.mode == RunMode.BACKTEST_DEBUG:
            fixture = self.definition.debug_fixture
            if fixture is None:
                raise RuntimeError("Backtest Debug fixture disappeared before execution")
            return _debug_derived_frames(fixture.derived_frames)
        if self._strategy is None:
            return []
        requests = {
            (assignment.ticker, timeframe)
            for assignment in self._strategy.assignments()
            for timeframe in strategy_rule_timeframes(assignment.parameters)
        }
        if not requests:
            return []
        fetch_permits = asyncio.Semaphore(replay_history_fetch_concurrency())

        async def load_derived(ticker: str, timeframe: str) -> list[ReplayDerivedFrame]:
            async with fetch_permits:
                return await _historical_derived_frames(
                    ticker=ticker,
                    timeframe=timeframe,
                    start=self.definition.session_start,
                    end=self.definition.session_end,
                    authority_sink=self._record_data_authority,
                )

        groups = await asyncio.gather(
            *(load_derived(ticker, timeframe) for ticker, timeframe in sorted(requests))
        )
        tickers = tuple(sorted({ticker for ticker, _ in requests}))
        events_by_ticker = await _historical_signal_events(
            tickers=tickers,
            start=self.definition.session_start,
            end=self.definition.session_end,
            authority_sink=self._record_data_authority,
        )
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

    def _record_data_authority(self, key: str, evidence: dict[str, Any]) -> None:
        normalized = deepcopy(evidence)
        previous = self._data_authority.get(key)
        if previous is not None and previous != normalized:
            raise RuntimeError(
                f"Historical data authority changed for {key}; restart from a new run"
            )
        if previous is not None:
            return
        self._data_authority[key] = normalized
        if self._journal is not None:
            self._journal.append(
                run_id=self.run_id,
                category="data_authority",
                entity_type="source_revision",
                entity_id=key,
                event_time=self.current_time or self.definition.session_start,
                payload={"source_key": key, **normalized},
            )
        self._write_manifest()

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
        if self.definition.debug_fixture is not None:
            fixture_target = self.run_dir / "debug-fixture.json"
            fixture_temporary = self.run_dir / "debug-fixture.json.tmp"
            fixture_temporary.write_text(
                json.dumps(
                    self.definition.debug_fixture.payload(include_records=True),
                    indent=2,
                    sort_keys=True,
                    default=str,
                ),
                encoding="utf-8",
            )
            fixture_temporary.replace(fixture_target)
            payload["debug_fixture_path"] = str(fixture_target)
        target = self.run_dir / "manifest.json"
        temporary = self.run_dir / "manifest.json.tmp"
        temporary.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
        temporary.replace(target)


class ReplayRunService:
    def __init__(
        self,
        runtime_root: Path | None = None,
        *,
        max_resident_runs: int | None = None,
    ) -> None:
        self.runtime_root = (runtime_root or replay_runtime_root()).resolve()
        configured_limit = max_resident_runs
        if configured_limit is None:
            try:
                configured_limit = int(
                    os.environ.get(
                        "TRADING_REPLAY_MAX_RESIDENT_RUNS",
                        str(DEFAULT_MAX_RESIDENT_RUNS),
                    )
                )
            except ValueError as exc:
                raise ValueError(
                    "TRADING_REPLAY_MAX_RESIDENT_RUNS must be an integer"
                ) from exc
        if configured_limit < 1:
            raise ValueError("max_resident_runs must be positive")
        self.max_resident_runs = configured_limit
        self._runs: dict[str, ReplayRunController] = {}
        self._lock = asyncio.Lock()

    async def create(self, definition: ReplayRunDefinition) -> ReplayRunController:
        controller = ReplayRunController(definition, runtime_root=self.runtime_root)
        async with self._lock:
            terminal = sorted(
                (
                    resident
                    for resident in self._runs.values()
                    if resident.status in TERMINAL_REPLAY_STATUSES
                ),
                key=lambda resident: resident.updated_at,
            )
            while len(self._runs) >= self.max_resident_runs and terminal:
                evicted = terminal.pop(0)
                self._runs.pop(evicted.run_id, None)
            if len(self._runs) >= self.max_resident_runs:
                raise ReplayRunCapacityError(
                    "Replay resident-run capacity is full; stop or finish an active run "
                    "before creating another"
                )
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


def backtest_runtime_root() -> Path:
    configured = os.environ.get("TRADING_BACKTEST_ROOT", "").strip()
    if configured:
        return Path(configured)
    trading_root = Path(
        os.environ.get("TRADING_RUNTIME_ROOT", str(DEFAULT_BACKTEST_ROOT.parent))
    )
    return trading_root / "backtest"


def backtest_debug_runtime_root() -> Path:
    configured = os.environ.get("TRADING_BACKTEST_DEBUG_ROOT", "").strip()
    if configured:
        return Path(configured)
    trading_root = Path(
        os.environ.get("TRADING_RUNTIME_ROOT", str(DEFAULT_BACKTEST_DEBUG_ROOT.parent))
    )
    return trading_root / "backtest_debug"


def _debug_market_events(rows: tuple[dict[str, Any], ...]) -> list[MarketEvent]:
    events: list[MarketEvent] = []
    for index, source in enumerate(rows, start=1):
        row = dict(source)
        kind = str(row.get("kind") or "").strip().lower()
        ticker = _ticker(row.get("ticker"))
        event_time = _debug_time(row.get("ts"))
        ingest_time = _debug_time(row.get("ingest_ts") or event_time)
        common = {
            "conditions": tuple(int(value) for value in row.get("conditions") or ()),
            "ingest_ts": ingest_time,
            "raw": dict(row.get("raw") or {}),
            "sequence": int(row.get("sequence") or index),
            "source": f"debug_fixture:{str(row.get('source') or 'deterministic')}",
            "tape": int(row.get("tape") or 0),
            "ticker": ticker,
            "ts": event_time,
        }
        if kind == "quote":
            event = QuoteEvent(
                ask_exchange=int(row.get("ask_exchange") or 0),
                ask_price=float(row.get("ask_price") or 0),
                ask_size=float(row.get("ask_size") or 0),
                bid_exchange=int(row.get("bid_exchange") or 0),
                bid_price=float(row.get("bid_price") or 0),
                bid_size=float(row.get("bid_size") or 0),
                indicators=tuple(int(value) for value in row.get("indicators") or ()),
                **common,
            )
        elif kind == "trade":
            event = TradeEvent(
                event_id=str(row.get("event_id") or f"debug-{index}"),
                exchange=int(row.get("exchange") or 0),
                participant_ts=(
                    _debug_time(row["participant_ts"])
                    if row.get("participant_ts")
                    else None
                ),
                price=float(row.get("price") or 0),
                size=float(row.get("size") or 0),
                trf_id=int(row.get("trf_id") or 0),
                trf_ts=(
                    _debug_time(row["trf_ts"]) if row.get("trf_ts") else None
                ),
                **common,
            )
        else:
            raise ValueError(f"Debug fixture record {index} has unsupported kind {kind!r}")
        events.append(event)
    ordered = sorted(events, key=lambda event: (event.ts, event.sequence, event.kind))
    if events != ordered:
        raise ValueError("Debug fixture market_events must already be causally ordered")
    return events


def _debug_derived_frames(rows: tuple[dict[str, Any], ...]) -> list[ReplayDerivedFrame]:
    frames = [
        ReplayDerivedFrame(
            as_of=_debug_time(row.get("as_of")),
            bar=dict(row.get("bar") or {}),
            indicator=dict(row.get("indicator") or {}),
            sequence=int(row.get("sequence") or index),
            ticker=_ticker(row.get("ticker")),
            timeframe=str(row.get("timeframe") or "1m"),
            signals={str(key): float(value) for key, value in dict(row.get("signals") or {}).items()},
        )
        for index, row in enumerate(rows, start=1)
    ]
    ordered = sorted(
        frames,
        key=lambda frame: (frame.as_of, frame.ticker, frame.timeframe, frame.sequence),
    )
    if frames != ordered:
        raise ValueError("Debug fixture derived_frames must already be causally ordered")
    return frames


def _debug_time(value: Any) -> datetime:
    if isinstance(value, datetime):
        parsed = value
    else:
        text = str(value or "").strip()
        if not text:
            raise ValueError("Debug fixture timestamps are required")
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("Debug fixture timestamps must include an explicit timezone")
    return parsed


def _historical_watchlist_members_for_configuration(
    approved: dict[str, Any],
    *,
    as_of: datetime,
) -> list[dict[str, Any]]:
    """Resolve every enabled Watchlist universe at one causal event clock.

    Replay and Backtest must never interpret a partial scanner materialization as
    the full eligible market because absent rows would become false exclusions.
    """
    configuration = dict(approved.get("payload") or {})
    universes = [
        dict(universe)
        for universe in configuration.get("universes") or []
        if bool(universe.get("enabled", True))
        and str(universe.get("source") or "") == "watchlist"
    ]
    if not universes:
        return []
    model = dict(approved.get("configuration_model") or {})
    if not model:
        raise ValueError(
            "Historical Watchlist resolution requires the approved configuration model"
        )
    from src.backend.watchlist_runtime_service import resolve_historical_watchlist

    members: dict[str, dict[str, Any]] = {}
    for universe in universes:
        resolved = resolve_historical_watchlist(
            model,
            str(universe.get("scanner_view_id") or ""),
            as_of=as_of,
        )
        if str(resolved.get("status") or "") != "ready" or not bool(
            dict(resolved.get("scanner") or {}).get("complete_universe")
        ):
            detail = str(resolved.get("status") or "unavailable")
            raise ValueError(
                f"Historical Watchlist {universe.get('name')} requires a complete "
                f"full-universe snapshot; current status is {detail}"
            )
        for row in resolved.get("members") or []:
            ticker = str(row.get("ticker") or "").strip().upper()
            if ticker:
                members[ticker] = dict(row)
    return [members[ticker] for ticker in sorted(members)]


def _historical_watchlist_membership_timeline_for_configuration(
    approved: dict[str, Any],
    *,
    start: datetime,
    end: datetime,
) -> list[dict[str, Any]]:
    """Resolve a bounded causal Watchlist snapshot at every run session boundary.

    Historical Scanner snapshots are full-market, minute-granularity products.
    Replaying every configured live refresh (often one second) would rebuild the
    full market thousands of times per session. The historical controller
    therefore pins membership at the requested first clock and re-resolves it at
    04:00 New York on every later exchange weekday. Intraday Scanner-membership
    event replay remains a separate product requirement.
    """
    if start.tzinfo is None or end.tzinfo is None:
        raise ValueError("Historical Watchlist timeline bounds must be timezone-aware")
    if end < start:
        raise ValueError("Historical Watchlist timeline end precedes start")
    local_start = start.astimezone(NEW_YORK)
    local_end = end.astimezone(NEW_YORK)
    clocks: list[datetime] = [local_start]
    cursor = local_start.date() + timedelta(days=1)
    while cursor <= local_end.date():
        if cursor.weekday() < 5:
            clocks.append(datetime.combine(cursor, clock_time(4, 0), tzinfo=NEW_YORK))
        cursor += timedelta(days=1)
    return [
        {
            "effective_at": as_of,
            "members": _historical_watchlist_members_for_configuration(
                approved,
                as_of=as_of,
            ),
        }
        for as_of in clocks
        if as_of <= local_end
    ]


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
    historical_watchlist_members: list[dict[str, Any]] = []
    historical_watchlist_error = ""
    try:
        historical_watchlist_members = _historical_watchlist_members_for_configuration(
            approved,
            as_of=definition.requested_start,
        )
    except Exception as exc:
        historical_watchlist_error = str(exc)
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
            *(
                str(row.get("ticker") or "").strip().upper()
                for row in historical_watchlist_members
            ),
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
            "historical_watchlists",
            "Historical Watchlists",
            not historical_watchlist_error,
            f"{len(historical_watchlist_members)} point-in-time Watchlist member(s) were resolved from a complete market snapshot."
            if historical_watchlist_members
            else "No enabled Watchlist-backed universe is configured for this run.",
            historical_watchlist_error or "Approved configuration at the Replay event clock",
            required=any(
                bool(universe.get("enabled", True))
                and str(universe.get("source") or "") == "watchlist"
                for universe in configuration.get("universes") or []
            ),
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


def backtest_preflight(
    *,
    anchor_date: date,
    session_count: int,
    initial_cash: float = 100_000.0,
    configuration_revision: dict[str, Any] | None = None,
) -> dict[str, Any]:
    approved = configuration_revision or replay_configuration_snapshot()
    base = historical_preflight(
        mode=RunMode.BACKTEST.value,
        anchor_date=anchor_date,
        session_count=session_count,
    )
    configuration = dict(approved.get("payload") or {})
    sessions = [date.fromisoformat(value) for value in base["window"]["sessions"]]
    bindings = [
        dict(row)
        for row in dict(configuration.get("accounts") or {}).get("bindings") or []
        if bool(row.get("enabled", True))
        and RunMode.BACKTEST.value in set(row.get("modes") or [])
    ]
    binding_keys = {str(row.get("account_key") or "") for row in bindings}
    assignments = [
        dict(row)
        for row in configuration.get("assignments") or []
        if str(row.get("status") or "") not in {"disabled", "completed", "error"}
        and str(row.get("account_key") or "") in binding_keys
    ]
    watchlists = [
        dict(row)
        for row in configuration.get("universes") or []
        if bool(row.get("enabled", True)) and str(row.get("source") or "") == "watchlist"
    ]
    watchlist_members: list[dict[str, Any]] = []
    watchlist_snapshot_count = 0
    watchlist_error = ""
    if watchlists and sessions:
        try:
            timeline = _historical_watchlist_membership_timeline_for_configuration(
                approved,
                start=datetime.combine(sessions[0], clock_time(4, 0), tzinfo=NEW_YORK),
                end=datetime.combine(sessions[-1], clock_time(20, 0), tzinfo=NEW_YORK),
            )
            watchlist_snapshot_count = len(timeline)
            watchlist_members = list(
                {
                    str(row.get("ticker") or "").upper(): row
                    for snapshot in timeline
                    for row in snapshot["members"]
                    if str(row.get("ticker") or "").strip()
                }.values()
            )
        except Exception as exc:
            watchlist_error = str(exc)
    checks = list(base["checks"])
    checks.append(
        {
            "id": "approved_configuration",
            "label": "Approved application revision",
            "status": "ready",
            "summary": f"Revision {approved.get('revision')} is pinned for the complete run.",
            "evidence": str(approved.get("content_hash") or approved.get("revision_id") or ""),
            "required": True,
        }
    )
    checks.append(
        {
            "id": "simulated_accounts",
            "label": "Backtest account bindings",
            "status": "ready" if bindings else "blocked",
            "summary": (
                f"{len(bindings)} account binding(s) map to isolated simulated ledgers."
                if bindings
                else "The approved revision has no enabled account binding for Backtest."
            ),
            "evidence": ", ".join(sorted(binding_keys)) or "No Backtest account authority",
            "required": True,
        }
    )
    work_ready = bool(assignments or watchlist_members) and not watchlist_error
    checks.append(
        {
            "id": "strategy_assignments",
            "label": "Historical strategy population",
            "status": "ready" if work_ready else "blocked",
            "summary": (
                f"{len(assignments)} pinned assignment(s) and {len(watchlist_members)} causal Watchlist member(s) across {watchlist_snapshot_count} session snapshot(s) are configured."
                if work_ready
                else watchlist_error or "Backtest needs an active assignment or a non-empty causal Watchlist universe."
            ),
            "evidence": "Point-in-time membership is pinned at the first event clock and every later exchange-session boundary.",
            "required": True,
        }
    )
    storage_ready = False
    storage_evidence = str(backtest_runtime_root())
    try:
        root = backtest_runtime_root()
        root.mkdir(parents=True, exist_ok=True)
        probe = root / f".backtest-preflight-{uuid4().hex}.tmp"
        probe.write_text("ready", encoding="utf-8")
        probe.unlink()
        storage_ready = True
    except OSError as exc:
        storage_evidence = str(exc)
    checks.append(
        {
            "id": "runtime_storage",
            "label": "Backtest runtime storage",
            "status": "ready" if storage_ready else "blocked",
            "summary": (
                "Journal and manifests use the external trading runtime root."
                if storage_ready
                else "The external Backtest runtime root is not writable."
            ),
            "evidence": storage_evidence,
            "required": True,
        }
    )
    ready = bool(
        base["strategy_run_ready"]
        and bindings
        and work_ready
        and storage_ready
        and sessions
        and 1_000 <= initial_cash <= 1_000_000_000
    )
    return {
        **base,
        "checks": checks,
        "strategy_run_ready": ready,
        "configuration_revision_id": approved.get("revision_id", ""),
        "configuration_revision": approved.get("revision", 0),
        "configuration_content_hash": approved.get("content_hash", ""),
        "configuration_label": approved.get("label", ""),
        "initial_cash": initial_cash,
    }


def backtest_debug_preflight(
    *,
    session_date: date,
    start_time: clock_time,
    initial_cash: float = 100_000.0,
    assignment_ids: tuple[str, ...] = (),
    tickers: tuple[str, ...] = (),
    configuration_revision: dict[str, Any] | None = None,
) -> dict[str, Any]:
    approved = configuration_revision or replay_configuration_snapshot()
    configuration = dict(approved.get("payload") or {})
    bindings = [
        dict(row)
        for row in dict(configuration.get("accounts") or {}).get("bindings") or []
        if bool(row.get("enabled", True))
        and RunMode.BACKTEST_DEBUG.value in set(row.get("modes") or [])
    ]
    binding_keys = {str(row.get("account_key") or "") for row in bindings}
    assignments = [
        dict(row)
        for row in configuration.get("assignments") or []
        if str(row.get("status") or "") not in {"disabled", "completed", "error"}
        and str(row.get("account_key") or "") in binding_keys
        and (not assignment_ids or str(row.get("assignment_id") or "") in assignment_ids)
    ]
    configured_tickers = tuple(
        dict.fromkeys(
            [
                *(_ticker(row.get("ticker")) for row in assignments),
                *(_ticker(value) for value in tickers),
                *_canvas_profile_tickers(
                    dict(
                        dict(configuration.get("canvas") or {}).get("profile") or {}
                    )
                ),
            ]
        )
    )
    storage_ready = False
    storage_evidence = str(backtest_debug_runtime_root())
    try:
        root = backtest_debug_runtime_root()
        root.mkdir(parents=True, exist_ok=True)
        probe = root / f".backtest-debug-preflight-{uuid4().hex}.tmp"
        probe.write_text("ready", encoding="utf-8")
        probe.unlink()
        storage_ready = True
    except OSError as exc:
        storage_evidence = str(exc)
    checks = [
        _check(
            "approved_configuration",
            "Approved application revision",
            bool(approved.get("revision_id")),
            f"Revision {approved.get('revision')} is pinned to every fixture run.",
            str(approved.get("content_hash") or approved.get("revision_id") or ""),
        ),
        _check(
            "simulated_accounts",
            "Backtest Debug account bindings",
            bool(bindings),
            f"{len(bindings)} isolated simulated account binding(s) are enabled."
            if bindings
            else "The approved revision has no Backtest Debug account binding.",
            ", ".join(sorted(binding_keys)) or "No Backtest Debug account authority",
        ),
        _check(
            "configured_symbols",
            "Fixture symbols",
            bool(configured_tickers),
            f"{len(configured_tickers)} configured symbol(s) can be used by fixtures."
            if configured_tickers
            else "No Canvas, assignment, or explicit fixture symbol is configured.",
            ", ".join(configured_tickers[:12]) or "No symbol scope",
        ),
        _check(
            "runtime_storage",
            "Backtest Debug runtime storage",
            storage_ready,
            "Exact fixtures, manifests, and journals use the external runtime root."
            if storage_ready
            else "The external Backtest Debug runtime root is not writable.",
            storage_evidence,
        ),
    ]
    ready = all(row["status"] == "ready" for row in checks if row["required"])
    return {
        "schema_version": 1,
        "ready": ready,
        "strategy_run_ready": ready,
        "checks": checks,
        "session_date": session_date.isoformat(),
        "start_time": start_time.isoformat(timespec="seconds"),
        "initial_cash": initial_cash,
        "assignments": assignments,
        "tickers": list(configured_tickers),
        "configuration_revision_id": approved.get("revision_id", ""),
        "configuration_revision": approved.get("revision", 0),
        "configuration_content_hash": approved.get("content_hash", ""),
        "configuration_label": approved.get("label", ""),
    }


async def _historical_derived_frames(
    *,
    ticker: str,
    timeframe: str,
    start: datetime,
    end: datetime,
    authority_sink: Callable[[str, dict[str, Any]], None] | None = None,
) -> list[ReplayDerivedFrame]:
    url = qmd_history_websocket_url(
        f"/stream/derived/{urllib.parse.quote(ticker)}",
        {
            "start": start.isoformat(),
            "end": end.isoformat(),
            "timeframe": timeframe,
            "emit": "full",
            "as_of": end.isoformat(),
            "updates_per_second": 0,
        },
    )
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
    if authority_sink is not None:
        authority_sink(
            f"derived:{_ticker(ticker)}:{timeframe}",
            _qmd_payload_authority(payload, authority="qmd_history_derived"),
        )
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
    tickers: tuple[str, ...],
    start: datetime,
    end: datetime,
    authority_sink: Callable[[str, dict[str, Any]], None] | None = None,
) -> dict[str, list[dict[str, Any]]]:
    request = QmdProductRequest(
        "scanner",
        authority="history",
        start=start.isoformat(),
        end=end.isoformat(),
        as_of=end.isoformat(),
        timeout_seconds=120,
    )

    def fetch() -> dict[str, Any]:
        payload = qmd_product_request(request).payload
        if not isinstance(payload, dict):
            raise RuntimeError("QMD historical Scanner response must be an object")
        return payload

    payload = await asyncio.to_thread(fetch)
    if payload.get("error"):
        raise RuntimeError(f"QMD historical signal stream failed: {payload['error']}")
    if authority_sink is not None:
        authority_sink(
            "scanner_signals",
            _qmd_payload_authority(payload, authority="qmd_history_scanner"),
        )
    requested = set(tickers)
    grouped = {ticker: [] for ticker in tickers}
    for raw in payload.get("recent_signal_events") or []:
        row = dict(raw)
        ticker = str(row.get("ticker") or "").upper()
        if ticker in requested:
            grouped[ticker].append(row)
    for rows in grouped.values():
        rows.sort(
            key=lambda row: _aware_datetime(
                row.get("effective_at") or row.get("observed_at")
            )
        )
    return grouped


def _qmd_payload_authority(
    payload: dict[str, Any],
    *,
    authority: str,
) -> dict[str, Any]:
    cache = payload.get("cache") if isinstance(payload.get("cache"), dict) else {}
    revision = payload.get("source_revision")
    if not isinstance(revision, dict):
        revision = cache.get("source_revision")
    if not isinstance(revision, dict):
        raise RuntimeError(f"{authority} response omitted source revision evidence")
    token = str(revision.get("token") or revision.get("revision_token") or "").strip()
    plan_hash = str(revision.get("source_plan_hash") or "").strip()
    if not token or not plan_hash:
        raise RuntimeError(f"{authority} response returned incomplete source revision evidence")
    return {
        "authority": authority,
        "revision_token": token,
        "source_plan_hash": plan_hash,
        "complete_for_history": bool(revision.get("complete_for_history", False)),
        "source_tiers": list(revision.get("source_tiers") or ()),
        "engine_version": str(cache.get("engine_version") or payload.get("engine_version") or ""),
        "event_count": int(cache.get("event_count") or payload.get("event_count") or 0),
    }


def replay_history_fetch_concurrency() -> int:
    value = os.environ.get(
        "TRADING_REPLAY_HISTORY_FETCH_CONCURRENCY",
        str(DEFAULT_HISTORY_FETCH_CONCURRENCY),
    )
    try:
        parsed = int(value)
    except ValueError as exc:
        raise ValueError(
            "TRADING_REPLAY_HISTORY_FETCH_CONCURRENCY must be an integer"
        ) from exc
    if not 1 <= parsed <= 32:
        raise ValueError(
            "TRADING_REPLAY_HISTORY_FETCH_CONCURRENCY must be between 1 and 32"
        )
    return parsed


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
    side = str(
        dict(
            dict(configuration.get("strategy_profile") or {}).get("lifecycle") or {}
        ).get("trading_behavior", {}).get("side")
        or dict(dict(strategy.get("parameters") or {}).get("strategy_behavior") or {}).get("side")
        or "long"
    )
    campaign_id = str(
        payload.get("campaign_id")
        or f"{deployment.get('deployment_id') or 'deployment'}:{ticker}:{side}"
    )
    state = campaign_state(
        campaign_id=f"replay:{campaign_id}",
        deployment_id=str(deployment.get("deployment_id") or ""),
        profile_id=str(strategy.get("profile_id") or ""),
        book_id=str(deployment.get("book_id") or "default"),
        universe_id=str(deployment.get("universe_id") or ""),
        side=side,
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
