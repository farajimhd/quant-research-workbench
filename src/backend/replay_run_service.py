from __future__ import annotations

import asyncio
import hashlib
import json
import os
import re
import sqlite3
import time
import urllib.parse
from copy import deepcopy
from dataclasses import asdict, dataclass, field, replace
from datetime import UTC, date, datetime, time as clock_time, timedelta
from pathlib import Path
from typing import Any, Awaitable, Callable, Iterator, Mapping, Sequence
from uuid import uuid4
from weakref import WeakValueDictionary
from zoneinfo import ZoneInfo

import websockets
from websockets.exceptions import ConnectionClosedError

from src.backend.data_field_contracts import (
    field_instance_ref,
    interval_expression,
    project_data_field_outputs,
)
from src.backend.canonical_trading_service import trading_state_payload
from src.backend.lifecycle_contract import lifecycle_projection
from src.backend.news_signal_runtime_service import (
    all_news_synthesis_events,
    bullish_news_signal_rows,
)
from src.backend.signal_stream_runtime_service import SIGNAL_STREAM_RUNTIME
from src.backend.qmd_gateway_client import (
    QmdProductRequest,
    qmd_advance_historical_structure_snapshot,
    qmd_historical_structure_snapshot,
    qmd_historical_source_revision,
    qmd_history_websocket_url,
    qmd_product_request,
)
from src.backend.trading_runtime_service import (
    STRATEGY_ACTIVITY_EVENT_TYPES,
    historical_day_coverage,
    historical_gateway_base_url,
    historical_gateway_snapshot,
    historical_preflight,
    strategy_activity_event_type,
)
from src.backend.trading_configuration_service import (
    approved_session_configuration_snapshot,
    backtest_configuration_snapshot,
    backtest_debug_configuration_snapshot,
    candidate_session_configuration_snapshot,
    merged_assignment_parameters,
    replay_configuration_snapshot,
)
from src.backend.trading_action_registry import resolve_trading_action
from src.market_engine.events import MarketEvent, QuoteEvent, TradeEvent
from src.market_engine.historical_source import QmdHistoricalEventSource
from src.trading_runtime.domain import InstrumentContract, TradingMode
from src.trading_runtime.journal import JournalRecord, TradingJournal
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
    StrategyAssignment,
    StrategyObservation,
    StrategyPermissions,
    entry_stage_without_rule_set,
    evaluate_entry_decision_rules,
)
from src.trading_runtime.strategy_registry import (
    StrategyExecutorRegistration,
    strategy_executor,
)
from src.trading_runtime.strategy_orders import RuntimeIbkrStrategyOrderPlanner
from src.trading_runtime.strategy_campaign import campaign_state
from src.trading_runtime.strategy_activation import run_plan_accepts_signal
from src.trading_runtime.watchlist_resolver import evaluate_rule_sets_frame


NEW_YORK = ZoneInfo("America/New_York")
DEFAULT_REPLAY_ROOT = Path(r"D:\TradingML\runtimes\trading\replay")
DEFAULT_BACKTEST_ROOT = Path(r"D:\TradingML\runtimes\trading\backtest")
DEFAULT_BACKTEST_DEBUG_ROOT = Path(r"D:\TradingML\runtimes\trading\backtest_debug")
INDICATOR_EMA_WARMUP_DAYS = 7
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
REPLAY_RESTART_CHECKPOINT_INTERVAL_EVENTS = 25_000
BACKTEST_RESTART_CHECKPOINT_INTERVAL_EVENTS = 100_000
DEFAULT_MAX_RESIDENT_RUNS = 32
DEFAULT_HISTORY_FETCH_CONCURRENCY = 4
MAX_DEBUG_FIXTURE_EVENTS = 20_000
RESTART_CHECKPOINT_SCHEMA_VERSION = 3
# Version the projected frame contract, not only QMD's source revision. Version
# 4 adds exact prepared-bar trade counts and dollar volume. The shared bar
# artifact can carry zero spread when built from trade-only persisted bars, so
# current execution quality continues to prefer the causal raw quote stream.
PREPARED_FRAME_CACHE_SCHEMA_VERSION = 5
_PREPARED_FRAME_CACHE_LOCKS: WeakValueDictionary[str, asyncio.Lock] = (
    WeakValueDictionary()
)


def _replay_navigation_action(
    record: JournalRecord, target_event_type: str = ""
) -> dict[str, Any] | None:
    """Project the next causal milestone worth stopping Replay for."""

    payload = dict(record.payload)
    action = str(payload.get("action") or "").strip().lower()
    event_type = strategy_activity_event_type(
        record.entity_type,
        category=record.category,
    )
    if target_event_type and event_type != target_event_type:
        return None
    if not target_event_type:
        if record.category == "market_discovery_signal":
            kind = "watch_started"
            label = str(
                payload.get("signal_stream_name")
                or payload.get("signal_stream_id")
                or "Market signal"
            )
        elif record.category == "strategy_decision" and action not in {
            "",
            "wait",
            "hold",
        }:
            kind = "strategy_decision"
            label = action.replace("_", " ")
        elif record.category == "order_management":
            kind = "order_management"
            label = str(payload.get("state") or record.entity_type).replace("_", " ")
        else:
            return None
    elif event_type == "signal":
        kind = "strategy_signal"
        label = str(
            payload.get("signal_stream_name")
            or payload.get("signal_stream_id")
            or action
            or "Market signal"
        )
    elif event_type == "watchlist":
        kind = "watchlist_membership"
        label = str(payload.get("event") or "Watchlist membership").replace("_", " ")
    elif event_type == "campaign_state":
        kind = "campaign_state"
        label = str(
            payload.get("status")
            or payload.get("state")
            or action
            or "Campaign state"
        ).replace("_", " ")
    elif event_type == "decision":
        kind = "strategy_decision"
        label = (action or str(payload.get("state") or "Strategy decision")).replace(
            "_", " "
        )
    elif event_type == "order":
        kind = "order_management"
        label = str(payload.get("state") or record.entity_type).replace("_", " ")
    else:  # pragma: no cover - guarded by the shared activity taxonomy
        return None
    return {
        "kind": kind,
        "label": label,
        "ticker": str(payload.get("ticker") or "").upper(),
        "event_time": record.event_time.isoformat(),
        "category": record.category,
        "entity_type": record.entity_type,
        "entity_id": record.entity_id,
        "sequence": record.sequence,
        "event_type": event_type,
    }


class ReplayRunCapacityError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class HistoricalDebugFixture:
    fixture_id: str
    market_events: tuple[dict[str, Any], ...] = ()
    derived_frames: tuple[dict[str, Any], ...] = ()
    signal_events: tuple[dict[str, Any], ...] = ()
    watchlist_events: tuple[dict[str, Any], ...] = ()

    def __post_init__(self) -> None:
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}", self.fixture_id):
            raise ValueError("Debug fixture_id must be a stable 1-128 character identifier")
        if not self.market_events and not self.derived_frames and not self.signal_events:
            raise ValueError("Debug fixture requires market_events, derived_frames, or signal_events")
        if (
            len(self.market_events)
            + len(self.derived_frames)
            + len(self.signal_events)
            + len(self.watchlist_events)
            > MAX_DEBUG_FIXTURE_EVENTS
        ):
            raise ValueError(
                f"Debug fixture supports at most {MAX_DEBUG_FIXTURE_EVENTS:,} records"
            )
        _debug_watchlist_membership_timeline(self.watchlist_events)

    @property
    def content_hash(self) -> str:
        records = {
            "fixture_id": self.fixture_id,
            "market_events": self.market_events,
            "derived_frames": self.derived_frames,
            "signal_events": self.signal_events,
        }
        # Preserve hashes persisted before deterministic Watchlist membership
        # became part of the Backtest Debug fixture contract.
        if self.watchlist_events:
            records["watchlist_events"] = self.watchlist_events
        canonical = json.dumps(
            records,
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
            "signal_event_count": len(self.signal_events),
            "watchlist_event_count": len(self.watchlist_events),
        }
        if include_records:
            payload["market_events"] = [dict(row) for row in self.market_events]
            payload["derived_frames"] = [dict(row) for row in self.derived_frames]
            payload["signal_events"] = [dict(row) for row in self.signal_events]
            payload["watchlist_events"] = [dict(row) for row in self.watchlist_events]
        return payload


@dataclass(frozen=True, slots=True)
class ReplayRunDefinition:
    session_date: date
    start_time: clock_time
    end_time: clock_time = clock_time(20, 0)
    initial_cash: float = 100_000.0
    assignment_ids: tuple[str, ...] = ()
    tickers: tuple[str, ...] = ()
    configuration_revision: dict[str, Any] = field(default_factory=dict)
    execution_mode: str = "strategy"
    mode: RunMode = RunMode.REPLAY
    final_session_date: date | None = None
    debug_fixture: HistoricalDebugFixture | None = None
    simulation_profile: str = "baseline"
    historical_frame_cache: dict[tuple[str, str, str, str], Any] | None = field(
        default=None,
        repr=False,
        compare=False,
    )
    historical_watchlist_cache: dict[str, Any] | None = field(
        default=None,
        repr=False,
        compare=False,
    )

    def __post_init__(self) -> None:
        if self.mode not in {RunMode.REPLAY, RunMode.BACKTEST, RunMode.BACKTEST_DEBUG}:
            raise ValueError("Historical controller mode must be replay, backtest, or backtest_debug")
        if self.execution_mode not in {"manual", "strategy"}:
            raise ValueError("Historical execution mode must be manual or strategy")
        if self.mode in {RunMode.BACKTEST, RunMode.BACKTEST_DEBUG} and self.execution_mode != "strategy":
            raise ValueError("Backtest and Debug require a Strategy Deployment")
        if self.mode == RunMode.BACKTEST_DEBUG and self.debug_fixture is None:
            raise ValueError("Backtest Debug requires a deterministic fixture")
        if self.mode != RunMode.BACKTEST_DEBUG and self.debug_fixture is not None:
            raise ValueError("Debug fixtures may only be used by Backtest Debug")
        if self.simulation_profile not in {"baseline", "stress"}:
            raise ValueError("Simulation profile must be baseline or stress")
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
        if not 4 <= self.end_time.hour <= 20:
            raise ValueError("Replay end time must be inside the 04:00-20:00 New York session")
        if self.end_time.hour == 20 and (
            self.end_time.minute or self.end_time.second or self.end_time.microsecond
        ):
            raise ValueError("Replay end time cannot be later than 20:00 New York")
        if (
            (self.final_session_date is None or self.final_session_date == self.session_date)
            and self.end_time < self.start_time
        ):
            raise ValueError("Replay end time cannot precede its requested start time")
        if not 1_000 <= self.initial_cash <= 1_000_000_000:
            raise ValueError("Replay initial cash must be between 1,000 and 1,000,000,000")
        if len(self.tickers) > 100:
            raise ValueError("Replay supports at most 100 explicitly configured symbols")
        if not self.configuration_revision.get("revision_id"):
            raise ValueError("Replay requires an immutable configuration revision")
        if self.debug_fixture is not None:
            fixture_times = [
                *(_debug_time(row.get("ts")) for row in self.debug_fixture.market_events),
                *(_debug_time(row.get("as_of")) for row in self.debug_fixture.derived_frames),
                *(_debug_time(row.get("available_at") or row.get("event_time")) for row in self.debug_fixture.signal_events),
                *(_debug_time(row.get("effective_at")) for row in self.debug_fixture.watchlist_events),
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
            self.end_time,
            tzinfo=NEW_YORK,
        )

    @property
    def requested_start(self) -> datetime:
        return datetime.combine(self.session_date, self.start_time, tzinfo=NEW_YORK)

    def payload(self) -> dict[str, Any]:
        approved = self.configuration_revision
        configuration = dict(approved.get("payload") or {})
        canvas = dict(configuration.get("canvas") or {})
        payload = {
            "mode": self.mode.value,
            "execution_mode": self.execution_mode,
            "session_date": self.session_date.isoformat(),
            "start_time": self.start_time.isoformat(timespec="seconds"),
            "end_time": self.end_time.isoformat(timespec="seconds"),
            "session_start": self.session_start.isoformat(),
            "session_end": self.session_end.isoformat(),
            "requested_start": self.requested_start.isoformat(),
            "initial_cash": self.initial_cash,
            "simulation_profile": self.simulation_profile,
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
        return payload


@dataclass(slots=True)
class ReplayDerivedFrame:
    as_of: datetime
    bar: dict[str, Any]
    indicator: dict[str, Any]
    sequence: int
    ticker: str
    timeframe: str
    signals: dict[str, float] = field(default_factory=dict)


class ReplayFrameSpool:
    """Disk-backed, event-time-ordered derived frames for one historical run."""

    def __init__(self, path: Path, *, reset: bool = True) -> None:
        self.path = path
        self._signal_events: dict[str, list[dict[str, Any]]] = {}
        self.path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(self.path)
        try:
            with connection:
                if reset:
                    connection.execute("DROP TABLE IF EXISTS strategy_frames")
                    connection.execute("DROP TABLE IF EXISTS strategy_frame_streams")
                connection.execute(
                    """
                    CREATE TABLE IF NOT EXISTS strategy_frames (
                        as_of_us INTEGER NOT NULL,
                        as_of TEXT NOT NULL,
                        ticker TEXT NOT NULL,
                        timeframe TEXT NOT NULL,
                        sequence INTEGER NOT NULL,
                        bar_json TEXT NOT NULL,
                        indicator_json TEXT NOT NULL
                    )
                    """
                )
                connection.execute(
                    """
                    CREATE TABLE IF NOT EXISTS strategy_frame_streams (
                        ticker TEXT NOT NULL,
                        timeframe TEXT NOT NULL,
                        completed_at TEXT NOT NULL,
                        authority_json TEXT NOT NULL DEFAULT '{}',
                        PRIMARY KEY (ticker, timeframe)
                    )
                    """
                )
                stream_columns = {
                    str(row[1])
                    for row in connection.execute(
                        "PRAGMA table_info(strategy_frame_streams)"
                    )
                }
                if "authority_json" not in stream_columns:
                    connection.execute(
                        "ALTER TABLE strategy_frame_streams "
                        "ADD COLUMN authority_json TEXT NOT NULL DEFAULT '{}'"
                    )
        finally:
            connection.close()

    def append(self, frames: list[ReplayDerivedFrame]) -> None:
        if not frames:
            return
        rows = (
            (
                int(frame.as_of.timestamp() * 1_000_000),
                frame.as_of.isoformat(),
                frame.ticker,
                frame.timeframe,
                frame.sequence,
                json.dumps(frame.bar, separators=(",", ":"), sort_keys=True),
                json.dumps(frame.indicator, separators=(",", ":"), sort_keys=True),
            )
            for frame in frames
        )
        connection = sqlite3.connect(self.path)
        try:
            with connection:
                connection.executemany(
                    """
                    INSERT INTO strategy_frames (
                        as_of_us, as_of, ticker, timeframe, sequence, bar_json, indicator_json
                    ) VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    rows,
                )
        finally:
            connection.close()

    def delete_stream(self, ticker: str, timeframe: str) -> None:
        """Discard a partial transport attempt before retrying one frozen stream."""

        connection = sqlite3.connect(self.path)
        try:
            with connection:
                connection.execute(
                    "DELETE FROM strategy_frames WHERE ticker = ? AND timeframe = ?",
                    (_ticker(ticker), timeframe),
                )
                connection.execute(
                    "DELETE FROM strategy_frame_streams WHERE ticker = ? AND timeframe = ?",
                    (_ticker(ticker), timeframe),
                )
        finally:
            connection.close()

    def mark_stream_complete(
        self,
        ticker: str,
        timeframe: str,
        authority: dict[str, Any] | None = None,
    ) -> None:
        connection = sqlite3.connect(self.path)
        try:
            with connection:
                connection.execute(
                    """
                    INSERT OR REPLACE INTO strategy_frame_streams (
                        ticker, timeframe, completed_at, authority_json
                    ) VALUES (?, ?, ?, ?)
                    """,
                    (
                        _ticker(ticker),
                        timeframe,
                        datetime.now(UTC).isoformat(),
                        json.dumps(authority or {}, separators=(",", ":"), sort_keys=True),
                    ),
                )
        finally:
            connection.close()

    def completed_streams(self) -> set[tuple[str, str]]:
        connection = sqlite3.connect(self.path)
        try:
            return {
                (str(ticker), str(timeframe))
                for ticker, timeframe in connection.execute(
                    "SELECT ticker, timeframe FROM strategy_frame_streams"
                )
            }
        finally:
            connection.close()

    def stream_authorities(self) -> dict[tuple[str, str], dict[str, Any]]:
        connection = sqlite3.connect(self.path)
        try:
            return {
                (str(ticker), str(timeframe)): dict(json.loads(authority_json or "{}"))
                for ticker, timeframe, authority_json in connection.execute(
                    "SELECT ticker, timeframe, authority_json "
                    "FROM strategy_frame_streams"
                )
            }
        finally:
            connection.close()

    def finalize(self, signal_events: dict[str, list[dict[str, Any]]]) -> None:
        self._signal_events = {
            ticker: sorted(
                (dict(event) for event in events),
                key=lambda event: _aware_datetime(
                    event.get("effective_at") or event.get("observed_at")
                ),
            )
            for ticker, events in signal_events.items()
        }
        connection = sqlite3.connect(self.path)
        try:
            with connection:
                connection.execute(
                    """
                    CREATE INDEX IF NOT EXISTS strategy_frames_event_order
                    ON strategy_frames (as_of_us, ticker, timeframe, sequence)
                    """
                )
        finally:
            connection.close()

    def __iter__(self) -> Iterator[ReplayDerivedFrame]:
        event_indices: dict[str, int] = {}
        active_signals: dict[str, dict[str, float]] = {}
        connection = sqlite3.connect(self.path)
        cursor: sqlite3.Cursor | None = None
        try:
            cursor = connection.execute(
                """
                SELECT as_of, ticker, timeframe, sequence, bar_json, indicator_json
                FROM strategy_frames
                ORDER BY as_of_us, ticker, timeframe, sequence
                """
            )
            for as_of, ticker, timeframe, sequence, bar_json, indicator_json in cursor:
                frame_time = _aware_datetime(as_of)
                ticker_events = self._signal_events.get(ticker, [])
                event_index = event_indices.get(ticker, 0)
                ticker_signals = active_signals.setdefault(ticker, {})
                while event_index < len(ticker_events):
                    event = ticker_events[event_index]
                    effective_at = _aware_datetime(
                        event.get("effective_at") or event.get("observed_at")
                    )
                    if effective_at > frame_time:
                        break
                    signal_key = str(event.get("signal_key") or "")
                    working_timeframe = str(event.get("working_timeframe") or "")
                    key = (
                        f"{signal_key}@{working_timeframe}"
                        if signal_key and working_timeframe
                        else ""
                    )
                    if key:
                        if str(event.get("state") or "") == "resolved":
                            ticker_signals.pop(key, None)
                        else:
                            ticker_signals[key] = float(event.get("score") or 0)
                    event_index += 1
                event_indices[ticker] = event_index
                yield ReplayDerivedFrame(
                    as_of=frame_time,
                    bar=json.loads(bar_json),
                    indicator=json.loads(indicator_json),
                    sequence=int(sequence),
                    ticker=str(ticker),
                    timeframe=str(timeframe),
                    signals=dict(ticker_signals),
                )
        finally:
            if cursor is not None:
                cursor.close()
            connection.close()


_STRATEGY_BAR_FIELDS = frozenset(
    {
        "bar_end",
        "bar_start",
        "close",
        "dollar_volume",
        "high",
        "liquidity_score",
        "low",
        "open",
        "spread_bps_close",
        "spread_bps_mean",
        "sym",
        "timeframe",
        "trade_count",
        "volume",
        "volume_rate_ratio",
    }
)
_STRATEGY_INDICATOR_FIELDS = frozenset(
    {
        "atr_14",
        "bar_end",
        "bar_start",
        "close",
        "flow_price_divergence_score",
        "flow_structure_composite_bias",
        "flow_structure_composite_confidence",
        "flow_structure_composite_score",
        "liquidity_dislocation_score",
        "macd_histogram",
        "macd_line",
        "macd_signal",
        "prev_close",
        "previous_close",
        "previous_high",
        "price_change_1_bar_pct",
        "price_volume_expansion_score",
        "qmd_structure_support_field",
        "qmd_structure_resistance_field",
        "qmd_structure_pressure_bias",
        "qmd_structure_pressure_confidence",
        "qmd_structure_up_probability",
        "qmd_structure_support_price",
        "qmd_structure_support_lower",
        "qmd_structure_support_upper",
        "qmd_structure_support_strength",
        "qmd_structure_support_confidence",
        "qmd_structure_resistance_price",
        "qmd_structure_resistance_lower",
        "qmd_structure_resistance_upper",
        "qmd_structure_resistance_strength",
        "qmd_structure_resistance_confidence",
        "qmd_structure_unified_levels",
        "qmd_structure_unified_level_delta",
        "structure_bos_direction",
        "structure_choch_direction",
        "structure_luld_upper",
        "structure_swing_high",
        "structure_swing_low",
        "sym",
        "timeframe",
        "vwap",
        "vwap_transition_score",
    }
)
_STRATEGY_LAZY_STRUCTURE_FIELDS = frozenset(
    {
        "qmd_structure_support_field",
        "qmd_structure_resistance_field",
        "qmd_structure_pressure_bias",
        "qmd_structure_pressure_confidence",
        "qmd_structure_up_probability",
        "qmd_structure_support_price",
        "qmd_structure_support_lower",
        "qmd_structure_support_upper",
        "qmd_structure_support_strength",
        "qmd_structure_support_confidence",
        "qmd_structure_resistance_price",
        "qmd_structure_resistance_lower",
        "qmd_structure_resistance_upper",
        "qmd_structure_resistance_strength",
        "qmd_structure_resistance_confidence",
        "qmd_structure_unified_levels",
        "qmd_structure_unified_level_delta",
        "structure_swing_high",
        "structure_swing_low",
    }
)


def _simulation_config(definition: ReplayRunDefinition) -> SimulationConfig:
    stress = definition.mode == RunMode.BACKTEST and definition.simulation_profile == "stress"
    return SimulationConfig(
        initial_cash=definition.initial_cash,
        commission_per_share=0.005,
        minimum_commission=1.0,
        liquidity_participation=0.10 if stress else 0.25,
        # Aggressive limits and market orders consume the full causal
        # top-of-book/event size in the baseline profile. The former 25%
        # passive participation haircut made a routed order at the ask wait
        # through several seconds of quotes even when displayed liquidity was
        # immediately executable. Stress retains a conservative 25% sweep.
        marketable_liquidity_participation=0.25 if stress else 1.0,
        market_slippage_bps=10.0 if stress else 5.0 if definition.mode == RunMode.BACKTEST else 0.0,
    )


@dataclass(frozen=True, slots=True)
class ReplaySignalEvent:
    available_at: datetime
    occurrence: dict[str, Any]
    source_values: dict[str, Any]
    ticker: str


class ReplayRunController:
    """One durable event-time Replay run over the shared trading runtime."""

    def __init__(
        self,
        definition: ReplayRunDefinition,
        *,
        run_id: str | None = None,
        runtime_root: Path | None = None,
        resume_state: dict[str, Any] | None = None,
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
        self.speed = 1.0
        if self.definition.mode in {RunMode.BACKTEST, RunMode.BACKTEST_DEBUG}:
            self.speed = 0.0
        self.processed_events = 0
        self.warmup_events = 0
        self._task: asyncio.Task[None] | None = None
        self._condition = asyncio.Condition()
        self._stop_requested = False
        self._step_until: datetime | None = None
        self._fast_forward_until: datetime | None = None
        self._next_action_after_sequence: int | None = None
        self._navigation_target_event_type = ""
        self._navigation_target_action: dict[str, Any] | None = None
        self._navigation_prerequisite_action: dict[str, Any] | None = None
        self._navigation_skip_to_target = False
        self._last_navigation_action: dict[str, Any] | None = None
        self._navigation_started_at: datetime | None = None
        self._navigation_start_time: datetime | None = None
        self._navigation_start_processed_events = 0
        self._subscribers: set[asyncio.Queue[dict[str, Any]]] = set()
        self._last_publish_monotonic = 0.0
        self._manifest_write_task: asyncio.Task[None] | None = None
        self._manifest_write_pending = False
        self._runtime: TradingRuntime | None = None
        self._runtime_inputs_ready = False
        self._preparation_stage = "created"
        self._preparation_completed_units = 0
        self._preparation_total_units = 0
        self._strategy_frame_cache_status = "not_requested"
        self._strategy: Any | None = None
        self._strategy_registration: StrategyExecutorRegistration | None = None
        self._planner: RuntimeIbkrStrategyOrderPlanner | None = None
        self._journal: TradingJournal | None = None
        self._account_map: dict[str, str] = {}
        self._quotes: dict[str, QuoteEvent] = {}
        self._pending_passive_market_events: list[MarketEvent] = []
        self._previous_vwap: dict[tuple[str, str], tuple[datetime, float]] = {}
        self._strategy_source_values: dict[str, dict[str, Any]] = {}
        self._signal_stream_states: dict[tuple[str, str], dict[str, Any]] = {}
        self._signal_activated_tickers: set[str] = set()
        # Once a source-native signal has admitted a ticker, keep evaluating
        # that campaign after the short-lived signal episode expires (an open
        # position may still need management). Tickers that have never been
        # admitted need only cheap causal frame memory.
        self._strategy_engaged_tickers: set[str] = set()
        self._strategy_quality_admitted_tickers: set[str] = set()
        self._source_native_signal_episodes: dict[str, ReplaySignalEvent] = {}
        self._next_source_native_signal_refresh_at: datetime | None = None
        self._canvas_state_cache: tuple[float, dict[str, Any]] | None = None
        self._runtime_finished = False
        self._stream_tickers: tuple[str, ...] = ()
        self._pace_event_anchor: datetime | None = None
        self._pace_last_check_event_time: datetime | None = None
        self._pace_wall_anchor = 0.0
        self._pace_reset = True
        self._historical_watchlist_cache: list[dict[str, Any]] | None = None
        self._historical_watchlist_timeline_cache: list[dict[str, Any]] | None = None
        self._active_historical_watchlist_evidence: dict[str, dict[str, Any]] = {}
        if self.definition.mode == RunMode.BACKTEST_DEBUG:
            # Debug fixture records are the complete deterministic data
            # authority. Compiling historical plans here both weakens that
            # boundary and rejects intentionally injectable live-only fields.
            self._historical_watchlist_plans = []
            self._historical_core_signal_plans = []
        else:
            self._historical_watchlist_plans = _historical_watchlist_plans_for_configuration(
                self.definition.configuration_revision,
                start=self.definition.requested_start,
                end=self.definition.session_end,
            )
            self._historical_core_signal_plans = _historical_core_signal_plans_for_configuration(
                self.definition.configuration_revision,
                start=self.definition.session_start,
                end=self.definition.session_end,
            )
        self._historical_watchlist_timeline_index = 0
        self._active_historical_watchlist_tickers: set[str] = set()
        self._active_historical_watchlists: dict[str, set[str]] = {}
        self._active_historical_watchlist_rows: dict[
            str, dict[str, dict[str, Any]]
        ] = {}
        self._historical_external_signal_events: list[ReplaySignalEvent] = []
        self._historical_signal_identities: dict[str, dict[str, Any]] = {}
        self._strategy_quality_candidate_tickers: set[str] = set()
        self._strategy_quality_prune_ready = False
        self._historical_market_quality: dict[str, dict[str, Any]] = {}
        self._historical_prepared_structure: dict[str, dict[str, Any]] = {}
        self._historical_structure_context: dict[
            str, tuple[datetime, dict[str, Any], str, dict[str, Any]]
        ] = {}
        self._data_authority: dict[str, dict[str, Any]] = {}
        self._resume_state = deepcopy(resume_state) if resume_state is not None else None
        self._source_cursor: dict[str, Any] = {}
        self._frame_cursor: dict[str, Any] = {}
        self._processed_frames = 0
        self._last_restart_checkpoint_event_bucket = 0
        self._last_restart_checkpoint_frame_bucket = 0
        self._checkpoint_projection_cache: dict[str, Any] | None = None
        self._bar_gpt_origin_us = 0
        self._bar_gpt_prediction_origin_us = 0
        self._bar_gpt_scope_task: asyncio.Task[None] | None = None
        self._bar_gpt_pending_scope: dict[str, Any] | None = None
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
        await asyncio.to_thread(self._write_approved_configuration)
        self._write_manifest()
        self._task = asyncio.create_task(self._run(), name=f"replay-run-{self.run_id}")

    async def command(
        self,
        command: str,
        *,
        speed: float | None = None,
        target_time: clock_time | None = None,
        step_seconds: float = 1.0,
        target_event_type: str = "",
    ) -> dict[str, Any]:
        normalized = command.strip().lower()
        if normalized not in {"play", "pause", "step", "set_speed", "fast_forward", "next_action", "stop"}:
            raise ValueError(f"Unsupported Replay command: {command}")
        async with self._condition:
            if self.status in TERMINAL_REPLAY_STATUSES:
                raise ValueError(f"Replay run is already {self.status}")
            if normalized == "play":
                self.status = "running"
                self._step_until = None
                self._fast_forward_until = None
                self._next_action_after_sequence = None
                self._clear_navigation_search()
                self._pace_reset = True
            elif normalized == "pause":
                self.status = "paused"
                self._step_until = None
                self._fast_forward_until = None
                self._next_action_after_sequence = None
                self._clear_navigation_search()
            elif normalized == "step":
                if step_seconds <= 0 or step_seconds > 60:
                    raise ValueError("Replay step_seconds must be greater than zero and at most 60")
                base = self.current_time or self.definition.requested_start
                self._step_until = base + timedelta(seconds=step_seconds)
                self._fast_forward_until = None
                self._next_action_after_sequence = None
                self._clear_navigation_search()
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
                self._next_action_after_sequence = None
                self._clear_navigation_search()
                self._step_until = None
                self.status = "fast_forwarding"
            elif normalized == "next_action":
                normalized_target = target_event_type.strip().lower()
                if normalized_target and normalized_target not in STRATEGY_ACTIVITY_EVENT_TYPES:
                    raise ValueError(
                        "Replay target_event_type must be one of "
                        + ", ".join(STRATEGY_ACTIVITY_EVENT_TYPES)
                    )
                self._next_action_after_sequence = (
                    self._journal.latest_sequence(self.run_id)
                    if self._journal is not None
                    else 0
                )
                self._navigation_target_event_type = normalized_target
                self._last_navigation_action = None
                self._navigation_started_at = datetime.now(UTC)
                self._navigation_start_time = self.current_time or self.definition.requested_start
                self._navigation_start_processed_events = self.processed_events
                current = self.current_time or self.definition.requested_start
                next_source_record = (
                    self._journal.next_record_after_time(
                        self.run_id,
                        current,
                        categories=("market_discovery_signal",),
                    )
                    if self._journal is not None
                    else None
                )
                source_action = (
                    _replay_navigation_action(next_source_record)
                    if next_source_record is not None
                    else None
                )
                self._navigation_target_action = (
                    _replay_navigation_action(next_source_record, normalized_target)
                    if next_source_record is not None
                    else None
                )
                self._navigation_prerequisite_action = source_action
                self._navigation_skip_to_target = await self._can_skip_to_navigation_target()
                self._fast_forward_until = None
                self._step_until = None
                self.status = "fast_forwarding"
            else:
                self._stop_requested = True
                self._clear_navigation_search()
                self.status = "stopped"
            self.updated_at = datetime.now(UTC)
            self._condition.notify_all()
        self._flush_passive_market_events()
        await self._publish(
            force=True,
            allow_navigation=normalized == "next_action",
        )
        if normalized in {"pause", "stop"}:
            self._schedule_manifest_write()
        return self.stream_snapshot()

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
        action_definition = resolve_trading_action(
            action_id=str(payload.get("action_id") or ""),
            runtime_action=action,
        )
        action_id = str(action_definition["action_id"])
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
                "action_id": action_id,
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
                "action_id": action_id,
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

    def _strategy_debug_sources(self) -> dict[str, list[str]]:
        configuration = dict(self.definition.configuration_revision.get("payload") or {})
        run_plan = dict(configuration.get("run_plan") or {})
        activation = dict(configuration.get("signal_activation") or {})
        signal_stream_ids = [
            str(row.get("signal_stream_id") or "")
            for row in activation.get("signal_streams") or []
            if bool(row.get("enabled", True)) and str(row.get("signal_stream_id") or "")
        ]
        watchlist_ids = [
            str(value)
            for value in run_plan.get("watchlist_ids") or []
            if str(value)
        ]
        watchlist_ids.extend(
            str(universe.get("scanner_view_id") or universe.get("name") or "")
            for universe in configuration.get("universes") or []
            if bool(universe.get("enabled", True))
            and str(universe.get("source") or "") == "watchlist"
            and str(universe.get("scanner_view_id") or universe.get("name") or "")
        )
        return {
            "signal_stream_ids": list(dict.fromkeys(signal_stream_ids)),
            "watchlist_ids": list(dict.fromkeys(watchlist_ids)),
        }

    def _clear_navigation_search(self) -> None:
        self._next_action_after_sequence = None
        self._navigation_target_event_type = ""
        self._navigation_target_action = None
        self._navigation_prerequisite_action = None
        self._navigation_skip_to_target = False
        self._navigation_started_at = None
        self._navigation_start_time = None
        self._navigation_start_processed_events = self.processed_events

    def _navigation_search_projection(self) -> dict[str, Any]:
        active = self._next_action_after_sequence is not None
        scanned_events = (
            max(0, self.processed_events - self._navigation_start_processed_events)
            if active
            else 0
        )
        elapsed_seconds = (
            max(
                0.0,
                (datetime.now(UTC) - self._navigation_started_at).total_seconds(),
            )
            if active and self._navigation_started_at is not None
            else 0.0
        )
        target_time = (
            _optional_checkpoint_time(
                (
                    self._navigation_target_action
                    or self._navigation_prerequisite_action
                    or {}
                ).get("event_time")
            )
            if self._navigation_target_action is not None
            or self._navigation_prerequisite_action is not None
            else None
        )
        scanned_through = self.current_time if active else None
        start_time = self._navigation_start_time
        known_target_progress = (
            max(
                0.0,
                min(
                    1.0,
                    (scanned_through - start_time).total_seconds()
                    / max(0.001, (target_time - start_time).total_seconds()),
                ),
            )
            if scanned_through is not None
            and start_time is not None
            and target_time is not None
            and target_time > start_time
            else None
        )
        return {
            "active": active,
            "phase": "scanning" if active and self._runtime_inputs_ready else "preparing" if active else "idle",
            "started_at": (
                self._navigation_started_at.isoformat()
                if self._navigation_started_at is not None
                else None
            ),
            "start_event_time": (
                self._navigation_start_time.isoformat()
                if self._navigation_start_time is not None
                else None
            ),
            "scanned_events": scanned_events,
            "scanned_through_event_time": (
                scanned_through.isoformat() if scanned_through is not None else None
            ),
            "elapsed_seconds": elapsed_seconds,
            "events_per_second": (
                scanned_events / elapsed_seconds if elapsed_seconds > 0 else 0.0
            ),
            "known_target_progress": known_target_progress,
            "targets": list(STRATEGY_ACTIVITY_EVENT_TYPES),
            "target_event_type": self._navigation_target_event_type,
            "target_event_time": (
                target_time.isoformat() if target_time is not None else None
            ),
        }

    async def _can_skip_to_navigation_target(self) -> bool:
        """Allow raw-event skipping only before a source-native activation boundary."""

        if self._navigation_prerequisite_action is None or self._runtime is None:
            return False
        activation = dict(
            self.definition.configuration_revision["payload"].get("signal_activation")
            or {}
        )
        enabled_streams = [
            dict(stream)
            for stream in activation.get("signal_streams") or []
            if bool(stream.get("enabled", True))
        ]
        if not enabled_streams or not all(
            str(stream.get("occurrence_source") or "") == "qmd_squeeze_episode"
            for stream in enabled_streams
        ):
            return False
        if self._signal_activated_tickers:
            return False
        for account_id in self.account_ids:
            positions = await self._runtime.broker.positions(account_id)
            if any(abs(float(position.position)) > 0 for position in positions):
                return False
        return True

    def _navigation_skip_boundary(self) -> datetime | None:
        if (
            not self._navigation_skip_to_target
            or self._navigation_prerequisite_action is None
        ):
            return None
        return _optional_checkpoint_time(
            self._navigation_prerequisite_action.get("event_time")
        )

    def _transport_mode(self) -> str:
        if self._next_action_after_sequence is not None:
            return "next_action"
        if self._fast_forward_until is not None:
            return "fast_forward"
        if self._step_until is not None:
            return "step"
        if self.status == "running":
            return "play"
        return self.status

    def snapshot(self, *, include_details: bool = True) -> dict[str, Any]:
        current = self.current_time or self.definition.session_start
        checkpoint = self._checkpoint_projection()
        duration = max(
            1.0,
            (self.definition.session_end - self.definition.requested_start).total_seconds(),
        )
        elapsed = max(0.0, (current - self.definition.requested_start).total_seconds())
        payload = {
            "schema_version": 1,
            "mode": self.definition.mode.value,
            "run_id": self.run_id,
            "status": self.status,
            "runtime_ready": self._runtime_inputs_ready,
            "preparation_stage": self._preparation_stage,
            "preparation_progress": {
                "completed": self._preparation_completed_units,
                "total": self._preparation_total_units,
            },
            "preparation_cache": {
                "strategy_frames": self._strategy_frame_cache_status,
            },
            "execution_mode": self.definition.execution_mode,
            "strategy_debug_sources": self._strategy_debug_sources(),
            "error": self.error,
            "created_at": self.created_at.isoformat(),
            "updated_at": self.updated_at.isoformat(),
            "current_time": current.isoformat(),
            "session_date": self.definition.session_date.isoformat(),
            "requested_start": self.definition.requested_start.isoformat(),
            "session_start": self.definition.session_start.isoformat(),
            "session_end": self.definition.session_end.isoformat(),
            "speed": self.speed,
            "transport_mode": self._transport_mode(),
            "processed_events": self.processed_events,
            "warmup_events": self.warmup_events,
            "checkpoint": checkpoint,
            "navigation_action": deepcopy(self._last_navigation_action),
            "navigation_search": self._navigation_search_projection(),
            "progress": min(1.0, elapsed / duration),
            "account_ids": list(self.account_ids),
            "account_mapping": dict(self._account_map),
            **(
                {
                    "assignments": (
                        [assignment.payload() for assignment in self._strategy.assignments()]
                        if self._strategy is not None
                        else []
                    )
                }
                if include_details
                else {
                    "assignment_count": (
                        len(self._strategy.assignments()) if self._strategy is not None else 0
                    )
                }
            ),
            "historical_watchlists": {
                "active_tickers": sorted(self._active_historical_watchlist_tickers),
                "timeline_index": self._historical_watchlist_timeline_index,
                "timeline_count": len(self._historical_watchlist_timeline_cache or ()),
            },
            "watchlist_runtime": {
                "as_of": current.isoformat(),
                "status": "ready" if self._runtime_inputs_ready else "building",
                "watchlists": [
                    {
                        "watchlist_id": watchlist_id,
                        "status": "ready" if self._runtime_inputs_ready else "building",
                        "members": [
                            deepcopy(
                                self._active_historical_watchlist_rows
                                .get(watchlist_id, {})
                                .get(ticker, {"ticker": ticker})
                            )
                            for ticker in sorted(tickers)
                        ],
                    }
                    for watchlist_id in dict.fromkeys([
                        *self._strategy_debug_sources()["watchlist_ids"],
                        *self._active_historical_watchlists.keys(),
                    ])
                    for tickers in [
                        self._active_historical_watchlists.get(watchlist_id, set())
                    ]
                ],
            },
            "data_authority": {
                "configuration": {
                    "revision_id": self.definition.configuration_revision.get("revision_id", ""),
                    "revision": self.definition.configuration_revision.get("revision", 0),
                    "content_hash": self.definition.configuration_revision.get("content_hash", ""),
                },
                **(
                    {"sources": deepcopy(self._data_authority)}
                    if include_details
                    else {"source_count": len(self._data_authority)}
                ),
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
        payload["lifecycle"] = lifecycle_projection(
            resource_type="historical_trading_run",
            resource_id=self.run_id,
            status=self.status,
            progress=float(payload["progress"]),
            completed_units=self.processed_events,
            total_units=None,
            unit="market_events",
            checkpoint=checkpoint,
            error=self.error,
            created_at=payload["created_at"],
            updated_at=payload["updated_at"],
            finished_at=(
                payload["updated_at"]
                if self.status in TERMINAL_REPLAY_STATUSES
                else None
            ),
            supported_commands=("pause", "play", "stop", "resume"),
            authority="historical_run_controller",
            mode=self.definition.mode.value,
        )
        return payload

    def stream_snapshot(self) -> dict[str, Any]:
        """Return the bounded, frequently published Replay state.

        Full assignment and source-authority evidence remains available from
        ``snapshot`` and the durable manifest. Neither collection is consumed
        by the Replay Canvas, so retransmitting it on every clock tick only
        adds serialization, websocket, and browser parsing pressure.
        """

        payload = self.snapshot(include_details=False)
        if (
            self._next_action_after_sequence is not None
            and self._navigation_start_time is not None
        ):
            # Navigation publishes transport progress while the visible Canvas
            # remains pinned. Consumers refresh expensive charts/tables once,
            # at the final causal boundary, rather than for every scanned tick.
            payload["current_time"] = self._navigation_start_time.isoformat()
            duration = max(
                1.0,
                (
                    self.definition.session_end
                    - self.definition.requested_start
                ).total_seconds(),
            )
            payload["progress"] = max(
                0.0,
                (
                    self._navigation_start_time
                    - self.definition.requested_start
                ).total_seconds()
                / duration,
            )
        return payload

    def _checkpoint_projection(self) -> dict[str, Any]:
        if self._checkpoint_projection_cache is not None:
            return deepcopy(self._checkpoint_projection_cache)
        persisted = (
            self._journal.load_checkpoint(self.run_id)
            if self._journal is not None
            else None
        )
        if persisted is None:
            projection = {
                "status": "pending",
                "cursor": "",
                "event_time": None,
                "updated_at": None,
                "processed_events": 0,
                "interval_events": self._restart_checkpoint_interval_events(),
                "resume_supported": False,
            }
            self._checkpoint_projection_cache = projection
            return deepcopy(projection)
        state = dict(persisted.get("state") or {})
        restart_ready = (
            int(state.get("schema_version") or 0) == RESTART_CHECKPOINT_SCHEMA_VERSION
            and bool(state.get("complete"))
            and isinstance(state.get("broker"), dict)
            and isinstance(state.get("controller"), dict)
            and isinstance(state.get("identity"), dict)
            and isinstance(state.get("runtime"), dict)
        )
        projection = {
            "status": "available",
            "cursor": str(persisted.get("cursor") or ""),
            "event_time": persisted.get("event_time"),
            "updated_at": persisted.get("updated_at"),
            "processed_events": int(
                dict(state.get("controller") or {}).get("processed_events")
                or state.get("processed_events")
                or 0
            ),
            "interval_events": self._restart_checkpoint_interval_events(),
            "resume_supported": restart_ready,
            "schema_version": int(state.get("schema_version") or 1),
        }
        self._checkpoint_projection_cache = projection
        return deepcopy(projection)

    def _restart_checkpoint_interval_events(self) -> int:
        return (
            BACKTEST_RESTART_CHECKPOINT_INTERVAL_EVENTS
            if self.definition.mode == RunMode.BACKTEST
            else REPLAY_RESTART_CHECKPOINT_INTERVAL_EVENTS
        )

    def _restart_checkpoint_state(self) -> dict[str, Any]:
        if self._runtime is None:
            raise RuntimeError("Historical runtime is not ready for checkpointing")
        self._flush_passive_market_events()
        broker_checkpoint = getattr(self._runtime.broker, "checkpoint_state", None)
        if broker_checkpoint is None:
            raise RuntimeError("Historical broker does not support restart checkpoints")
        return {
            "schema_version": RESTART_CHECKPOINT_SCHEMA_VERSION,
            "complete": True,
            "identity": {
                "run_id": self.run_id,
                "mode": self.definition.mode.value,
                "configuration_revision_id": self.definition.configuration_revision.get(
                    "revision_id", ""
                ),
                "configuration_content_hash": self.definition.configuration_revision.get(
                    "content_hash", ""
                ),
                "debug_fixture_content_hash": (
                    self.definition.debug_fixture.content_hash
                    if self.definition.debug_fixture is not None
                    else ""
                ),
                "account_ids": list(self.account_ids),
                "strategy_executor": (
                    {
                        "strategy_id": self._strategy_registration.strategy_id,
                        "revision": self._strategy_registration.revision,
                        "implementation": self._strategy_registration.implementation,
                        "schema_version": self._strategy_registration.executor_schema_version,
                    }
                    if self._strategy_registration is not None
                    else None
                ),
            },
            "controller": {
                "current_time": self.current_time.isoformat() if self.current_time else None,
                "processed_events": self.processed_events,
                "warmup_events": self.warmup_events,
                "source_cursor": deepcopy(self._source_cursor),
                "frame_cursor": deepcopy(self._frame_cursor),
                "processed_frames": self._processed_frames,
                "previous_vwap": [
                    {
                        "ticker": ticker,
                        "timeframe": timeframe,
                        "observed_at": observed_at.isoformat(),
                        "value": value,
                    }
                    for (ticker, timeframe), (observed_at, value) in sorted(
                        self._previous_vwap.items()
                    )
                ],
                "strategy_source_values": deepcopy(self._strategy_source_values),
                "quotes": {
                    ticker: _market_event_checkpoint(event)
                    for ticker, event in self._quotes.items()
                },
                "watchlist_timeline_index": self._historical_watchlist_timeline_index,
                "active_watchlist_tickers": sorted(
                    self._active_historical_watchlist_tickers
                ),
                "active_watchlists": {
                    watchlist_id: sorted(tickers)
                    for watchlist_id, tickers in sorted(
                        self._active_historical_watchlists.items()
                    )
                },
                "active_watchlist_rows": deepcopy(
                    self._active_historical_watchlist_rows
                ),
                "active_watchlist_evidence": deepcopy(
                    self._active_historical_watchlist_evidence
                ),
                "signal_activated_tickers": sorted(self._signal_activated_tickers),
                "strategy_engaged_tickers": sorted(self._strategy_engaged_tickers),
                "strategy_quality_admitted_tickers": sorted(
                    self._strategy_quality_admitted_tickers
                ),
                "source_native_signal_episodes": [
                    {
                        "available_at": event.available_at.isoformat(),
                        "occurrence": deepcopy(event.occurrence),
                        "source_values": deepcopy(event.source_values),
                        "ticker": event.ticker,
                    }
                    for event in self._source_native_signal_episodes.values()
                ],
                "data_authority": deepcopy(self._data_authority),
            },
            "runtime": {
                "processed_events": self._runtime.processed_events,
                "last_event_time": (
                    self._runtime.last_event_time.isoformat()
                    if self._runtime.last_event_time
                    else None
                ),
                "latest_checkpoint_cursor": self._runtime._latest_checkpoint_cursor,
            },
            "assignments": [
                assignment.payload() for assignment in self._strategy.assignments()
            ] if self._strategy is not None else [
            ],
            "broker": broker_checkpoint(),
        }

    def _save_restart_checkpoint(self, event_time: datetime) -> None:
        if self._journal is None or self._runtime is None:
            return
        state = self._restart_checkpoint_state()
        cursor = json.dumps(
            {
                "market": state["controller"]["source_cursor"],
                "frame": state["controller"]["frame_cursor"],
            },
            separators=(",", ":"),
            sort_keys=True,
        )
        self._journal.save_checkpoint(self.run_id, cursor, state, event_time)
        self._checkpoint_projection_cache = {
            "status": "available",
            "cursor": cursor,
            "event_time": event_time.isoformat(),
            "updated_at": datetime.now(UTC).isoformat(),
            "processed_events": int(
                dict(state.get("controller") or {}).get("processed_events") or 0
            ),
            "interval_events": self._restart_checkpoint_interval_events(),
            "resume_supported": True,
            "schema_version": int(state.get("schema_version") or 1),
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
        ticker = _ticker(symbol)
        records = [
            *self._journal.strategy_records(
                ticker=ticker,
                as_of=self.current_time or self.definition.requested_start,
                limit=250,
            ),
            *self._journal.order_management_records(
                ticker=ticker,
                as_of=self.current_time or self.definition.requested_start,
                limit=250,
            ),
        ]
        records.sort(key=lambda record: record.sequence)
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
        ticker_assignments = [
            row for row in assignments if str(row.get("ticker") or "").upper() == ticker
        ]
        configuration = self.definition.configuration_revision["payload"]
        strategy_configuration = dict(configuration.get("strategy") or {})
        definition = {
            **strategy_configuration,
            "config": {"parameters": strategy_configuration.get("parameters") or {}},
        }
        strategy = {
            "fixture": False,
            "run_id": self.run_id,
            "runtime_mode": self.definition.mode.value,
            "strategy_id": strategy_configuration.get("strategy_id", ""),
            "name": strategy_configuration.get("name", "Manual trading"),
            "revision": strategy_configuration.get("revision", 0),
            "profile_id": strategy_configuration.get("profile_id"),
            "profile_revision": strategy_configuration.get("profile_revision"),
            "deployment": deepcopy(configuration.get("deployment") or {}),
            "action_definitions": deepcopy(strategy_configuration.get("action_definitions") or []),
            "action_policies": deepcopy(strategy_configuration.get("action_policies") or []),
            "automatic": self._strategy is not None,
            "state": ticker_assignments[0]["status"] if ticker_assignments else "not_assigned",
            "definition": definition,
            "assignment": ticker_assignments[0] if ticker_assignments else None,
            # Canvas is symbol-scoped. The complete assignment authority has a
            # dedicated endpoint and must not be retransmitted on every clock
            # refresh for every open chart.
            "assignments": ticker_assignments,
            "signals": [
                row
                for row in strategy_records
                if row["entity_type"] == "signal"
                and str(row.get("ticker") or "").upper() == ticker
            ],
            "decisions": [
                row
                for row in strategy_records
                if row["category"] == "strategy_decision"
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
            "run": self.stream_snapshot(),
        }

    def signal_stream_snapshot(
        self,
        *,
        signal_stream_id: str = "",
        as_of: datetime | None = None,
        limit: int = 5_000,
    ) -> dict[str, Any]:
        if self._journal is None:
            raise ValueError("Replay Signal Stream is not ready")
        from src.backend.signal_stream_runtime_service import SIGNAL_STREAM_RUNTIME

        return SIGNAL_STREAM_RUNTIME.snapshot(
            self._journal,
            signal_stream_id=signal_stream_id,
            run_id=self.run_id,
            as_of=as_of,
            limit=limit,
            configuration=self.definition.configuration_revision["payload"],
        )

    def strategy_activity_snapshot(
        self,
        *,
        as_of: datetime | None = None,
        strategy_id: str = "",
        ticker: str = "",
        event_type: str = "",
        limit: int = 500,
    ) -> dict[str, Any]:
        if self._journal is None:
            raise ValueError("Replay Strategy Activity is not ready")
        from src.backend.trading_runtime_service import strategy_activity_payload

        return strategy_activity_payload(
            journal=self._journal,
            as_of=as_of,
            strategy_id=strategy_id,
            run_id=self.run_id,
            ticker=ticker,
            event_type=event_type,
            limit=limit,
        )

    def subscribe(self) -> asyncio.Queue[dict[str, Any]]:
        queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue(maxsize=4)
        self._subscribers.add(queue)
        return queue

    def unsubscribe(self, queue: asyncio.Queue[dict[str, Any]]) -> None:
        self._subscribers.discard(queue)

    async def _run(self) -> None:
        try:
            if self.status == "created":
                self.status = "warming"
            self.updated_at = datetime.now(UTC)
            await self._publish(force=True)
            self._journal = TradingJournal(self.run_dir / "journal.sqlite3")
            self._preparation_stage = "signal_occurrences"
            await self._publish(force=True)
            self._historical_external_signal_events = (
                await self._load_historical_signal_events()
            )
            self._historical_external_signal_events.sort(
                key=lambda row: (
                    row.available_at,
                    row.ticker,
                    str(row.occurrence.get("signal_stream_id") or ""),
                    str(row.occurrence.get("event_id") or ""),
                )
            )
            configuration = self.definition.configuration_revision["payload"]
            run_plan = dict(configuration.get("run_plan") or {})
            source_native_identity_only = (
                bool(self._historical_external_signal_events)
                and str(
                    dict(run_plan.get("activation") or {}).get(
                        "watchlist_policy"
                    )
                    or "any_selected"
                )
                == "not_required"
            )
            if source_native_identity_only:
                self._preparation_stage = "signal_identity"
                await self._publish(force=True)
                self._historical_signal_identities = await asyncio.to_thread(
                    _historical_source_native_signal_identities,
                    self._historical_watchlist_plans,
                    self._historical_external_signal_events,
                )
                self._preparation_stage = "strategy_quality_admission"
                await self._publish(force=True)
                self._strategy_quality_candidate_tickers = await asyncio.to_thread(
                    _historical_strategy_quality_candidate_tickers,
                    self._historical_watchlist_plans,
                    tuple(sorted(self._historical_signal_identities)),
                )
                self._strategy_quality_prune_ready = True
                self._record_data_authority(
                    "strategy_quality_admission",
                    {
                        "authority": "compiled_historical_watchlist_plan",
                        "watchlist_id": "squeeze-tradable-candidates",
                        "source_signal_ticker_count": len(
                            self._historical_signal_identities
                        ),
                        "ever_eligible_ticker_count": len(
                            self._strategy_quality_candidate_tickers
                        ),
                        "use": "necessary-condition computation prune only",
                    },
                )
                # Entry eligibility belongs to the Strategy rule graph.  The
                # configured Watchlist remains a presentation surface, not a
                # second all-market admission gate for source-native runs.
                self._historical_watchlist_plans = []
            else:
                self._historical_watchlist_plans = (
                    _historical_watchlist_plans_at_source_native_events(
                        self._historical_watchlist_plans,
                        self._historical_external_signal_events,
                        configuration=configuration,
                    )
                )
            self._preparation_stage = "watchlist_membership"
            await self._publish(force=True)
            await self._prepare_historical_watchlist_timeline()
            self._preparation_stage = "strategy_runtime"
            await self._publish(force=True)
            await self._initialize_runtime()
            self._record_historical_watchlist_authority()
            self._preparation_stage = "strategy_frames"
            await self._publish(force=True)
            frame_source = await self._load_strategy_frames()
            frame_iterator = iter(frame_source)
            next_frame = next(frame_iterator, None)
            external_index = 0
            if self._resume_state is not None and self._frame_cursor:
                while (
                    next_frame is not None
                    and not _frame_after_cursor(next_frame, self._frame_cursor)
                ):
                    next_frame = next(frame_iterator, None)
            if self._resume_state is not None and self.current_time is not None:
                while (
                    external_index < len(self._historical_external_signal_events)
                    and self._historical_external_signal_events[external_index].available_at
                    <= self.current_time
                ):
                    external_index += 1
            self._stream_tickers = self._resolved_tickers()
            self._preparation_stage = "market_events"
            await self._publish(force=True)
            async for events in self._market_event_batches():
                if not self._runtime_inputs_ready:
                    self._runtime_inputs_ready = True
                    self._preparation_stage = "ready"
                    self.updated_at = datetime.now(UTC)
                    await self._publish(force=True)
                for event_index, event in enumerate(events, start=1):
                    if self._resume_state is not None and self._source_cursor and not _event_after_cursor(
                        event, self._source_cursor
                    ):
                        continue
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
                        broker_has_orders = bool(
                            self._runtime is not None
                            and getattr(self._runtime.broker, "has_orders", True)
                        )
                        pace_due = (
                            self._pace_reset
                            or broker_has_orders
                            or self._pace_last_check_event_time is None
                            or (
                                event.ts - self._pace_last_check_event_time
                            ).total_seconds()
                            >= 0.05
                        )
                        if pace_due:
                            await self._pace(event)
                            self._pace_last_check_event_time = event.ts
                    navigation_skip_boundary = self._navigation_skip_boundary()
                    if (
                        event.ts >= self.definition.requested_start
                        and navigation_skip_boundary is not None
                        and event.ts < navigation_skip_boundary
                    ):
                        while next_frame is not None and next_frame.as_of <= event.ts:
                            frame = next_frame
                            while (
                                external_index < len(self._historical_external_signal_events)
                                and self._historical_external_signal_events[external_index].available_at
                                <= frame.as_of
                            ):
                                signal_event = self._historical_external_signal_events[external_index]
                                await self._process_external_signal_event(signal_event)
                                external_index += 1
                            self._apply_historical_watchlist_membership(frame.as_of)
                            # Before the first source-native activation the
                            # strategy cannot act. Preserve only causal VWAP
                            # memory; full evaluation starts at the activation
                            # boundary instead of scanning every assignment for
                            # every pre-signal frame.
                            self._remember_strategy_frame(frame)
                            next_frame = next(frame_iterator, None)
                        if isinstance(event, QuoteEvent):
                            self._quotes[event.ticker] = event
                        self._source_cursor = {
                            "ts": event.ts.astimezone(UTC).isoformat(),
                            "ticker": event.ticker,
                            "sequence": int(event.sequence),
                            "kind": event.kind,
                        }
                        self.processed_events += 1
                        self.current_time = event.ts
                        self.updated_at = datetime.now(UTC)
                        if event_index % 250 == 0:
                            await self._publish()
                        if event_index % 25 == 0:
                            await asyncio.sleep(0)
                        continue
                    while next_frame is not None and next_frame.as_of <= event.ts:
                        frame = next_frame
                        while (
                            external_index < len(self._historical_external_signal_events)
                            and self._historical_external_signal_events[external_index].available_at
                            <= frame.as_of
                        ):
                            signal_event = self._historical_external_signal_events[external_index]
                            await self._process_external_signal_event(signal_event)
                            external_index += 1
                        if frame.as_of < self.definition.requested_start:
                            self._remember_strategy_frame(frame)
                        else:
                            self._apply_historical_watchlist_membership(frame.as_of)
                            await self._wait_until_active()
                            if self._stop_requested:
                                await self._finish("stopped")
                                return
                            if await self._process_strategy_frame(frame):
                                await self._after_event(frame.as_of)
                        next_frame = next(frame_iterator, None)
                    while (
                        external_index < len(self._historical_external_signal_events)
                        and self._historical_external_signal_events[external_index].available_at
                        <= event.ts
                    ):
                        signal_event = self._historical_external_signal_events[external_index]
                        await self._process_external_signal_event(signal_event)
                        external_index += 1
                    if event.ts >= self.definition.requested_start:
                        self._apply_historical_watchlist_membership(event.ts)
                        await self._wait_until_active()
                        if self._stop_requested:
                            await self._finish("stopped")
                            return
                    await self._process_market_event(
                        event,
                        # This strategy evaluates normalized causal observations
                        # in _process_strategy_frame. Its raw-event callback is a
                        # deliberate no-op, so invoking it for every quote/trade
                        # only reduces Replay throughput.
                        evaluate_strategy=False,
                    )
                    if event.ts < self.definition.requested_start:
                        self.warmup_events += 1
                    else:
                        self.processed_events += 1
                        exact_transport_boundary = (
                            self._step_until is not None
                            and event.ts >= self._step_until
                        ) or (
                            self._fast_forward_until is not None
                            and event.ts >= self._fast_forward_until
                        )
                        if exact_transport_boundary or event_index % 100 == 0:
                            await self._after_event(event.ts)
                        else:
                            self.current_time = event.ts
                            self.updated_at = datetime.now(UTC)
                    if event_index % 25 == 0:
                        await asyncio.sleep(0)
            if not self._runtime_inputs_ready:
                # A valid empty market stream (including derived-frame-only
                # debug fixtures) is fully prepared once source iteration
                # completes; it must not remain permanently "warming".
                self._runtime_inputs_ready = True
                self._preparation_stage = "ready"
                self.updated_at = datetime.now(UTC)
                await self._publish(force=True)
            if (
                next_frame is not None
                and self.status == "warming"
                and self.definition.mode == RunMode.BACKTEST_DEBUG
            ):
                self.status = "running"
                self.current_time = self.definition.requested_start
                self.updated_at = datetime.now(UTC)
                await self._publish(force=True)
                self._write_manifest()
            while next_frame is not None:
                frame = next_frame
                while (
                    external_index < len(self._historical_external_signal_events)
                    and self._historical_external_signal_events[external_index].available_at
                    <= frame.as_of
                ):
                    signal_event = self._historical_external_signal_events[external_index]
                    await self._process_external_signal_event(signal_event)
                    external_index += 1
                if frame.as_of >= self.definition.requested_start:
                    self._apply_historical_watchlist_membership(frame.as_of)
                    await self._wait_until_active()
                    if await self._process_strategy_frame(frame):
                        await self._after_event(frame.as_of)
                next_frame = next(frame_iterator, None)
            while external_index < len(self._historical_external_signal_events):
                signal_event = self._historical_external_signal_events[external_index]
                await self._process_external_signal_event(signal_event)
                external_index += 1
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
            # QMD filters raw trade prints before JSON serialization, so this
            # larger cursor page amortizes storage latency without recreating
            # the former 25k-object Python conversion burst.
            batch_size=100_000,
            # Do not make Canvas readiness wait for the full throughput-sized
            # page. Subsequent pages are prefetched at 100k while Replay
            # consumes this smaller first causal window.
            initial_batch_size=10_000,
            # Strategy indicators already include causal trade/volume data;
            # simulated execution consumes quote liquidity. Raw trade prints
            # are therefore redundant in the strategy transport.
            event_kinds=("quote",),
            start_cursor=(
                {
                    "sip_timestamp_us": int(
                        _checkpoint_time(self._source_cursor["ts"]).timestamp()
                        * 1_000_000
                    ),
                    "ticker": str(self._source_cursor["ticker"]),
                    "ordinal": int(self._source_cursor["sequence"]),
                }
                if (
                    self._resume_state is not None
                    and self._source_cursor
                    and self._source_cursor.get("ticker")
                )
                else None
            ),
        )
        await source.health()
        async for batch in source.stream():
            if source.source_revision is None:
                raise RuntimeError("QMD historical event source omitted pinned authority")
            self._record_data_authority(
                "market_events",
                {
                    "authority": "qmd_history_events",
                    # Replay consumes source-native quotes only.  Strategy
                    # trade/volume indicators come from the separately pinned
                    # QMD derived-frame authority below, so transporting raw
                    # trades here would duplicate the dominant event volume.
                    "event_kinds": ["quote"],
                    "trade_volume_authority": "qmd_derived_frames",
                    **source.source_revision,
                },
            )
            yield batch.events

    async def _initialize_runtime(
        self,
        *,
        record_configuration: bool = True,
        record_lifecycle: bool = True,
        review_only: bool = False,
    ) -> None:
        configuration = self.definition.configuration_revision["payload"]
        strategy_configuration = dict(configuration.get("strategy") or {})
        strategy_enabled = self.definition.execution_mode == "strategy"
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
        if self._resume_state is not None and strategy_enabled:
            checkpoint_assignments = self._resume_state.get("assignments")
            if not isinstance(checkpoint_assignments, list):
                raise ValueError("Restart checkpoint omitted Strategy assignment state")
            assignments = [
                _strategy_assignment_from_checkpoint(dict(row))
                for row in checkpoint_assignments
            ]
        else:
            source_assignments = self._selected_assignments() if strategy_enabled else []
            assignments = [
                _assignment_from_payload(
                    row,
                    account_id=simulated_by_key[str(row["account_key"])],
                    source=f"{self.definition.mode.value}:{row.get('source') or 'configured'}",
                    configuration=configuration,
                )
                for row in source_assignments
            ]
        if strategy_enabled:
            self._strategy_registration = strategy_executor(
                str(strategy_configuration["strategy_id"]),
                int(strategy_configuration["revision"]),
            )
            assignment_identities = {
                (assignment.strategy_id, assignment.strategy_revision)
                for assignment in assignments
            }
            if assignment_identities - {self._strategy_registration.key}:
                raise ValueError(
                    "Historical Run Plan contains assignments for a different Strategy executor"
                )
            self._strategy = self._strategy_registration.strategy_factory(assignments)
        else:
            self._strategy_registration = None
            self._strategy = None
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
            strategy_id=str(strategy_configuration.get("strategy_id") or "manual"),
            strategy_revision=int(strategy_configuration.get("revision") or 0),
            run_id=self.run_id,
            limit_offset_bps=float(configuration["oms"]["limit_offset_bps"]),
        )
        if self._journal is None:
            self._journal = TradingJournal(self.run_dir / "journal.sqlite3")
        if record_configuration:
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
                    str(strategy_configuration.get("strategy_id") or "manual"): float(
                        binding.get("strategy_allocation", 1.0)
                    )
                },
                strategy_mandates={
                    str(strategy_configuration.get("strategy_id") or "manual"): next(
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
            strategy_id=str(strategy_configuration.get("strategy_id") or "manual"),
            strategy_revision=int(strategy_configuration.get("revision") or 0),
            groups=groups,
        )
        broker = SimulatedBrokerAdapter(
            list(self.account_ids),
            _simulation_config(self.definition),
            mode=TradingMode(self.definition.mode.value),
        )
        if self._resume_state is not None:
            broker_state = self._resume_state.get("broker")
            if not isinstance(broker_state, dict):
                raise ValueError("Restart checkpoint omitted simulated broker state")
            broker.restore_checkpoint_state(broker_state)
        self._runtime = TradingRuntime(
            RunConfig(
                mode=self.definition.mode,
                strategy_id=str(strategy_configuration.get("strategy_id") or ""),
                strategy_revision=int(strategy_configuration.get("revision") or 0),
                account_ids=self.account_ids,
                anchor_date=self.definition.session_date,
                run_id=self.run_id,
                run_plan_id=str(
                    dict(configuration.get("run_plan") or {}).get("run_plan_id")
                    or dict(configuration.get("deployment") or {}).get("deployment_id")
                    or dict(configuration.get("session_profile") or {}).get("session_profile_id")
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
                # ReplayRunController owns the complete restart checkpoint.
                # Prevent the inner runtime's processed-count-only checkpoint
                # from overwriting that state in the shared journal row.
                checkpoint_interval_events=2**63 - 1,
            ),
            broker,
            self._strategy,
            self._journal,
            intent_planner=self._planner,
            portfolio=portfolio,
            review_only=review_only,
        )
        await self._runtime.initialize(
            record_lifecycle=record_lifecycle,
            review_only=review_only,
        )
        if self._resume_state is not None:
            self._restore_restart_checkpoint()
        if not review_only:
            self._runtime.persist_strategy_assignments(
                self.current_time or self.definition.requested_start,
                record_events=False,
            )

    def _restore_restart_checkpoint(self) -> None:
        if self._runtime is None or self._resume_state is None:
            raise RuntimeError("Restart runtime is not initialized")
        state = self._resume_state
        if (
            int(state.get("schema_version") or 0) != RESTART_CHECKPOINT_SCHEMA_VERSION
            or not bool(state.get("complete"))
        ):
            raise ValueError("Historical restart checkpoint is incomplete or unsupported")
        identity = state.get("identity")
        if not isinstance(identity, dict):
            raise ValueError("Historical restart checkpoint omitted run identity")
        expected_identity = {
            "run_id": self.run_id,
            "mode": self.definition.mode.value,
            "configuration_revision_id": self.definition.configuration_revision.get(
                "revision_id", ""
            ),
            "configuration_content_hash": self.definition.configuration_revision.get(
                "content_hash", ""
            ),
            "debug_fixture_content_hash": (
                self.definition.debug_fixture.content_hash
                if self.definition.debug_fixture is not None
                else ""
            ),
            "account_ids": list(self.account_ids),
            "strategy_executor": (
                {
                    "strategy_id": self._strategy_registration.strategy_id,
                    "revision": self._strategy_registration.revision,
                    "implementation": self._strategy_registration.implementation,
                    "schema_version": self._strategy_registration.executor_schema_version,
                }
                if self._strategy_registration is not None
                else None
            ),
        }
        if identity != expected_identity:
            raise ValueError("Historical restart checkpoint identity changed")
        controller = state.get("controller")
        runtime = state.get("runtime")
        if not isinstance(controller, dict) or not isinstance(runtime, dict):
            raise ValueError("Historical restart checkpoint omitted runtime state")
        current_time = _optional_checkpoint_time(controller.get("current_time"))
        last_event_time = _optional_checkpoint_time(runtime.get("last_event_time"))
        self.current_time = current_time
        self.processed_events = int(controller.get("processed_events") or 0)
        self.warmup_events = int(controller.get("warmup_events") or 0)
        self._source_cursor = dict(controller.get("source_cursor") or {})
        self._frame_cursor = dict(controller.get("frame_cursor") or {})
        self._processed_frames = int(controller.get("processed_frames") or 0)
        checkpoint_interval = self._restart_checkpoint_interval_events()
        self._last_restart_checkpoint_event_bucket = (
            self.processed_events // checkpoint_interval
        )
        self._last_restart_checkpoint_frame_bucket = (
            self._processed_frames // checkpoint_interval
        )
        if not self._source_cursor and not self._frame_cursor:
            raise ValueError("Historical restart checkpoint omitted all source cursors")
        self._previous_vwap = {
            (str(row["ticker"]), str(row["timeframe"])): (
                _checkpoint_time(row["observed_at"]),
                float(row["value"]),
            )
            for row in controller.get("previous_vwap") or ()
        }
        self._strategy_source_values = deepcopy(
            dict(controller.get("strategy_source_values") or {})
        )
        self._quotes = {
            str(ticker): _quote_from_checkpoint(dict(row))
            for ticker, row in dict(controller.get("quotes") or {}).items()
        }
        self._historical_watchlist_timeline_index = int(
            controller.get("watchlist_timeline_index") or 0
        )
        self._active_historical_watchlist_tickers = {
            str(value).upper()
            for value in controller.get("active_watchlist_tickers") or ()
        }
        self._active_historical_watchlists = {
            str(watchlist_id): {
                str(value).upper() for value in tickers if str(value).strip()
            }
            for watchlist_id, tickers in dict(
                controller.get("active_watchlists") or {}
            ).items()
        }
        restored_rows = dict(controller.get("active_watchlist_rows") or {})
        self._active_historical_watchlist_rows = {
            str(watchlist_id): {
                str(ticker).upper(): deepcopy(dict(row))
                for ticker, row in dict(rows).items()
                if str(ticker).strip()
            }
            for watchlist_id, rows in restored_rows.items()
        }
        self._active_historical_watchlist_evidence = deepcopy(
            dict(controller.get("active_watchlist_evidence") or {})
        )
        if not self._active_historical_watchlist_rows:
            # Schema-v2 checkpoints did not persist per-Watchlist evidence.
            # Reconstruct the best exact state available from their membership
            # sets and the persisted union evidence.
            self._active_historical_watchlist_rows = {
                watchlist_id: {
                    ticker: {
                        "ticker": ticker,
                        **deepcopy(
                            self._active_historical_watchlist_evidence.get(ticker, {})
                        ),
                        "watchlist_ids": [watchlist_id],
                    }
                    for ticker in tickers
                }
                for watchlist_id, tickers in self._active_historical_watchlists.items()
            }
        self._signal_activated_tickers = {
            str(value).upper()
            for value in controller.get("signal_activated_tickers") or ()
        }
        self._strategy_engaged_tickers = {
            str(value).upper()
            for value in controller.get("strategy_engaged_tickers") or ()
        } | self._signal_activated_tickers
        self._strategy_quality_admitted_tickers = {
            str(value).upper()
            for value in controller.get("strategy_quality_admitted_tickers") or ()
        }
        self._source_native_signal_episodes = {
            str(row.get("ticker") or "").upper(): ReplaySignalEvent(
                available_at=_checkpoint_time(row["available_at"]),
                occurrence=deepcopy(dict(row.get("occurrence") or {})),
                source_values=deepcopy(dict(row.get("source_values") or {})),
                ticker=str(row.get("ticker") or "").upper(),
            )
            for row in controller.get("source_native_signal_episodes") or ()
            if str(row.get("ticker") or "").strip()
        }
        self._next_source_native_signal_refresh_at = min(
            (
                _optional_checkpoint_time(event.occurrence.get("squeeze_expires_at"))
                or event.available_at + timedelta(minutes=5)
                for event in self._source_native_signal_episodes.values()
            ),
            default=None,
        )
        self._data_authority = deepcopy(dict(controller.get("data_authority") or {}))
        self._runtime.processed_events = int(runtime.get("processed_events") or 0)
        self._runtime.last_event_time = last_event_time
        self._runtime._latest_checkpoint_cursor = str(
            runtime.get("latest_checkpoint_cursor") or ""
        )
        self.status = (
            "running"
            if self.definition.mode in {RunMode.BACKTEST, RunMode.BACKTEST_DEBUG}
            else "paused"
        )
        self.updated_at = datetime.now(UTC)

    async def _process_market_event(
        self,
        event: MarketEvent,
        *,
        evaluate_strategy: bool = True,
    ) -> None:
        if self._runtime is None:
            raise RuntimeError("Replay runtime was not initialized")
        self._observe_historical_market_quality_event(event)
        if isinstance(event, QuoteEvent):
            self._quotes[event.ticker] = event
        if (
            not evaluate_strategy
            and not bool(getattr(self._runtime.broker, "has_orders", True))
        ):
            self._pending_passive_market_events.append(event)
            if len(self._pending_passive_market_events) >= 100:
                self._flush_passive_market_events()
        else:
            self._flush_passive_market_events()
            await self._runtime.process_event(event, evaluate_strategy=evaluate_strategy)
        self._source_cursor = {
            "ts": event.ts.astimezone(UTC).isoformat(),
            "ticker": event.ticker,
            "sequence": int(event.sequence),
            "kind": event.kind,
        }

    def _observe_historical_market_quality_event(self, event: MarketEvent) -> None:
        """Accumulate causal absolute-liquidity facts from their raw authority."""

        state = self._historical_market_quality.setdefault(
            event.ticker,
            {
                "dollar_volume": 0.0,
                "share_volume": 0.0,
                "trade_buckets": [],
                "volume_buckets": [],
                "raw_authority": False,
            },
        )
        if isinstance(event, QuoteEvent):
            midpoint = event.midpoint
            if midpoint > 0 and event.ask_price >= event.bid_price > 0:
                state["spread_bps"] = (event.ask_price - event.bid_price) / midpoint * 10_000
            return
        if event.price <= 0 or event.size <= 0:
            return
        state["raw_authority"] = True
        state["dollar_volume"] = float(state.get("dollar_volume") or 0) + event.price * event.size
        state["share_volume"] = float(state.get("share_volume") or 0) + event.size
        cutoff = event.ts - timedelta(seconds=60)
        trade_buckets = [
            (clock, count)
            for clock, count in list(state.get("trade_buckets") or [])
            if clock > cutoff
        ]
        if trade_buckets and trade_buckets[-1][0] == event.ts:
            trade_buckets[-1] = (event.ts, trade_buckets[-1][1] + 1)
        else:
            trade_buckets.append((event.ts, 1))
        state["trade_buckets"] = trade_buckets
        second = int(event.ts.timestamp())
        volume_buckets = [
            (bucket, volume)
            for bucket, volume in list(state.get("volume_buckets") or [])
            if bucket >= second - 2
        ]
        if volume_buckets and volume_buckets[-1][0] == second:
            volume_buckets[-1] = (second, volume_buckets[-1][1] + event.size)
        else:
            volume_buckets.append((second, event.size))
        state["volume_buckets"] = volume_buckets

    def _flush_passive_market_events(self) -> None:
        if not self._pending_passive_market_events:
            return
        if self._runtime is None:
            raise RuntimeError("Replay runtime was not initialized")
        pending = self._pending_passive_market_events
        self._pending_passive_market_events = []
        self._runtime.process_passive_market_events(pending)

    async def _process_strategy_frame(self, frame: ReplayDerivedFrame) -> bool:
        if self._runtime is None or self._strategy is None:
            return False
        self._flush_passive_market_events()
        self._refresh_source_native_signal_activation(frame.as_of)
        source_cache = self._strategy_source_values.setdefault(frame.ticker, {})
        self._project_historical_market_quality(frame, source_cache)
        if frame.ticker in self._signal_activated_tickers:
            self._strategy_engaged_tickers.add(frame.ticker)
        activation = dict(
            self.definition.configuration_revision["payload"].get("signal_activation")
            or {}
        )
        enabled_streams = [
            dict(stream)
            for stream in activation.get("signal_streams") or []
            if bool(stream.get("enabled", True))
        ]
        source_native_only = bool(enabled_streams) and all(
            str(stream.get("occurrence_source") or "") == "qmd_squeeze_episode"
            for stream in enabled_streams
        )
        if source_native_only and frame.ticker not in self._strategy_engaged_tickers:
            # The configured strategy is forbidden to evaluate before its
            # source-native Early Squeeze occurrence. Avoid projecting rule
            # fields and scanning assignments for hundreds of unrelated
            # tickers while preserving the causal VWAP history needed if this
            # ticker is admitted later.
            self._remember_strategy_frame(frame)
            self._frame_cursor = {
                "as_of": frame.as_of.astimezone(UTC).isoformat(),
                "ticker": frame.ticker,
                "timeframe": frame.timeframe,
                "sequence": frame.sequence,
            }
            self._processed_frames += 1
            if self._processed_frames % 64 == 0:
                await asyncio.sleep(0)
            return False
        if (
            source_native_only
            and frame.ticker not in self._strategy_quality_admitted_tickers
            and frame.timeframe != "1s"
        ):
            # The approved volume/spread-quality gate is entirely one-second
            # and event/session sourced. Before it passes, higher-frequency
            # veto evaluation cannot authorize an entry and only creates
            # redundant wait decisions.
            self._remember_strategy_frame(frame)
            self._frame_cursor = {
                "as_of": frame.as_of.astimezone(UTC).isoformat(),
                "ticker": frame.ticker,
                "timeframe": frame.timeframe,
                "sequence": frame.sequence,
            }
            self._processed_frames += 1
            if self._processed_frames % 64 == 0:
                await asyncio.sleep(0)
            return False
        await self._ensure_bar_gpt_features(frame.as_of)
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
        prepared_structure = self._historical_prepared_structure.setdefault(
            frame.ticker, {}
        )
        for key in _STRATEGY_LAZY_STRUCTURE_FIELDS:
            if key in indicator:
                prepared_structure[key] = deepcopy(indicator[key])
        if "qmd_structure_unified_levels" in indicator:
            prepared_structure["qmd_structure_unified_levels"] = deepcopy(
                indicator["qmd_structure_unified_levels"]
            )
        elif isinstance(indicator.get("qmd_structure_unified_level_delta"), Mapping):
            current_levels = {
                (int(row.get("side") or 0), str(row.get("unified_level_id") or "")): dict(row)
                for row in prepared_structure.get("qmd_structure_unified_levels") or ()
                if isinstance(row, Mapping)
            }
            delta = dict(indicator["qmd_structure_unified_level_delta"])
            for row in delta.get("removed") or ():
                if isinstance(row, Mapping):
                    current_levels.pop(
                        (int(row.get("side") or 0), str(row.get("unified_level_id") or "")),
                        None,
                    )
            for row in delta.get("upserts") or ():
                if isinstance(row, Mapping):
                    current_levels[
                        (int(row.get("side") or 0), str(row.get("unified_level_id") or ""))
                    ] = dict(row)
            prepared_structure["qmd_structure_unified_levels"] = [
                current_levels[key] for key in sorted(current_levels)
            ]
        structural_indicator = {**prepared_structure, **indicator}
        unified_levels = tuple(
            dict(row)
            for row in structural_indicator.get("qmd_structure_unified_levels") or ()
            if isinstance(row, Mapping)
        )
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
            swing_high=_optional_positive(
                structural_indicator.get("structure_swing_high")
            ),
            swing_low=_optional_positive(
                structural_indicator.get("structure_swing_low")
            ),
            structural_support_price=_optional_positive(
                structural_indicator.get("qmd_structure_support_price")
            ),
            structural_support_lower=_optional_positive(
                structural_indicator.get("qmd_structure_support_lower")
            ),
            structural_support_upper=_optional_positive(
                structural_indicator.get("qmd_structure_support_upper")
            ),
            structural_support_strength=float(
                structural_indicator.get("qmd_structure_support_strength") or 0
            ),
            structural_support_confidence=float(
                structural_indicator.get("qmd_structure_support_confidence") or 0
            ),
            structural_resistance_price=_optional_positive(
                structural_indicator.get("qmd_structure_resistance_price")
            ),
            structural_resistance_lower=_optional_positive(
                structural_indicator.get("qmd_structure_resistance_lower")
            ),
            structural_resistance_upper=_optional_positive(
                structural_indicator.get("qmd_structure_resistance_upper")
            ),
            structural_resistance_strength=float(
                structural_indicator.get("qmd_structure_resistance_strength") or 0
            ),
            structural_resistance_confidence=float(
                structural_indicator.get("qmd_structure_resistance_confidence") or 0
            ),
            structural_support_levels=tuple(
                row for row in unified_levels if int(row.get("side") or 0) > 0
            ),
            structural_resistance_levels=tuple(
                row for row in unified_levels if int(row.get("side") or 0) < 0
            ),
            structural_up_probability=float(
                structural_indicator.get("qmd_structure_up_probability") or 0.5
            ),
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
            acceleration=float(indicator.get("price_change_1_bar_pct") or 0),
            volatility=float(indicator.get("atr_14") or 0),
            upper_luld_price=_optional_positive(indicator.get("structure_luld_upper")),
            # `market_open` is the strategy's tradability gate, not an RTH-only
            # label. US equities are routable in the configured extended-hours
            # session; order intents separately mark outside-RTH routing.
            market_open=clock_time(4, 0) <= observed_market_time < clock_time(20, 0),
            source_signal_ids=(f"qmd-derived:{frame.ticker}:{frame.timeframe}:{frame.sequence}",),
            source_timeframe=frame.timeframe,
        )
        if self._strategy_registration is None:
            raise RuntimeError("Historical Strategy executor registration is unavailable")
        changed_source_values = self._strategy_registration.observation_projector(
            base, frame.timeframe
        )
        source_cache.update(changed_source_values)
        source_cache.update(self._project_signal_data_fields(frame))
        evaluation_events = ["indicator_update", "bar_close"]
        changed_source_ids = [
            source_key
            for source_key in changed_source_values
            if not source_key.startswith("signal.")
        ]
        if frame.signals:
            evaluation_events.append("signal_event")
            for source in self._strategy_registration.input_catalog_factory():
                source_id = str(source["source_id"])
                if not source_id.startswith("signal."):
                    continue
                runtime_field = str(source["runtime_field"])
                signal_key = runtime_field.removesuffix("_score")
                if f"{signal_key}@{frame.timeframe}" in frame.signals:
                    changed_source_ids.append(f"{source_id}@{frame.timeframe}")
        self._apply_historical_signal_streams(frame, source_cache)
        base = replace(
            base,
            changed_source_ids=tuple(changed_source_ids),
            evaluation_events=tuple(evaluation_events),
            # Signal-stream projections must be part of the immutable
            # observation evaluated below.  Copying first left veto and
            # confirmation signals one frame behind the source cache.
            source_values=deepcopy(source_cache),
        )
        ticker_assignments = tuple(
            assignment
            for assignment in self._strategy.assignments()
            if assignment.ticker == frame.ticker
        )
        if frame.ticker not in self._strategy_quality_admitted_tickers:
            quality_rules = [
                dict(rule_set)
                for assignment in ticker_assignments
                for rule_set in dict(
                    dict(assignment.parameters.get("entry_rules") or {}).get(
                        "confirmation"
                    )
                    or {}
                ).get("rule_sets")
                or []
                if str(rule_set.get("rule_set_id") or "")
                == "strategy-squeeze-volume-spread-quality"
                and bool(rule_set.get("enabled", True))
            ]
            if not quality_rules:
                self._strategy_quality_admitted_tickers.add(frame.ticker)
            else:
                # The Strategy source cache retains causal provenance records
                # shaped as {observed_at, value}.  The vector rule evaluator
                # consumes scalar columns.  Passing the provenance structs
                # made every quality check false while the Strategy's exact
                # evaluator correctly admitted the same observation.
                quality_row = {
                    key: (
                        value.get("value")
                        if isinstance(value, Mapping) and "value" in value
                        else value
                    )
                    for key, value in source_cache.items()
                }
                quality_masks = evaluate_rule_sets_frame(
                    quality_rules,
                    [{"ticker": frame.ticker, **quality_row}],
                )
                if all(
                    bool((quality_masks.get(str(rule_set["rule_set_id"])) or [False])[0])
                    for rule_set in quality_rules
                ):
                    self._strategy_quality_admitted_tickers.add(frame.ticker)
        if (
            "qmd_structure_unified_levels" not in prepared_structure
            and self._entry_structure_context_is_actionable(
            base,
            frame,
            ticker_assignments=ticker_assignments,
            )
        ):
            structural = await self._historical_entry_structure_context(frame)
            base = replace(
                base,
                swing_high=_optional_positive(
                    structural.get("structure_swing_high")
                ),
                swing_low=_optional_positive(
                    structural.get("structure_swing_low")
                ),
                structural_support_price=_optional_positive(
                    structural.get("qmd_structure_support_price")
                ),
                structural_support_lower=_optional_positive(
                    structural.get("qmd_structure_support_lower")
                ),
                structural_support_upper=_optional_positive(
                    structural.get("qmd_structure_support_upper")
                ),
                structural_support_strength=float(
                    structural.get("qmd_structure_support_strength") or 0
                ),
                structural_support_confidence=float(
                    structural.get("qmd_structure_support_confidence") or 0
                ),
                structural_resistance_price=_optional_positive(
                    structural.get("qmd_structure_resistance_price")
                ),
                structural_resistance_lower=_optional_positive(
                    structural.get("qmd_structure_resistance_lower")
                ),
                structural_resistance_upper=_optional_positive(
                    structural.get("qmd_structure_resistance_upper")
                ),
                structural_resistance_strength=float(
                    structural.get("qmd_structure_resistance_strength") or 0
                ),
                structural_resistance_confidence=float(
                    structural.get("qmd_structure_resistance_confidence") or 0
                ),
                structural_support_levels=tuple(
                    dict(row)
                    for row in structural.get("qmd_structure_support_levels") or ()
                ),
                structural_resistance_levels=tuple(
                    dict(row)
                    for row in structural.get("qmd_structure_resistance_levels") or ()
                ),
                structural_up_probability=float(
                    structural.get("qmd_structure_up_probability") or 0.5
                ),
            )
            structural_source_values = (
                self._strategy_registration.observation_projector(
                    base, frame.timeframe
                )
            )
            source_cache.update(structural_source_values)
            base = replace(
                base,
                changed_source_ids=tuple(
                    sorted(
                        set(base.changed_source_ids)
                        | {
                            source_id
                            for source_id in structural_source_values
                            if "structure" in source_id
                        }
                    )
                ),
                source_values=deepcopy(source_cache),
            )
        for assignment in ticker_assignments:
            positions = await self._runtime.broker.positions(assignment.account_id)
            position = next(
                (row for row in positions if int(row.conid) == assignment.conid),
                None,
            )
            if not _historical_watchlist_assignment_is_observable(
                source=assignment.source,
                ticker=assignment.ticker,
                active_tickers=self._active_historical_watchlist_tickers,
                strategy_engaged_tickers=self._strategy_engaged_tickers,
                position_quantity=float(position.position if position else 0),
            ):
                continue
            if (
                bool(
                    dict(
                        self.definition.configuration_revision["payload"].get(
                            "signal_activation"
                        )
                        or {}
                    ).get("signal_streams")
                )
                and
                (position is None or float(position.position) == 0)
                and assignment.status == AssignmentStatus.WATCHING
                and assignment.ticker not in self._strategy_engaged_tickers
            ):
                continue
            observation = replace(
                base,
                position_quantity=float(position.position if position else 0),
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
        self._frame_cursor = {
            "as_of": frame.as_of.astimezone(UTC).isoformat(),
            "ticker": frame.ticker,
            "timeframe": frame.timeframe,
            "sequence": frame.sequence,
        }
        self._processed_frames += 1
        await asyncio.sleep(0)
        return True

    def _entry_structure_context_is_actionable(
        self,
        observation: StrategyObservation,
        frame: ReplayDerivedFrame,
        *,
        ticker_assignments: Sequence[StrategyAssignment],
    ) -> bool:
        if frame.timeframe != "1s":
            return False
        if frame.ticker not in self._strategy_engaged_tickers:
            return False
        # Use the exact compiled entry contract over the fully projected source
        # cache.  Direct dataclass fields can legitimately be absent on a bar
        # while the last causally eligible source value remains authoritative;
        # the Strategy evaluator uses that same cache.  A hand-written subset
        # here previously missed real entries and silently skipped structural
        # checkpoint enrichment.
        for assignment in ticker_assignments:
            if assignment.status == AssignmentStatus.MANAGING:
                return True
            if assignment.status not in {
                AssignmentStatus.WATCHING,
                AssignmentStatus.REENTRY_COOLDOWN,
            }:
                continue
            if (
                assignment.state.get("liquidity_admitted_at")
                or frame.ticker in self._strategy_quality_admitted_tickers
            ):
                # Liquidity admission is the durable start of campaign
                # monitoring. Advance the causal level book on every closed
                # one-second bar from that point onward, independently of
                # VWAP, MACD, spread, and other entry confirmations. Waiting
                # for those confirmations before loading structure loses a
                # breakout that happens on the same bar confirmation becomes
                # true and incorrectly advances the watched frontier to the
                # next overhead level.
                return True
            phase_name = (
                "reentry"
                if int(assignment.state.get("reentries") or 0) > 0
                else "initial_entry"
            )
            phase_policy = dict(assignment.parameters.get("phase_policy") or {})
            rules = (
                dict(dict(phase_policy.get(phase_name) or {}).get("rules") or {})
                if phase_name == "reentry"
                else dict(assignment.parameters.get("entry_rules") or {})
            )
            result = evaluate_entry_decision_rules(rules, observation)
            if (
                bool(dict(result.get("confirmation") or {}).get("passed"))
                and not bool(dict(result.get("veto") or {}).get("passed"))
            ):
                # Structural enrichment supplies the trigger authority itself;
                # fetching it cannot depend on the old timeframe swing trigger.
                return True
        return False

    async def _historical_entry_structure_context(
        self, frame: ReplayDerivedFrame
    ) -> dict[str, Any]:
        cached = self._historical_structure_context.get(frame.ticker)
        if cached is not None and frame.as_of - cached[0] < timedelta(seconds=1):
            return cached[1]
        if cached is None:
            payload = await asyncio.to_thread(
                qmd_historical_structure_snapshot,
                ticker=frame.ticker,
                as_of=frame.as_of.astimezone(UTC).isoformat(),
            )
            provenance = {
                "seed_authority_start": payload.get("seed_authority_start"),
                "seed_source_plan_hash": payload.get("seed_source_plan_hash"),
                "seed_source_revision_token": payload.get("seed_source_revision_token"),
            }
        else:
            payload = await asyncio.to_thread(
                qmd_advance_historical_structure_snapshot,
                session_id=cached[2],
                as_of=frame.as_of.astimezone(UTC).isoformat(),
            )
            provenance = cached[3]
        snapshot = dict(payload.get("snapshot") or {})
        support = dict(snapshot.get("support") or {})
        resistance = dict(snapshot.get("resistance") or {})
        one_second_structure = next(
            (
                dict(row)
                for row in snapshot.get("timeframe_states") or ()
                if isinstance(row, Mapping)
                and str(row.get("timeframe") or "").lower() == "1s"
            ),
            {},
        )
        unified_levels = [
            dict(row) for row in snapshot.get("unified_levels") or ()
            if isinstance(row, Mapping)
        ]
        context = {
            # Full-session prepared frames can omit sparse generic swing
            # columns. The causal historical Structure snapshot carries the
            # same 1s frontier explicitly, so historical entry must project it
            # instead of silently degrading to the nearest Unified band.
            "structure_swing_high": one_second_structure.get("swing_high"),
            "structure_swing_low": one_second_structure.get("swing_low"),
            "qmd_structure_support_price": support.get("price"),
            "qmd_structure_support_lower": support.get("lower"),
            "qmd_structure_support_upper": support.get("upper"),
            "qmd_structure_support_strength": support.get("strength"),
            "qmd_structure_support_confidence": support.get("confidence"),
            "qmd_structure_resistance_price": resistance.get("price"),
            "qmd_structure_resistance_lower": resistance.get("lower"),
            "qmd_structure_resistance_upper": resistance.get("upper"),
            "qmd_structure_resistance_strength": resistance.get("strength"),
            "qmd_structure_resistance_confidence": resistance.get("confidence"),
            "qmd_structure_support_levels": [
                row for row in unified_levels if int(row.get("side") or 0) > 0
            ],
            "qmd_structure_resistance_levels": [
                row for row in unified_levels if int(row.get("side") or 0) < 0
            ],
            "qmd_structure_up_probability": snapshot.get("up_probability"),
        }
        self._historical_structure_context[frame.ticker] = (
            frame.as_of,
            context,
            str(payload.get("session_id") or ""),
            provenance,
        )
        source_plan = dict(payload.get("source_plan") or {})
        self._record_data_authority(
            f"structure:{frame.ticker}:{frame.as_of.astimezone(UTC).isoformat()}",
            {
                "authority": "qmd_history_causal_structure_checkpoint",
                "as_of": frame.as_of.astimezone(UTC).isoformat(),
                "seed_authority_start": provenance.get("seed_authority_start"),
                "seed_source_plan_hash": provenance.get("seed_source_plan_hash"),
                "seed_source_revision_token": provenance.get(
                    "seed_source_revision_token"
                ),
                "source_plan_hash": source_plan.get("plan_hash"),
                "event_count": int(payload.get("event_count") or 0),
                "advanced_event_count": int(
                    payload.get("advanced_event_count") or 0
                ),
                "split_adjustments": list(
                    dict(payload.get("checkpoint") or {}).get("applied_split_adjustments")
                    or []
                ),
            },
        )
        return context

    def _project_historical_market_quality(
        self,
        frame: ReplayDerivedFrame,
        source_cache: dict[str, Any],
    ) -> None:
        """Project causal absolute-liquidity facts from completed one-second bars."""

        if frame.timeframe != "1s":
            return
        bar = dict(frame.bar)
        state = self._historical_market_quality.setdefault(
            frame.ticker,
            {
                "dollar_volume": 0.0,
                "share_volume": 0.0,
                "trade_buckets": [],
            },
        )
        raw_authority = bool(state.get("raw_authority"))
        if raw_authority:
            trade_buckets = [
                (clock, count)
                for clock, count in list(state.get("trade_buckets") or [])
                if clock > frame.as_of - timedelta(seconds=60)
            ]
            state["trade_buckets"] = trade_buckets
            trade_rate_10s = sum(
                count
                for clock, count in trade_buckets
                if clock > frame.as_of - timedelta(seconds=10)
            ) / 10.0
            trade_rate_60s = sum(count for _, count in trade_buckets) / 60.0
            spread_bps = state.get("spread_bps")
            volume_by_second = dict(state.get("volume_buckets") or [])
            completed_second = int(frame.as_of.timestamp()) - 1
            current_volume = float(volume_by_second.get(completed_second) or 0)
            prior_volume = float(volume_by_second.get(completed_second - 1) or 0)
            volume_rate_ratio = current_volume / prior_volume if prior_volume > 0 else None
        else:
            current_volume = max(0.0, float(bar.get("volume") or 0))
            previous_volume = float(state.get("previous_bar_volume") or 0)
            state["dollar_volume"] = float(state.get("dollar_volume") or 0) + max(
                0.0,
                float(bar.get("dollar_volume") or 0)
                or current_volume * max(0.0, float(bar.get("close") or 0)),
            )
            state["share_volume"] = float(state.get("share_volume") or 0) + max(
                0.0, current_volume
            )
            buckets = [
                (clock, count)
                for clock, count in list(state.get("trade_buckets") or [])
                if clock > frame.as_of - timedelta(seconds=60)
            ]
            buckets.append((
                frame.as_of,
                max(
                    1 if current_volume > 0 else 0,
                    int(bar.get("trade_count") or 0),
                ),
            ))
            state["trade_buckets"] = buckets
            trade_rate_10s = sum(
                count
                for clock, count in buckets
                if clock > frame.as_of - timedelta(seconds=10)
            ) / 10.0
            trade_rate_60s = sum(count for _, count in buckets) / 60.0
            spread_bps = state.get("spread_bps")
            if spread_bps is None:
                spread_bps = _positive(
                    bar.get("spread_bps_close") or bar.get("spread_bps_mean")
                )
            volume_rate_ratio = bar.get("volume_rate_ratio")
            if volume_rate_ratio is None and previous_volume > 0:
                volume_rate_ratio = current_volume / previous_volume
            state["previous_bar_volume"] = current_volume
        values = {
            "market.session_dollar_volume": state["dollar_volume"],
            "market.volume": state["share_volume"],
            "market.trade_rate_10s": trade_rate_10s,
            "market.trade_rate_60s": trade_rate_60s,
            "market.spread_bps": spread_bps,
            "volume_rate_ratio": volume_rate_ratio,
        }
        field_refs = {
            "market.session_dollar_volume": "data.market.session_dollar_volume@1:value",
            "market.volume": "data.market.volume@1:value",
            "market.trade_rate_10s": "data.market.trade_rate_10s@1:value",
            "market.trade_rate_60s": "data.market.trade_rate_60s@1:value",
            "market.spread_bps": "data.market.spread_bps@1:value",
            "volume_rate_ratio": "data.volume_rate_ratio@1:value",
        }
        for source_id, value in values.items():
            if value is None:
                continue
            record = {
                "observed_at": frame.as_of.isoformat(),
                "value": float(value),
            }
            source_cache[source_id] = record
            source_cache[field_refs[source_id]] = record
            if source_id == "volume_rate_ratio":
                source_cache[f"{source_id}@1s"] = record
                source_cache[f"{field_refs[source_id]}@1s"] = record

    async def _process_external_signal_event(self, event: ReplaySignalEvent) -> None:
        source_cache = self._strategy_source_values.setdefault(event.ticker, {})
        source_cache.update(deepcopy(event.source_values))
        configuration = self.definition.configuration_revision["payload"]
        run_plan = dict(configuration.get("run_plan") or {})
        activation = dict(run_plan.get("activation") or {})
        is_new_for_run = event.available_at >= self.definition.requested_start
        accepts_prior = str(activation.get("event_policy") or "new_occurrences") == "latest_session_occurrence"
        self._apply_historical_watchlist_membership(event.available_at)
        if is_new_for_run or accepts_prior:
            if event.occurrence.get("squeeze_expires_at"):
                self._source_native_signal_episodes[event.ticker] = event
                self._refresh_source_native_signal_activation(
                    event.available_at,
                    force=True,
                )
            elif run_plan_accepts_signal(
                run_plan,
                event.occurrence,
                eligible_tickers=self._historical_signal_eligible_tickers(run_plan),
            ):
                self._signal_activated_tickers.add(event.ticker)
                self._strategy_engaged_tickers.add(event.ticker)
        if is_new_for_run:
            await self._after_event(event.available_at)

    def _project_signal_data_fields(self, frame: ReplayDerivedFrame) -> dict[str, Any]:
        activation = dict(
            self.definition.configuration_revision["payload"].get("signal_activation") or {}
        )
        if not any(
            str(stream.get("occurrence_source") or "") != "qmd_squeeze_episode"
            for stream in activation.get("signal_streams") or []
            if bool(stream.get("enabled", True))
        ):
            # Exact squeeze occurrences are loaded from the immutable QMD
            # occurrence table. Re-projecting the broad scanner data-field
            # catalog from derived bars would introduce a second authority.
            return {}
        plan = dict(activation.get("data_field_plan") or {})
        raw = {
            "ticker": frame.ticker,
            "symbol": frame.ticker,
            "indicator_interval": frame.timeframe,
            "indicator_timeframe": frame.timeframe,
            **dict(frame.bar),
            **dict(frame.indicator),
            **dict(frame.signals),
        }
        if self._bar_gpt_fields_required():
            from src.backend.model_feature_store import MODEL_FEATURE_STORE

            raw.update(MODEL_FEATURE_STORE.scoped_fields(
                mode=self.definition.mode.value,
                scope_id=f"{self.definition.mode.value}:{self.run_id}",
                ticker=frame.ticker,
                as_of_us=int(frame.as_of.timestamp() * 1_000_000),
            ))
        projected = project_data_field_outputs(
            [raw],
            activation.get("data_fields") or [],
            field_refs=list(plan.get("field_refs") or []),
            field_instances=list(plan.get("field_instances") or []),
        )
        return dict(projected[0]) if projected else {}

    def _apply_historical_signal_streams(
        self, frame: ReplayDerivedFrame, source_values: dict[str, Any]
    ) -> None:
        if self._journal is None:
            return
        configuration = self.definition.configuration_revision["payload"]
        run_plan = dict(configuration.get("run_plan") or {})
        activation = dict(configuration.get("signal_activation") or {})
        rules = {
            str(row.get("rule_set_id") or ""): dict(row)
            for row in activation.get("rule_sets") or []
        }
        columns = {
            str(row.get("column_id") or ""): dict(row)
            for row in activation.get("column_catalog") or []
        }
        eligible = self._historical_signal_eligible_tickers(run_plan)
        for stream in activation.get("signal_streams") or []:
            stream_id = str(stream.get("signal_stream_id") or "")
            if not stream_id or not bool(stream.get("enabled", True)):
                continue
            if str(stream.get("occurrence_source") or "") == "qmd_squeeze_episode":
                # Source-native historical occurrences are processed by
                # _process_external_signal_event; do not synthesize duplicates
                # from later derived frames.
                continue
            source_type = str(stream.get("source_type") or "core_scan")
            source_watchlist_id = str(stream.get("source_id") or "")
            if (
                source_type == "watchlist"
                and frame.ticker
                not in self._active_historical_watchlists.get(
                    source_watchlist_id, set()
                )
            ):
                matches = False
            else:
                rule_ids = [
                    str(value) for value in stream.get("inclusion_rule_sets") or [] if str(value)
                ]
                masks = evaluate_rule_sets_frame(
                    (rules[rule_id] for rule_id in rule_ids if rule_id in rules),
                    [{"ticker": frame.ticker, **source_values}],
                )
                results = [bool((masks.get(rule_id) or [False])[0]) for rule_id in rule_ids]
                matches = bool(results) and (
                    any(results)
                    if str(stream.get("inclusion_operator") or "all") == "any"
                    else all(results)
                )
            key = (stream_id, frame.ticker)
            previous = self._signal_stream_states.get(key, {})
            previous_match = bool(previous.get("matching"))
            last_emitted = _optional_datetime(previous.get("last_emitted_at"))
            cooldown = timedelta(milliseconds=max(0, int(stream.get("cooldown_ms") or 0)))
            cooldown_ready = last_emitted is None or frame.as_of >= last_emitted + cooldown
            should_emit = matches and (
                not previous_match
                or (
                    str(stream.get("rearm_policy") or "after_false") == "after_cooldown"
                    and cooldown_ready
                )
            )
            next_state = {**previous, "matching": matches}
            if should_emit:
                occurrence = _historical_signal_occurrence(
                    stream,
                    frame=frame,
                    source_values=source_values,
                    columns=columns,
                )
                _, inserted = self._journal.append_once(
                    run_id=self.run_id,
                    category="market_discovery_signal",
                    entity_type="signal_occurrence",
                    entity_id=str(occurrence["event_id"]),
                    event_time=frame.as_of,
                    payload=occurrence,
                )
                if inserted and run_plan_accepts_signal(
                    run_plan,
                    occurrence,
                    eligible_tickers=eligible,
                ):
                    self._signal_activated_tickers.add(frame.ticker)
                    self._strategy_engaged_tickers.add(frame.ticker)
                next_state["last_emitted_at"] = frame.as_of.isoformat()
            self._signal_stream_states[key] = next_state

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
        self._flush_passive_market_events()
        self.current_time = event_time
        self.updated_at = datetime.now(UTC)
        # Replay BarGPT is intentionally chart-scoped while strategy entry is
        # being validated. Prewarming every assignment here competes with the
        # one ticker the operator is inspecting and makes visual UAT unusable.
        transport_boundary = False
        if self._next_action_after_sequence is not None and self._journal is not None:
            for record in self._journal.records(
                self.run_id, after_sequence=self._next_action_after_sequence
            ):
                self._next_action_after_sequence = record.sequence
                action = _replay_navigation_action(
                    record, self._navigation_target_event_type
                )
                if action is not None:
                    self._last_navigation_action = action
                    self._next_action_after_sequence = None
                    self._clear_navigation_search()
                    self.status = "paused"
                    transport_boundary = True
                    break
            if (
                self._next_action_after_sequence is not None
                and self._navigation_target_action is not None
                and (
                    target_time := _optional_checkpoint_time(
                        self._navigation_target_action.get("event_time")
                    )
                ) is not None
                and event_time >= target_time
            ):
                self._last_navigation_action = deepcopy(self._navigation_target_action)
                self._clear_navigation_search()
                self.status = "paused"
                transport_boundary = True
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
        checkpoint_interval = self._restart_checkpoint_interval_events()
        event_bucket = self.processed_events // checkpoint_interval
        frame_bucket = self._processed_frames // checkpoint_interval
        event_checkpoint_due = (
            event_bucket > self._last_restart_checkpoint_event_bucket
            and bool(self._source_cursor)
        )
        frame_checkpoint_due = (
            frame_bucket > self._last_restart_checkpoint_frame_bucket
            and bool(self._frame_cursor)
        )
        if event_checkpoint_due or frame_checkpoint_due:
            self._save_restart_checkpoint(event_time)
            self._last_restart_checkpoint_event_bucket = event_bucket
            self._last_restart_checkpoint_frame_bucket = frame_bucket
        if transport_boundary:
            self._schedule_manifest_write()

    async def _finish(self, status: str) -> None:
        self._flush_passive_market_events()
        if self._runtime is not None and not self._runtime_finished:
            await self._runtime.finish(status=status)
            self._runtime_finished = True
        if self.current_time is not None and (self._source_cursor or self._frame_cursor):
            self._save_restart_checkpoint(self.current_time)
        self._next_action_after_sequence = None
        self._clear_navigation_search()
        self.status = status
        self.updated_at = datetime.now(UTC)
        await self._publish(force=True)
        self._schedule_manifest_write()
        if self._manifest_write_task is not None:
            await self._manifest_write_task
        # Persist the complete terminal snapshot only once transport has
        # stopped.  In-flight boundary writes intentionally use the compact
        # projection so they cannot serialize hundreds of assignments while
        # the event loop is advancing them.
        await asyncio.to_thread(self._write_manifest, include_details=True)
        if self._bar_gpt_scope_task is not None:
            await asyncio.gather(self._bar_gpt_scope_task, return_exceptions=True)
        try:
            from src.backend.bar_gpt_client import remove_bar_gpt_scope
            await asyncio.to_thread(remove_bar_gpt_scope, f"{self.definition.mode.value}:{self.run_id}", 0.5)
        except Exception:
            pass

    def _schedule_bar_gpt_scope(self, event_time: datetime) -> None:
        if self._bar_gpt_fields_required():
            return
        origin_us = int(event_time.timestamp()) * 1_000_000
        if origin_us <= self._bar_gpt_origin_us:
            return
        configuration = dict(self.definition.configuration_revision.get("payload") or {})
        discovery = dict(configuration.get("market_discovery") or {})
        serving = dict(dict(discovery.get("model_serving") or {}).get("bar_gpt") or {})
        if not bool(serving.get("enabled", True)):
            return
        selected = {str(value) for value in serving.get("watchlist_ids") or ["core-candidates"] if str(value)}
        tickers = sorted({
            ticker
            for watchlist_id, members in self._active_historical_watchlists.items()
            if watchlist_id in selected
            for ticker in members
        })
        if not tickers:
            tickers = sorted(set(self._stream_tickers or self.definition.tickers))
        maximum = max(1, min(int(serving.get("maximum_tickers") or 500), 5000))
        self._bar_gpt_origin_us = origin_us
        self._bar_gpt_pending_scope = {
            "scope_id": f"{self.definition.mode.value}:{self.run_id}",
            "mode": self.definition.mode.value,
            "tickers": tickers[:maximum],
            "model_ids": [str(value) for value in serving.get("model_ids") or [] if str(value)],
            "watchlist_ids": sorted(selected),
            "trigger_mode": str(serving.get("trigger_mode") or "auto"),
            "clock_us": origin_us,
            "revision": max(1, self.processed_events + self._processed_frames),
            "ttl_ms": 60_000,
            "source": "backend.replay_run",
            "timeout": 0.75,
        }
        if self._bar_gpt_scope_task is None or self._bar_gpt_scope_task.done():
            self._bar_gpt_scope_task = asyncio.create_task(
                self._publish_pending_bar_gpt_scopes(),
                name=f"bar-gpt-scope-{self.run_id}",
            )

    def _bar_gpt_fields_required(self) -> bool:
        if self.definition.mode == RunMode.BACKTEST_DEBUG:
            # Any model outputs used by Debug must be explicit hashed fixture
            # fields. Never consult mutable service or feature-store state.
            return False
        activation = dict(
            self.definition.configuration_revision["payload"].get("signal_activation") or {}
        )
        enabled_streams = [
            dict(stream)
            for stream in activation.get("signal_streams") or []
            if bool(stream.get("enabled", True))
        ]
        if enabled_streams and all(
            str(stream.get("occurrence_source") or "") == "qmd_squeeze_episode"
            for stream in enabled_streams
        ):
            # The historical projection consumes immutable source-native
            # occurrences and QMD derived indicators; the broad UI catalog is
            # descriptive here and does not activate model feature serving.
            return False
        return "model.bargpt." in json.dumps(activation, sort_keys=True, default=str)

    async def _ensure_bar_gpt_features(self, event_time: datetime) -> None:
        if not self._bar_gpt_fields_required():
            return
        origin_us = int(event_time.timestamp() * 1_000_000)
        if origin_us <= self._bar_gpt_prediction_origin_us:
            return
        configuration = dict(self.definition.configuration_revision.get("payload") or {})
        discovery = dict(configuration.get("market_discovery") or {})
        serving = dict(dict(discovery.get("model_serving") or {}).get("bar_gpt") or {})
        if not bool(serving.get("enabled", True)):
            raise RuntimeError("BarGPT Data Fields are active but model serving is disabled")
        selected = {str(value) for value in serving.get("watchlist_ids") or ["core-candidates"] if str(value)}
        tickers = sorted({
            ticker
            for watchlist_id, members in self._active_historical_watchlists.items()
            if watchlist_id in selected
            for ticker in members
        }) or sorted(set(self._stream_tickers or self.definition.tickers))
        maximum = max(1, min(int(serving.get("maximum_tickers") or 500), 5000))
        from src.backend.bar_gpt_client import advance_bar_gpt_scope
        from src.backend.model_feature_store import MODEL_FEATURE_STORE

        result = await asyncio.to_thread(
            advance_bar_gpt_scope,
            f"{self.definition.mode.value}:{self.run_id}",
            mode=self.definition.mode.value,
            tickers=tickers[:maximum],
            model_ids=[str(value) for value in serving.get("model_ids") or [] if str(value)],
            watchlist_ids=sorted(selected),
            clock_us=origin_us,
            revision=max(1, self.processed_events + self._processed_frames),
            source="backend.replay_rule_barrier",
            timeout=float(os.environ.get("BAR_GPT_BACKTEST_INFERENCE_TIMEOUT_SECONDS", "120")),
        )
        predictions = list(result.get("predictions") or [])
        if not predictions:
            raise RuntimeError(f"BarGPT produced no predictions at historical origin {origin_us}")
        for prediction in predictions:
            MODEL_FEATURE_STORE.publish(dict(prediction))
        self._bar_gpt_prediction_origin_us = origin_us

    async def _publish_pending_bar_gpt_scopes(self) -> None:
        from src.backend.bar_gpt_client import publish_bar_gpt_scope
        while self._bar_gpt_pending_scope is not None:
            payload = self._bar_gpt_pending_scope
            self._bar_gpt_pending_scope = None
            scope_id = str(payload.pop("scope_id"))
            await asyncio.to_thread(publish_bar_gpt_scope, scope_id, **payload)

    def _schedule_manifest_write(self) -> None:
        """Coalesce durable manifest updates without blocking Replay transport."""

        self._manifest_write_pending = True
        if self._manifest_write_task is None or self._manifest_write_task.done():
            self._manifest_write_task = asyncio.create_task(
                self._write_pending_manifests(),
                name=f"replay-manifest-{self.run_id}",
            )

    async def _write_pending_manifests(self) -> None:
        while self._manifest_write_pending:
            self._manifest_write_pending = False
            await asyncio.to_thread(self._write_manifest, include_details=False)

    async def _publish(
        self,
        *,
        force: bool = False,
        allow_navigation: bool = False,
    ) -> None:
        if not self._subscribers:
            return
        now = time.monotonic()
        if not force and now - self._last_publish_monotonic < 0.25:
            return
        self._last_publish_monotonic = now
        payload = self.stream_snapshot()
        for queue in tuple(self._subscribers):
            if queue.full():
                try:
                    queue.get_nowait()
                except asyncio.QueueEmpty:
                    pass
            queue.put_nowait(payload)

    def _selected_assignments(self) -> list[dict[str, Any]]:
        configuration = self.definition.configuration_revision["payload"]
        explicit_tickers = {
            _ticker(value) for value in self.definition.tickers if str(value).strip()
        }
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
            and (
                not explicit_tickers
                or _ticker(row.get("ticker")) in explicit_tickers
            )
        ]
        historical_members = [
            member
            for member in self._historical_watchlist_members()
            if not explicit_tickers
            or _ticker(member.get("ticker")) in explicit_tickers
        ]
        existing_member_tickers = {
            str(member.get("ticker") or "").upper() for member in historical_members
        }
        signal_identities = self._historical_signal_assignment_identities()
        external_signal_members = {
            event.ticker: {
                "ticker": event.ticker,
                "ibkr_conid": int(
                    event.occurrence.get("conid")
                    or signal_identities.get(event.ticker, {}).get("ibkr_conid")
                    or 0
                ),
            }
            for event in self._historical_external_signal_events
            if event.ticker not in existing_member_tickers
            and (not explicit_tickers or event.ticker in explicit_tickers)
            and _strategy_can_enter_at(configuration, event.available_at)
        }
        run_plan = dict(configuration.get("run_plan") or {})
        if (
            list(run_plan.get("watchlist_ids") or [])
            and str(dict(run_plan.get("activation") or {}).get("watchlist_policy") or "any_selected")
            != "not_required"
        ):
            # Signals outside the causal eligibility Watchlists must never
            # create assignments (or force unresolved broker identities).
            external_signal_members = {}
        historical_members = [
            *historical_members,
            *(external_signal_members[ticker] for ticker in sorted(external_signal_members)),
        ]
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
                        "source": (
                            "historical_signal_stream"
                            if ticker in external_signal_members
                            else "historical_watchlist"
                        ),
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

    def _historical_signal_assignment_identities(self) -> dict[str, dict[str, Any]]:
        identities: dict[str, dict[str, Any]] = deepcopy(
            self._historical_signal_identities
        )
        for snapshot in self._historical_watchlist_timeline():
            for row in snapshot.get("assignment_identities") or []:
                ticker = str(row.get("ticker") or "").strip().upper()
                identity = {
                    key: deepcopy(value)
                    for key, value in dict(row).items()
                    if key != "ticker"
                }
                if not ticker or int(identity.get("ibkr_conid") or 0) <= 0:
                    continue
                prior = identities.get(ticker)
                if prior is not None and prior != identity:
                    raise ValueError(
                        f"Historical signal identity changed for {ticker}; "
                        "ticker-only assignment authority is unsafe"
                    )
                identities[ticker] = identity
        return identities

    def _historical_watchlist_members(self) -> list[dict[str, Any]]:
        if self._historical_watchlist_cache is not None:
            return self._historical_watchlist_cache
        members: dict[str, dict[str, Any]] = {}
        conids: dict[str, int] = {}
        for snapshot in self._historical_watchlist_timeline():
            rows = list(snapshot.get("members") or [])
            rows.extend(
                _historical_watchlist_transition_row(transition)
                for transition in snapshot.get("transitions") or []
                if str(transition.get("event") or "") in {"added", "rank_changed"}
            )
            for row in rows:
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
            fixture = self.definition.debug_fixture
            shared = self.definition.historical_watchlist_cache
            cache_key = hashlib.sha256(
                json.dumps(
                    self._historical_watchlist_plans,
                    default=str,
                    separators=(",", ":"),
                    sort_keys=True,
                ).encode("utf-8")
            ).hexdigest()
            cached = shared.get(cache_key) if shared is not None else None
            if cached is None:
                cached = (
                    _debug_watchlist_membership_timeline(fixture.watchlist_events)
                    if self.definition.mode == RunMode.BACKTEST_DEBUG and fixture is not None
                    else _historical_watchlist_membership_timeline_from_plans(
                        self._historical_watchlist_plans,
                        projection_tickers=self._historical_watchlist_projection_tickers(),
                    )
                )
                if shared is not None:
                    shared[cache_key] = cached
            self._historical_watchlist_timeline_cache = cached
        return self._historical_watchlist_timeline_cache

    def _historical_watchlist_projection_tickers(self) -> list[str] | None:
        configuration = self.definition.configuration_revision["payload"]
        activation = dict(configuration.get("signal_activation") or {})
        enabled_streams = [
            dict(stream)
            for stream in activation.get("signal_streams") or []
            if bool(stream.get("enabled", True))
        ]
        if not enabled_streams or not all(
            str(stream.get("occurrence_source") or "").strip()
            for stream in enabled_streams
        ):
            return None
        tickers = sorted({
            event.ticker
            for event in self._historical_external_signal_events
            if _strategy_can_enter_at(configuration, event.available_at)
        })
        return tickers or None

    async def _prepare_historical_watchlist_timeline(self) -> None:
        """Materialize historical universe authority without blocking the API loop."""
        if self._historical_watchlist_timeline_cache is not None:
            return
        await asyncio.to_thread(self._historical_watchlist_timeline)

    def _apply_historical_watchlist_membership(self, event_time: datetime) -> None:
        timeline = self._historical_watchlist_timeline()
        while self._historical_watchlist_timeline_index < len(timeline):
            snapshot = timeline[self._historical_watchlist_timeline_index]
            effective_at = snapshot["effective_at"]
            if effective_at > event_time:
                break
            transition_by_ticker: dict[str, dict[str, Any]] = {}
            if "transitions" in snapshot:
                for transition in snapshot.get("transitions") or []:
                    ticker = str(transition.get("ticker") or "").upper()
                    watchlist_id = str(transition.get("watchlist_id") or "")
                    if not ticker or not watchlist_id:
                        continue
                    transition_by_ticker[ticker] = dict(transition)
                    rows = self._active_historical_watchlist_rows.setdefault(
                        watchlist_id, {}
                    )
                    if str(transition.get("event") or "") == "removed":
                        rows.pop(ticker, None)
                    elif str(transition.get("event") or "") in {
                        "added",
                        "rank_changed",
                    }:
                        rows[ticker] = _historical_watchlist_transition_row(
                            transition
                        )
                member_rows = _historical_watchlist_union_rows(
                    self._active_historical_watchlist_rows
                )
            else:
                member_rows = [dict(row) for row in snapshot.get("members") or []]
                self._active_historical_watchlist_rows = {}
                for row in member_rows:
                    for watchlist_id in row.get("watchlist_ids") or []:
                        self._active_historical_watchlist_rows.setdefault(
                            str(watchlist_id), {}
                        )[str(row.get("ticker") or "").upper()] = dict(row)

            current = {
                str(row.get("ticker") or "").upper()
                for row in member_rows
                if str(row.get("ticker") or "").strip()
            }
            current_by_watchlist: dict[str, set[str]] = {
                watchlist_id: set(rows)
                for watchlist_id, rows in self._active_historical_watchlist_rows.items()
            }
            current_evidence: dict[str, dict[str, Any]] = {}
            for row in member_rows:
                ticker = str(row.get("ticker") or "").upper()
                if not ticker:
                    continue
                current_evidence[ticker] = {
                    str(source_id): deepcopy(value)
                    for source_id, value in row.items()
                    if source_id
                    not in {
                        "ticker",
                        "rank",
                        "score",
                        "membership_reason",
                        "watchlist_ids",
                    }
                }
                for watchlist_id in row.get("watchlist_ids") or []:
                    current_by_watchlist.setdefault(str(watchlist_id), set()).add(ticker)
            added = sorted(current - self._active_historical_watchlist_tickers)
            removed = sorted(self._active_historical_watchlist_tickers - current)
            previous_evidence = self._active_historical_watchlist_evidence
            self._active_historical_watchlist_tickers = current
            self._active_historical_watchlists = current_by_watchlist
            self._active_historical_watchlist_evidence = current_evidence
            for ticker in removed:
                source_cache = self._strategy_source_values.get(ticker, {})
                for source_id in previous_evidence.get(ticker, {}):
                    source_cache.pop(source_id, None)
                    if "@@" in source_id:
                        base, dimension = source_id.split("@@", 1)
                        interval, separator, aggregation = dimension.partition("##")
                        alias = f"{base}@{interval}"
                        if separator and aggregation:
                            alias += f"#{aggregation}"
                        source_cache.pop(alias, None)
            for ticker, evidence in current_evidence.items():
                source_cache = self._strategy_source_values.setdefault(ticker, {})
                for source_id, value in evidence.items():
                    record = {
                        "observed_at": effective_at.isoformat(),
                        "value": deepcopy(value),
                    }
                    source_cache[source_id] = record
                    if "@@" in source_id:
                        base, dimension = source_id.split("@@", 1)
                        interval, separator, aggregation = dimension.partition("##")
                        alias = f"{base}@{interval}"
                        if separator and aggregation:
                            alias += f"#{aggregation}"
                        source_cache[alias] = record
            self._historical_watchlist_timeline_index += 1
            self._refresh_source_native_signal_activation(effective_at)
            if self._journal is not None:
                for ticker in added:
                    transition = transition_by_ticker.get(ticker, {})
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
                            "evidence": deepcopy(transition.get("evidence") or {}),
                            "reason": str(transition.get("reason") or "rules passed"),
                            "source": "causal_historical_watchlist",
                            "watchlist_id": str(transition.get("watchlist_id") or ""),
                        },
                    )

                for ticker in removed:
                    transition = transition_by_ticker.get(ticker, {})
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
                            "evidence": deepcopy(transition.get("evidence") or {}),
                            "reason": str(
                                transition.get("reason") or "rules no longer passed"
                            ),
                            "source": "causal_historical_watchlist",
                            "watchlist_id": str(transition.get("watchlist_id") or ""),
                        },
                    )

    def _historical_signal_eligible_tickers(
        self, run_plan: dict[str, Any]
    ) -> set[str] | None:
        selected = [
            str(value) for value in run_plan.get("watchlist_ids") or [] if str(value)
        ]
        policy = str(
            dict(run_plan.get("activation") or {}).get("watchlist_policy")
            or "any_selected"
        )
        if not selected or policy == "not_required":
            return None
        memberships = [
            set(self._active_historical_watchlists.get(watchlist_id, set()))
            for watchlist_id in selected
        ]
        if policy == "all_selected":
            return set.intersection(*memberships) if memberships else set()
        return set().union(*memberships) if memberships else set()

    def _refresh_source_native_signal_activation(
        self,
        event_time: datetime,
        *,
        force: bool = False,
    ) -> None:
        if not self._source_native_signal_episodes:
            self._next_source_native_signal_refresh_at = None
            return
        configuration = self.definition.configuration_revision["payload"]
        run_plan = dict(configuration.get("run_plan") or {})
        activation = dict(run_plan.get("activation") or {})
        if (
            not force
            and str(activation.get("watchlist_policy") or "any_selected")
            == "not_required"
            and self._next_source_native_signal_refresh_at is not None
            and event_time <= self._next_source_native_signal_refresh_at
        ):
            return
        eligible = self._historical_signal_eligible_tickers(run_plan)
        next_refresh_at: datetime | None = None
        for ticker, event in list(self._source_native_signal_episodes.items()):
            expires_at = _optional_checkpoint_time(
                event.occurrence.get("squeeze_expires_at")
            )
            if expires_at is None:
                expires_at = event.available_at + timedelta(minutes=5)
            if event_time > expires_at:
                self._source_native_signal_episodes.pop(ticker, None)
                self._signal_activated_tickers.discard(ticker)
                continue
            if event_time < event.available_at:
                next_refresh_at = (
                    event.available_at
                    if next_refresh_at is None
                    else min(next_refresh_at, event.available_at)
                )
                continue
            if run_plan_accepts_signal(
                run_plan,
                event.occurrence,
                eligible_tickers=eligible,
            ):
                self._signal_activated_tickers.add(ticker)
                self._strategy_source_values.setdefault(ticker, {}).update(
                    deepcopy(event.source_values)
                )
            else:
                self._signal_activated_tickers.discard(ticker)
            next_refresh_at = (
                expires_at
                if next_refresh_at is None
                else min(next_refresh_at, expires_at)
            )
        self._next_source_native_signal_refresh_at = next_refresh_at

    def _record_historical_watchlist_authority(self) -> None:
        fixture = self.definition.debug_fixture
        if (
            self.definition.mode == RunMode.BACKTEST_DEBUG
            and fixture is not None
            and fixture.watchlist_events
        ):
            self._record_data_authority(
                "watchlist_membership",
                {
                    "authority": "backtest_debug_fixture",
                    "fixture_id": fixture.fixture_id,
                    "revision_token": fixture.content_hash,
                    "row_count": len(fixture.watchlist_events),
                },
            )
            return
        for plan in self._historical_watchlist_plans:
            self._record_data_authority(
                f"watchlist_membership_plan:{plan['watchlist_id']}",
                {
                    "authority": "compiled_historical_watchlist_plan",
                    "cadence_ms": plan["cadence_ms"],
                    "external_features": plan["external_features"],
                    "plan_hash": plan["plan_hash"],
                    "qmd_sources": plan["qmd_sources"],
                    "schema_version": plan["schema_version"],
                    "watchlist_id": plan["watchlist_id"],
                },
            )
        timeline = self._historical_watchlist_timeline()
        if timeline:
            self._record_data_authority(
                "watchlist_membership_timeline",
                {
                    "authority": "historical_watchlist_resolution",
                    "first_effective_at": timeline[0]["effective_at"]
                    .astimezone(UTC)
                    .isoformat(),
                    "last_effective_at": timeline[-1]["effective_at"]
                    .astimezone(UTC)
                    .isoformat(),
                    "snapshot_count": len(timeline),
                    "watchlist_plan_hashes": sorted(
                        str(plan["plan_hash"])
                        for plan in self._historical_watchlist_plans
                    ),
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
        tickers = tuple(
            dict.fromkeys(
                [
                    *assignment_tickers,
                    *universe_tickers,
                    *(event.ticker for event in self._historical_external_signal_events),
                    *(_ticker(value) for value in self.definition.tickers),
                ]
            )
        )
        source_native_signal_tickers = {
            event.ticker for event in self._historical_external_signal_events
        }
        if self._strategy is not None and source_native_signal_tickers:
            # This is a computation-only projection. A source-native signal is
            # a mandatory activation prerequisite, so an assignment with no
            # occurrence cannot evaluate or trade. Pruning it cannot alter the
            # strategy path and avoids replaying irrelevant raw market events.
            assigned = set(assignment_tickers)
            tickers = tuple(
                ticker
                for ticker in tickers
                if ticker in assigned and ticker in source_native_signal_tickers
            )
        if not tickers:
            raise ValueError(
                "Historical run requires at least one explicit symbol, strategy assignment, or configured universe member"
            )
        return tickers

    async def _load_strategy_frames(self) -> list[ReplayDerivedFrame] | ReplayFrameSpool:
        if self.definition.mode == RunMode.BACKTEST_DEBUG:
            self._strategy_frame_cache_status = "fixture"
            fixture = self.definition.debug_fixture
            if fixture is None:
                raise RuntimeError("Backtest Debug fixture disappeared before execution")
            return _debug_derived_frames(fixture.derived_frames)
        if self._strategy is None or self._strategy_registration is None:
            self._strategy_frame_cache_status = "not_required"
            return []
        source_native_signal_tickers = {
            event.ticker for event in self._historical_external_signal_events
        }
        activation = dict(
            self.definition.configuration_revision["payload"].get("signal_activation") or {}
        )
        enabled_streams = [
            dict(stream)
            for stream in activation.get("signal_streams") or []
            if bool(stream.get("enabled", True))
        ]
        source_native_only = bool(enabled_streams) and all(
            str(stream.get("occurrence_source") or "") == "qmd_squeeze_episode"
            for stream in enabled_streams
        )
        requests = {
            (assignment.ticker, timeframe)
            for assignment in self._strategy.assignments()
            if not source_native_signal_tickers
            or assignment.ticker in source_native_signal_tickers
            if not source_native_only
            or not self._strategy_quality_prune_ready
            or assignment.ticker in self._strategy_quality_candidate_tickers
            for timeframe in self._strategy_registration.timeframe_resolver(
                assignment.parameters
            )
        }
        if not requests:
            self._strategy_frame_cache_status = "not_required"
            return []
        evaluation_end = _strategy_evaluation_end(
            self.definition.configuration_revision["payload"],
            session_start=self.definition.session_start,
            session_end=self.definition.session_end,
        )
        ordered_requests = sorted(requests)
        self._preparation_completed_units = 0
        self._preparation_total_units = len(ordered_requests)
        spool_path = self.run_dir / "strategy-frames.sqlite3"
        if self._resume_state is not None and spool_path.exists():
            spool = ReplayFrameSpool(spool_path, reset=False)
            if requests.issubset(await asyncio.to_thread(spool.completed_streams)):
                tickers = tuple(sorted({ticker for ticker, _ in requests}))
                events_by_ticker = (
                    {}
                    if source_native_only
                    else await _historical_signal_events(
                        tickers=tickers,
                        start=self.definition.session_start,
                        end=evaluation_end,
                        authority_sink=self._record_data_authority,
                    )
                )
                await asyncio.to_thread(spool.finalize, events_by_ticker)
                self._preparation_completed_units = len(ordered_requests)
                self._strategy_frame_cache_status = "run_checkpoint"
                return spool
        indicator_columns = (
            tuple(sorted(_STRATEGY_INDICATOR_FIELDS - _STRATEGY_LAZY_STRUCTURE_FIELDS))
            if source_native_only
            else None
        )
        durable_cache = self.definition.historical_frame_cache is None
        cache_source_revision: dict[str, Any] | None = None
        if durable_cache:
            cache_source_revision = await asyncio.to_thread(
                qmd_historical_source_revision,
                start=(
                    self.definition.session_start
                    - timedelta(days=INDICATOR_EMA_WARMUP_DAYS)
                ).isoformat(),
                end=evaluation_end.isoformat(),
                tickers=tuple(sorted({ticker for ticker, _ in requests})),
            )
            self._record_data_authority(
                "prepared_strategy_frame_source",
                {
                    "authority": "qmd_history_source_revision",
                    "indicator_warmup_days": INDICATOR_EMA_WARMUP_DAYS,
                    "revision_token": str(cache_source_revision["token"]),
                    "source_plan_hash": str(
                        cache_source_revision["source_plan_hash"]
                    ),
                    "calculation_revision": str(
                        cache_source_revision.get("calculation_revision") or ""
                    ),
                    "corporate_action_revision": str(
                        cache_source_revision.get("corporate_action_revision") or ""
                    ),
                    "complete_for_history": bool(
                        cache_source_revision.get("complete_for_history")
                    ),
                    "request_complete": bool(
                        cache_source_revision.get("request_complete")
                    ),
                    "source_tiers": list(
                        cache_source_revision.get("source_tiers") or ()
                    ),
                },
            )
        cache_path = (
            _prepared_frame_cache_path(
                self.runtime_root,
                start=self.definition.session_start,
                end=evaluation_end,
                requests=ordered_requests,
                indicator_columns=indicator_columns,
                source_revision=cache_source_revision,
            )
            if durable_cache
            else spool_path
        )
        cache_lock = _PREPARED_FRAME_CACHE_LOCKS.setdefault(
            str(cache_path), asyncio.Lock()
        )
        async with cache_lock:
            if durable_cache and cache_path.is_file():
                cached = ReplayFrameSpool(cache_path, reset=False)
                completed = await asyncio.to_thread(cached.completed_streams)
                if requests.issubset(completed):
                    authorities = await asyncio.to_thread(cached.stream_authorities)
                    for (ticker, timeframe), authority in authorities.items():
                        if (ticker, timeframe) in requests and authority:
                            self._record_data_authority(
                                f"derived:{ticker}:{timeframe}", authority
                            )
                    events_by_ticker = await self._strategy_frame_signal_events(
                        requests=requests,
                        source_native_only=source_native_only,
                    )
                    await asyncio.to_thread(cached.finalize, events_by_ticker)
                    self._preparation_completed_units = len(ordered_requests)
                    self._strategy_frame_cache_status = "hit"
                    return cached

            build_path = (
                cache_path.with_name(
                    f".{cache_path.name}.building"
                )
                if durable_cache
                else spool_path
            )
            partial_exists = durable_cache and build_path.is_file()
            spool = ReplayFrameSpool(build_path, reset=not partial_exists)
            completed_streams = (
                await asyncio.to_thread(spool.completed_streams)
                if partial_exists
                else set()
            )
            completed_requests = requests.intersection(completed_streams)
            self._preparation_completed_units = len(completed_requests)
            self._strategy_frame_cache_status = (
                "partial_hit"
                if completed_requests
                else "miss"
                if durable_cache
                else "request_memory"
            )
            missing_requests = requests - completed_requests
            timeframes_by_ticker: dict[str, list[str]] = {}
            for ticker, timeframe in sorted(missing_requests):
                timeframes_by_ticker.setdefault(ticker, []).append(timeframe)
            request_queue: asyncio.Queue[tuple[str, tuple[str, ...]]] = asyncio.Queue()
            for ticker, timeframes in timeframes_by_ticker.items():
                request_queue.put_nowait((ticker, tuple(timeframes)))
            writer_lock = asyncio.Lock()

            async def load_worker() -> None:
                while True:
                    try:
                        ticker, timeframes = request_queue.get_nowait()
                    except asyncio.QueueEmpty:
                        return
                    try:
                        for timeframe in timeframes:
                            authority: dict[str, Any] = {}

                            def record_authority(
                                key: str, evidence: dict[str, Any]
                            ) -> None:
                                self._record_data_authority(key, evidence)
                                if key == f"derived:{ticker}:{timeframe}":
                                    authority.update(deepcopy(evidence))

                            if self.definition.historical_frame_cache is None:
                                # A prior process may have stopped after
                                # appending a partial stream but before its
                                # completion marker. Resume only certified
                                # streams and restart this one cleanly.
                                async with writer_lock:
                                    await asyncio.to_thread(
                                        spool.delete_stream,
                                        ticker,
                                        timeframe,
                                    )

                                async def persist(batch: list[ReplayDerivedFrame]) -> None:
                                    async with writer_lock:
                                        await asyncio.to_thread(spool.append, batch)

                                for attempt in range(8):
                                    try:
                                        if (
                                            source_native_only
                                            and self._strategy_quality_prune_ready
                                        ):
                                            await _stream_historical_bar_derived_frames(
                                                ticker=ticker,
                                                timeframe=timeframe,
                                                start=self.definition.session_start,
                                                end=evaluation_end,
                                                frame_sink=persist,
                                                authority_sink=record_authority,
                                                indicator_columns=indicator_columns or (),
                                            )
                                        else:
                                            await _stream_historical_derived_frames(
                                                ticker=ticker,
                                                timeframe=timeframe,
                                                start=self.definition.session_start,
                                                end=evaluation_end,
                                                frame_sink=persist,
                                                authority_sink=record_authority,
                                                indicator_columns=indicator_columns,
                                            )
                                        break
                                    except Exception as exc:
                                        if not _retryable_historical_stream_error(exc):
                                            raise
                                        async with writer_lock:
                                            await asyncio.to_thread(
                                                spool.delete_stream,
                                                ticker,
                                                timeframe,
                                            )
                                        if attempt == 7:
                                            raise
                                        await asyncio.sleep(min(10.0, 0.5 * (2**attempt)))
                            else:
                                frames = await _historical_derived_frames(
                                    ticker=ticker,
                                    timeframe=timeframe,
                                    start=self.definition.session_start,
                                    end=evaluation_end,
                                    authority_sink=record_authority,
                                    frame_cache=self.definition.historical_frame_cache,
                                )
                                async with writer_lock:
                                    await asyncio.to_thread(spool.append, frames)
                            async with writer_lock:
                                await asyncio.to_thread(
                                    spool.mark_stream_complete,
                                    ticker,
                                    timeframe,
                                    authority,
                                )
                            self._preparation_completed_units += 1
                            self.updated_at = datetime.now(UTC)
                            await self._publish(force=True)
                    finally:
                        request_queue.task_done()

            try:
                worker_count = min(
                    replay_history_fetch_concurrency(), len(timeframes_by_ticker)
                )
                if worker_count:
                    await asyncio.gather(
                        *(load_worker() for _ in range(worker_count))
                    )
                events_by_ticker = await self._strategy_frame_signal_events(
                    requests=requests,
                    source_native_only=source_native_only,
                )
                await asyncio.to_thread(spool.finalize, events_by_ticker)
                if durable_cache:
                    await asyncio.to_thread(
                        _replace_path_with_retry, build_path, cache_path
                    )
                    spool = ReplayFrameSpool(cache_path, reset=False)
                    await asyncio.to_thread(spool.finalize, events_by_ticker)
                    self._strategy_frame_cache_status = "built"
                return spool
            except Exception:
                # Completed streams remain restart-safe in the deterministic
                # partial cache. Incomplete streams have no completion marker
                # and are deleted before their next attempt.
                raise

    async def _load_historical_signal_events(self) -> list[ReplaySignalEvent]:
        """Load independent immutable signal authorities concurrently, then merge deterministically."""

        batches = await asyncio.gather(
            self._load_market_signal_events(),
            self._load_source_native_signal_events(),
            self._load_external_signal_events(),
        )
        events = _filter_historical_signal_events(
            [event for batch in batches for event in batch],
            self.definition.tickers,
        )
        events.sort(
            key=lambda row: (
                row.available_at,
                row.ticker,
                str(row.occurrence.get("signal_stream_id") or ""),
                str(row.occurrence.get("event_id") or ""),
            )
        )
        return events

    async def _strategy_frame_signal_events(
        self,
        *,
        requests: set[tuple[str, str]],
        source_native_only: bool,
    ) -> dict[str, list[dict[str, Any]]]:
        if source_native_only:
            return {}
        return await _historical_signal_events(
            tickers=tuple(sorted({ticker for ticker, _ in requests})),
            start=self.definition.session_start,
            end=_strategy_evaluation_end(
                self.definition.configuration_revision["payload"],
                session_start=self.definition.session_start,
                session_end=self.definition.session_end,
            ),
            authority_sink=self._record_data_authority,
        )

    async def _load_external_signal_events(self) -> list[ReplaySignalEvent]:
        if self._journal is None:
            raise RuntimeError("Historical signal journal is unavailable")
        configuration = self.definition.configuration_revision["payload"]
        activation = dict(configuration.get("signal_activation") or {})
        configured_streams = [
            dict(row)
            for row in activation.get("signal_streams") or []
            if bool(row.get("enabled", True))
        ]
        fixture_stream_ids = {
            str(row.get("signal_stream_id") or "")
            for row in (
                self.definition.debug_fixture.signal_events
                if self.definition.debug_fixture is not None
                else ()
            )
            if str(row.get("signal_stream_id") or "")
        }
        streams = [
            row
            for row in configured_streams
            if (
                str(row.get("signal_stream_id") or "") in fixture_stream_ids
                if self.definition.mode == RunMode.BACKTEST_DEBUG
                else str(row.get("source_type") or "") == "news_events"
            )
        ]
        if not streams:
            return []
        if self.definition.mode == RunMode.BACKTEST_DEBUG:
            fixture = self.definition.debug_fixture
            if fixture is None:
                raise RuntimeError("Backtest Debug fixture disappeared before execution")
            source_rows = [dict(row) for row in fixture.signal_events]
            self._record_data_authority(
                "external_signal_events",
                {
                    "authority": "backtest_debug_fixture",
                    "fixture_id": fixture.fixture_id,
                    "revision_token": fixture.content_hash,
                    "row_count": len(source_rows),
                },
            )
        else:
            raw_news_rows = await asyncio.to_thread(
                all_news_synthesis_events,
                start_at=self.definition.session_start,
                as_of=self.definition.session_end,
            )
            source_rows = []
            for source in raw_news_rows:
                available_at = _optional_checkpoint_time(source.get("updated_at_utc"))
                if available_at is None:
                    continue
                try:
                    document = json.loads(str(source.get("synthesis_json") or "{}"))
                except json.JSONDecodeError:
                    continue
                source_rows.extend(
                    bullish_news_signal_rows(
                        configuration,
                        dict(document) if isinstance(document, dict) else {},
                        source=source,
                        market_rows=None,
                        available_at=available_at,
                        require_market_row=False,
                    )
                )
            identity_hash_rows: list[dict[str, Any]] = []
            if source_rows:
                from src.backend.historical_scanner_service import (
                    historical_scanner_reference_projection,
                )

                rows_by_clock: dict[datetime, list[dict[str, Any]]] = {}
                for row in source_rows:
                    event_clock = _optional_checkpoint_time(row.get("available_at"))
                    if event_clock is not None:
                        rows_by_clock.setdefault(event_clock, []).append(row)
                identity_permits = asyncio.Semaphore(replay_history_fetch_concurrency())

                async def identity_at(clock: datetime) -> tuple[datetime, dict[str, dict[str, Any]]]:
                    async with identity_permits:
                        projection = await asyncio.to_thread(
                            historical_scanner_reference_projection,
                            clock,
                        )
                    return clock, projection

                identity_results = await asyncio.gather(
                    *(identity_at(clock) for clock in sorted(rows_by_clock))
                )
                for clock, projection in identity_results:
                    for row in rows_by_clock[clock]:
                        ticker = str(row.get("ticker") or "").upper()
                        identity = dict(projection.get(ticker) or {})
                        row.update(identity)
                        identity_hash_rows.append({
                            "available_at": clock.isoformat(),
                            "ticker": ticker,
                            "symbol_id": identity.get("symbol_id"),
                            "ibkr_conid": identity.get("ibkr_conid"),
                        })
            source_hash = hashlib.sha256(
                json.dumps(raw_news_rows, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")
            ).hexdigest()
            identity_hash = hashlib.sha256(
                json.dumps(identity_hash_rows, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")
            ).hexdigest()
            self._record_data_authority(
                "external_signal_events",
                {
                    "authority": "q_live.news_synthesis_v1",
                    "engine_version": str(raw_news_rows[0].get("engine_version") or "") if raw_news_rows else "",
                    "revision_token": source_hash,
                    "identity_revision_token": identity_hash,
                    "row_count": len(raw_news_rows),
                    "available_start": self.definition.session_start.astimezone(UTC).isoformat(),
                    "available_end": self.definition.session_end.astimezone(UTC).isoformat(),
                },
            )
        compiled: list[ReplaySignalEvent] = []
        discovery = {
            "market_discovery": {
                "signal_streams": streams,
                "rule_sets": activation.get("rule_sets") or [],
                "column_catalog": activation.get("column_catalog") or [],
            }
        }
        for stream in streams:
            stream_id = str(stream.get("signal_stream_id") or "")
            rows = [
                row
                for row in source_rows
                if str(row.get("signal_stream_id") or stream_id) == stream_id
            ]
            occurrences = await asyncio.to_thread(
                SIGNAL_STREAM_RUNTIME.append_external_event_rows,
                discovery,
                signal_stream_id=stream_id,
                rows=rows,
                journal=self._journal,
                event_run_id=self.run_id,
                include_existing=True,
            )
            for occurrence in occurrences:
                available_at = _optional_checkpoint_time(
                    occurrence.get("effective_at") or occurrence.get("event_time")
                )
                ticker = str(occurrence.get("ticker") or "").upper()
                if available_at is None or not ticker:
                    continue
                compiled.append(
                    ReplaySignalEvent(
                        available_at=available_at,
                        occurrence=occurrence,
                        source_values=_occurrence_source_values(occurrence),
                        ticker=ticker,
                    )
                )
        return sorted(
            compiled,
            key=lambda row: (
                row.available_at,
                row.ticker,
                str(row.occurrence.get("signal_stream_id") or ""),
                str(row.occurrence.get("event_id") or ""),
            ),
        )

    async def _load_source_native_signal_events(self) -> list[ReplaySignalEvent]:
        if self.definition.mode == RunMode.BACKTEST_DEBUG:
            return []
        if self._journal is None:
            raise RuntimeError("Historical signal journal is unavailable")
        activation = dict(
            self.definition.configuration_revision["payload"].get("signal_activation")
            or {}
        )
        from src.backend.historical_signal_occurrence_service import (
            SUPPORTED_NATIVE_OCCURRENCE_SOURCES,
            historical_source_native_signal_occurrences,
        )

        native_streams = [
            dict(row)
            for row in activation.get("signal_streams") or []
            if bool(row.get("enabled", True))
            and str(row.get("occurrence_source") or "").strip()
        ]
        unsupported = sorted({
            str(row.get("occurrence_source") or "").strip()
            for row in native_streams
            if str(row.get("occurrence_source") or "").strip()
            not in SUPPORTED_NATIVE_OCCURRENCE_SOURCES
        })
        if unsupported:
            raise RuntimeError(
                "Historical source-native Signal Streams lack an immutable occurrence loader: "
                + ", ".join(unsupported)
            )
        streams = native_streams
        if not streams:
            return []

        permits = asyncio.Semaphore(replay_history_fetch_concurrency())

        async def load_stream(stream: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
            async with permits:
                loaded = await asyncio.to_thread(
                    historical_source_native_signal_occurrences,
                    stream,
                    start=self.definition.requested_start,
                    end=self.definition.session_end,
                )
            return stream, loaded

        loaded_streams = await asyncio.gather(*(load_stream(stream) for stream in streams))
        loaded_streams = _filter_loaded_source_native_occurrences(
            loaded_streams,
            self.definition.tickers,
        )
        return await asyncio.to_thread(
            self._persist_source_native_signal_events,
            loaded_streams,
        )

    def _persist_source_native_signal_events(
        self,
        loaded_streams: list[tuple[dict[str, Any], dict[str, Any]]],
    ) -> list[ReplaySignalEvent]:
        if self._journal is None:
            raise RuntimeError("Historical signal journal is unavailable")
        results: list[ReplaySignalEvent] = []
        for stream, loaded in loaded_streams:
            stream_id = str(stream.get("signal_stream_id") or "")
            self._record_data_authority(
                f"source_native_signal_stream:{stream_id}",
                dict(loaded.get("authority") or {}),
            )
            prepared: list[tuple[dict[str, Any], datetime, str]] = []
            for source in loaded.get("occurrences") or []:
                occurrence = dict(source)
                available_at = _optional_checkpoint_time(
                    occurrence.get("available_at")
                    or occurrence.get("effective_at")
                    or occurrence.get("event_time")
                )
                ticker = str(occurrence.get("ticker") or "").upper()
                event_id = str(occurrence.get("event_id") or "")
                if available_at is None or not ticker or not event_id:
                    raise RuntimeError(
                        f"Historical source-native Signal Stream {stream_id} returned an invalid occurrence"
                    )
                prepared.append((occurrence, available_at, ticker))
            persisted_records = self._journal.append_once_many(
                {
                    "run_id": self.run_id,
                    "category": "market_discovery_signal",
                    "entity_type": "signal_occurrence",
                    "entity_id": str(occurrence["event_id"]),
                    "event_time": available_at,
                    "payload": occurrence,
                }
                for occurrence, available_at, _ticker in prepared
            )
            for (record, _inserted), (_occurrence, available_at, ticker) in zip(
                persisted_records, prepared, strict=True
            ):
                persisted_occurrence = dict(record.payload)
                results.append(
                    ReplaySignalEvent(
                        available_at=available_at,
                        occurrence=persisted_occurrence,
                        source_values=_occurrence_source_values(persisted_occurrence),
                        ticker=ticker,
                    )
                )
        return sorted(
            results,
            key=lambda row: (
                row.available_at,
                row.ticker,
                str(row.occurrence.get("signal_stream_id") or ""),
                str(row.occurrence.get("event_id") or ""),
            ),
        )

    async def _load_market_signal_events(self) -> list[ReplaySignalEvent]:
        if not self._historical_core_signal_plans:
            return []
        if self._journal is None:
            raise RuntimeError("Historical signal journal is unavailable")
        from src.backend.historical_watchlist_feature_service import (
            materialize_historical_watchlist_plans,
        )

        batch = await asyncio.to_thread(
            materialize_historical_watchlist_plans,
            self._historical_core_signal_plans,
        )
        return await asyncio.to_thread(self._compile_market_signal_events, batch)

    def _compile_market_signal_events(
        self, batch: dict[str, Any]
    ) -> list[ReplaySignalEvent]:
        if self._journal is None:
            raise RuntimeError("Historical signal journal is unavailable")
        by_virtual_watchlist = {
            str(row.get("watchlist_id") or ""): dict(row)
            for row in batch.get("materializations") or []
        }
        configuration = self.definition.configuration_revision["payload"]
        activation = dict(configuration.get("signal_activation") or {})
        stream_by_id = {
            str(row.get("signal_stream_id") or ""): dict(row)
            for row in activation.get("signal_streams") or []
        }
        discovery = {
            "market_discovery": {
                "signal_streams": list(stream_by_id.values()),
                "rule_sets": activation.get("rule_sets") or [],
                "column_catalog": activation.get("column_catalog") or [],
            }
        }
        result: list[ReplaySignalEvent] = []
        for plan in self._historical_core_signal_plans:
            stream_id = str(plan.get("signal_stream_id") or "")
            virtual_id = str(plan.get("watchlist_id") or "")
            materialized = by_virtual_watchlist.get(virtual_id)
            if materialized is None:
                raise RuntimeError(
                    f"QMD History omitted Signal Stream materialization {stream_id}"
                )
            self._record_data_authority(
                f"signal_stream_materialization:{stream_id}",
                {
                    "authority": "qmd_history_signal_stream_timeline",
                    "application_materialization_id": str(
                        materialized.get("application_materialization_id") or ""
                    ),
                    "qmd_materialization_id": str(
                        materialized.get("materialization_id") or ""
                    ),
                    "plan_hash": str(plan.get("plan_hash") or ""),
                    "source_revision": dict(materialized.get("source_revision") or {}),
                    "dependency_source_revision": dict(
                        batch.get("dependency_source_revision") or {}
                    ),
                    "identity_revision": dict(
                        materialized.get("identity_revision") or {}
                    ),
                },
            )
            rows = []
            for chunk in materialized.get("chunks") or []:
                for transition in dict(chunk).get("transitions") or []:
                    if str(transition.get("event") or "") != "added":
                        continue
                    effective_at = str(transition.get("effective_at") or "")
                    ticker = str(transition.get("ticker") or "").upper()
                    rows.append({
                        "ticker": ticker,
                        "symbol": ticker,
                        "available_at": effective_at,
                        "source_event_id": (
                            f"{materialized.get('application_materialization_id')}|"
                            f"{effective_at}|{ticker}|added"
                        ),
                        **dict(transition.get("evidence") or {}),
                        **dict(transition.get("identity") or {}),
                    })
            occurrences = SIGNAL_STREAM_RUNTIME.append_external_event_rows(
                discovery,
                signal_stream_id=stream_id,
                rows=rows,
                journal=self._journal,
                event_run_id=self.run_id,
                include_existing=True,
            )
            for occurrence in occurrences:
                available_at = _optional_checkpoint_time(
                    occurrence.get("effective_at") or occurrence.get("event_time")
                )
                ticker = str(occurrence.get("ticker") or "").upper()
                if available_at is None or not ticker:
                    continue
                result.append(
                    ReplaySignalEvent(
                        available_at=available_at,
                        occurrence=occurrence,
                        source_values=_occurrence_source_values(occurrence),
                        ticker=ticker,
                    )
                )
        return result

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
        # The journal is the durable authority ledger.  Do not rewrite the full
        # configuration/catalog manifest for every newly observed source key:
        # historical runs can register hundreds of keys before replay begins,
        # and modern configuration catalogs are intentionally large.  The
        # manifest is persisted at lifecycle and restart-checkpoint boundaries
        # (and again on completion), so omitting this redundant write preserves
        # restart semantics without quadratic startup I/O.

    def _write_manifest(self, *, include_details: bool = True) -> None:
        if not self.run_dir.exists():
            return
        run = self.snapshot(include_details=include_details)
        payload = {
            "schema_version": 2,
            "run": run,
            "definition": self.definition.payload(),
            "approved_configuration_path": "approved-configuration.json",
            "approved_configuration_revision_id": str(
                self.definition.configuration_revision.get("revision_id") or ""
            ),
            "approved_configuration_content_hash": str(
                self.definition.configuration_revision.get("content_hash") or ""
            ),
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
            _replace_path_with_retry(fixture_temporary, fixture_target)
            payload["debug_fixture_path"] = str(fixture_target)
        target = self.run_dir / "manifest.json"
        temporary = self.run_dir / "manifest.json.tmp"
        temporary.write_text(
            json.dumps(payload, separators=(",", ":"), sort_keys=True),
            encoding="utf-8",
        )
        _replace_path_with_retry(temporary, target)
        summary_target = self.run_dir / "run-summary.json"
        summary_temporary = self.run_dir / "run-summary.json.tmp"
        summary_temporary.write_text(
            json.dumps(run, separators=(",", ":"), sort_keys=True),
            encoding="utf-8",
        )
        _replace_path_with_retry(summary_temporary, summary_target)
        selection_target = self.run_dir / "run-selection.json"
        selection_temporary = self.run_dir / "run-selection.json.tmp"
        selection_temporary.write_text(
            json.dumps(
                _run_selection_projection(
                    _replay_run_list_projection(run, resident=False),
                    self.definition.configuration_revision,
                ),
                separators=(",", ":"),
                sort_keys=True,
            ),
            encoding="utf-8",
        )
        _replace_path_with_retry(selection_temporary, selection_target)

    def _write_approved_configuration(self) -> None:
        target = self.run_dir / "approved-configuration.json"
        expected_revision_id = str(
            self.definition.configuration_revision.get("revision_id") or ""
        )
        expected_content_hash = str(
            self.definition.configuration_revision.get("content_hash") or ""
        )
        if target.is_file():
            existing = json.loads(target.read_text(encoding="utf-8"))
            if (
                str(existing.get("revision_id") or "") != expected_revision_id
                or str(existing.get("content_hash") or "") != expected_content_hash
            ):
                raise ValueError("Replay approved configuration changed on disk")
            return
        temporary = self.run_dir / "approved-configuration.json.tmp"
        temporary.write_text(
            json.dumps(
                self.definition.configuration_revision,
                separators=(",", ":"),
                sort_keys=True,
            ),
            encoding="utf-8",
        )
        _replace_path_with_retry(temporary, target)


def _replace_path_with_retry(temporary: Path, target: Path) -> None:
    """Preserve atomic manifests when a Windows reader briefly holds the target."""

    for attempt in range(100):
        try:
            temporary.replace(target)
            return
        except PermissionError:
            if attempt == 99:
                raise
            time.sleep(0.05)


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
        await self._admit(controller)
        await controller.start()
        return controller

    async def resume(self, run_id: str) -> ReplayRunController:
        normalized = str(run_id or "").strip()
        if not re.fullmatch(r"[0-9a-fA-F-]{36}", normalized):
            raise KeyError(run_id)
        resident = self._runs.get(normalized)
        if resident is not None and resident.status not in TERMINAL_REPLAY_STATUSES:
            raise ValueError("Historical run is already resident and active")
        run_dir = (self.runtime_root / normalized).resolve()
        if self.runtime_root != run_dir and self.runtime_root not in run_dir.parents:
            raise ValueError("Historical run directory escaped the runtime root")
        manifest_path = run_dir / "manifest.json"
        journal_path = run_dir / "journal.sqlite3"
        if not manifest_path.is_file() or not journal_path.is_file():
            raise KeyError(run_id)
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        prior_status = str(dict(manifest.get("run") or {}).get("status") or "")
        if prior_status == "completed":
            raise ValueError("Completed historical runs cannot be resumed")
        journal = TradingJournal(journal_path)
        try:
            persisted = journal.load_checkpoint(normalized)
        finally:
            journal.close()
        state = dict((persisted or {}).get("state") or {})
        if (
            int(state.get("schema_version") or 0) != RESTART_CHECKPOINT_SCHEMA_VERSION
            or not bool(state.get("complete"))
        ):
            raise ValueError("Historical run has no complete restart-safe checkpoint")
        definition = _definition_from_manifest(manifest, run_dir=run_dir)
        identity = dict(state.get("identity") or {})
        expected_fixture_hash = (
            definition.debug_fixture.content_hash
            if definition.debug_fixture is not None
            else ""
        )
        if (
            str(identity.get("run_id") or "") != normalized
            or str(identity.get("mode") or "") != definition.mode.value
            or str(identity.get("configuration_revision_id") or "")
            != str(definition.configuration_revision.get("revision_id") or "")
            or str(identity.get("configuration_content_hash") or "")
            != str(definition.configuration_revision.get("content_hash") or "")
            or str(identity.get("debug_fixture_content_hash") or "")
            != expected_fixture_hash
            or list(identity.get("account_ids") or ())
            != list(_simulated_account_ids(definition))
        ):
            raise ValueError("Historical restart checkpoint identity changed")
        if resident is not None and resident._journal is not None:
            resident._journal.close()
            resident._journal = None
        controller = ReplayRunController(
            definition,
            run_id=normalized,
            runtime_root=self.runtime_root,
            resume_state=state,
        )
        await self._admit(controller)
        await controller.start()
        return controller

    async def review_completed(self, run_id: str) -> ReplayRunController:
        """Open a durable completed Backtest as an immutable Canvas review."""

        normalized = str(run_id or "").strip()
        if not re.fullmatch(r"[0-9a-fA-F-]{36}", normalized):
            raise KeyError(run_id)
        resident = self._runs.get(normalized)
        if resident is not None:
            if resident.status != "completed":
                raise ValueError("Only completed Backtests can be opened for review")
            return resident
        run_dir = (self.runtime_root / normalized).resolve()
        if self.runtime_root != run_dir and self.runtime_root not in run_dir.parents:
            raise ValueError("Historical run directory escaped the runtime root")
        manifest_path = run_dir / "manifest.json"
        journal_path = run_dir / "journal.sqlite3"
        if not manifest_path.is_file() or not journal_path.is_file():
            raise KeyError(run_id)
        persisted_run, definition, state = await asyncio.to_thread(
            _load_completed_review_materials,
            normalized,
            run_dir,
            manifest_path,
            journal_path,
        )
        controller = ReplayRunController(
            definition,
            run_id=normalized,
            runtime_root=self.runtime_root,
            resume_state=state,
        )
        controller._journal = TradingJournal(journal_path, read_only=True)
        await asyncio.to_thread(
            _initialize_completed_review_controller,
            controller,
        )
        controller.status = "completed"
        controller.error = str(persisted_run.get("error") or "")
        controller._runtime_inputs_ready = True
        controller._runtime_finished = True
        controller._preparation_stage = "ready"
        controller._strategy_frame_cache_status = str(
            dict(persisted_run.get("preparation_cache") or {}).get("strategy_frames")
            or "run_checkpoint"
        )
        controller.created_at = _checkpoint_time(
            persisted_run.get("created_at") or controller.created_at
        )
        controller.updated_at = _checkpoint_time(
            persisted_run.get("updated_at") or controller.updated_at
        )
        await self._admit(controller)
        return controller

    async def _admit(self, controller: ReplayRunController) -> None:
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

    def get(self, run_id: str) -> ReplayRunController:
        normalized = str(run_id or "").strip()
        if not re.fullmatch(r"[0-9a-fA-F-]{36}", normalized):
            raise KeyError(run_id)
        controller = self._runs.get(normalized)
        if controller is None:
            raise KeyError(run_id)
        return controller

    def list(self, *, include_durable: bool = False) -> list[dict[str, Any]]:
        rows = {
            controller.run_id: _run_selection_projection(
                _replay_run_list_projection(controller.stream_snapshot(), resident=True),
                controller.definition.configuration_revision,
            )
            for controller in self._runs.values()
        }
        if include_durable and self.runtime_root.is_dir():
            for run_dir in self.runtime_root.iterdir():
                if not run_dir.is_dir() or run_dir.name in rows:
                    continue
                try:
                    durable = _durable_run_selection(run_dir)
                    if durable is not None:
                        rows[run_dir.name] = durable
                except (OSError, TypeError, ValueError, json.JSONDecodeError):
                    continue
        return sorted(rows.values(), key=lambda row: str(row.get("created_at") or ""), reverse=True)


def _replay_run_list_projection(
    snapshot: dict[str, Any],
    *,
    resident: bool,
) -> dict[str, Any]:
    """Return the bounded lifecycle fields required by the recent-runs picker."""

    fields = (
        "schema_version",
        "run_id",
        "status",
        "runtime_ready",
        "preparation_stage",
        "preparation_progress",
        "preparation_cache",
        "error",
        "created_at",
        "updated_at",
        "current_time",
        "session_date",
        "requested_start",
        "session_start",
        "session_end",
        "progress",
        "checkpoint",
        "mode",
        "execution_mode",
        "configuration_revision",
        "configuration_revision_id",
        "processed_events",
    )
    return {
        **{field: deepcopy(snapshot.get(field)) for field in fields},
        "resident": resident,
    }


def _run_selection_projection(
    row: dict[str, Any], approved: dict[str, Any]
) -> dict[str, Any]:
    payload = dict(approved.get("payload") or {})
    strategy = dict(payload.get("strategy") or {})
    run_plan = dict(payload.get("run_plan") or {})
    return {
        **row,
        "configuration_label": str(approved.get("label") or ""),
        "configuration_content_hash": str(approved.get("content_hash") or ""),
        "strategy_id": str(strategy.get("strategy_id") or ""),
        "strategy_name": str(strategy.get("name") or strategy.get("strategy_id") or ""),
        "strategy_revision": int(strategy.get("revision") or 0),
        "run_plan_name": str(run_plan.get("name") or run_plan.get("run_plan_id") or ""),
    }


def _load_completed_review_materials(
    run_id: str,
    run_dir: Path,
    manifest_path: Path,
    journal_path: Path,
) -> tuple[dict[str, Any], ReplayRunDefinition, dict[str, Any]]:
    """Load large immutable review artifacts without blocking the API event loop."""

    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    persisted_run = dict(manifest.get("run") or {})
    if str(persisted_run.get("status") or "") != "completed":
        raise ValueError("Only completed Backtests can be opened for review")
    definition = _definition_from_manifest(manifest, run_dir=run_dir)
    if definition.mode != RunMode.BACKTEST:
        raise ValueError("Completed-run review accepts Backtest runs only")
    journal = TradingJournal(journal_path, read_only=True)
    try:
        persisted = journal.load_checkpoint(run_id)
    finally:
        journal.close()
    state = dict((persisted or {}).get("state") or {})
    if (
        int(state.get("schema_version") or 0) != RESTART_CHECKPOINT_SCHEMA_VERSION
        or not bool(state.get("complete"))
    ):
        raise ValueError("Completed Backtest has no complete review checkpoint")
    return persisted_run, definition, state


def _initialize_completed_review_controller(controller: ReplayRunController) -> None:
    """Rebuild a terminal runtime off the serving event loop."""

    asyncio.run(
        controller._initialize_runtime(
            record_configuration=False,
            record_lifecycle=False,
            review_only=True,
        )
    )


def _durable_run_selection(run_dir: Path) -> dict[str, Any] | None:
    """Read a compact picker row, including bounded legacy-run discovery."""

    selection_path = run_dir / "run-selection.json"
    if selection_path.is_file():
        return dict(json.loads(selection_path.read_text(encoding="utf-8")))
    manifest_path = run_dir / "manifest.json"
    if not manifest_path.is_file():
        return None
    # Legacy manifests embed the full configuration and runtime snapshot and can
    # be tens of megabytes. The definition is deliberately written first, while
    # terminal lifecycle fields are near the tail, so discovery reads bounded
    # edges instead of materializing every historical assignment on the API loop.
    size = manifest_path.stat().st_size
    with manifest_path.open("rb") as handle:
        head = handle.read(min(size, 131_072)).decode("utf-8", errors="ignore")
        if size > 2_097_152:
            handle.seek(size - 2_097_152)
        tail = handle.read().decode("utf-8", errors="ignore")

    def string(pattern: str, source: str = head) -> str:
        match = re.search(pattern, source)
        return json.loads(f'"{match.group(1)}"') if match else ""

    def integer(pattern: str, source: str = tail) -> int:
        match = re.search(pattern, source)
        return int(match.group(1)) if match else 0

    status_match = re.search(
        r'"speed":[^,]+,"status":"(completed|failed|stopped)","strategy_debug_sources"',
        tail,
    )
    if status_match is None:
        return None
    mode = string(r'"mode":"([^"\\]*(?:\\.[^"\\]*)*)"')
    session_date = string(r'"session_date":"([^"\\]*(?:\\.[^"\\]*)*)"')
    session_end = string(r'"session_end":"([^"\\]*(?:\\.[^"\\]*)*)"')
    label = string(r'"configuration_label":"([^"\\]*(?:\\.[^"\\]*)*)"')
    revision = integer(r'"configuration_revision":(\d+)', head)
    processed = integer(r'"processed_events":(\d+)', tail)
    modified = datetime.fromtimestamp(manifest_path.stat().st_mtime, tz=UTC).isoformat()
    selection = {
        "schema_version": 1,
        "run_id": run_dir.name,
        "status": status_match.group(1),
        "runtime_ready": False,
        "preparation_stage": "ready",
        "created_at": modified,
        "updated_at": modified,
        "current_time": session_end,
        "session_date": session_date,
        "session_end": session_end,
        "mode": mode,
        "configuration_revision": revision,
        "configuration_label": label,
        "strategy_name": label,
        "strategy_revision": 0,
        "processed_events": processed,
        "resident": False,
    }
    # Add a compact derived sidecar so every later process restart avoids even
    # the bounded legacy edge scan. The immutable journal and manifest remain
    # untouched and authoritative.
    temporary = run_dir / "run-selection.json.tmp"
    try:
        temporary.write_text(
            json.dumps(selection, separators=(",", ":"), sort_keys=True),
            encoding="utf-8",
        )
        _replace_path_with_retry(temporary, selection_path)
    except OSError:
        temporary.unlink(missing_ok=True)
    return selection


def replay_runtime_root() -> Path:
    configured = os.environ.get("TRADING_REPLAY_ROOT", "").strip()
    if configured:
        return Path(configured)
    trading_root = Path(
        os.environ.get("TRADING_RUNTIME_ROOT", str(DEFAULT_REPLAY_ROOT.parent))
    )
    return trading_root / "replay"


def _prepared_frame_cache_path(
    runtime_root: Path,
    *,
    start: datetime,
    end: datetime,
    requests: list[tuple[str, str]],
    indicator_columns: tuple[str, ...] | None,
    source_revision: dict[str, Any] | None,
) -> Path:
    """Identify one immutable, restart-persistent derived-frame preparation."""

    identity = {
        "schema_version": PREPARED_FRAME_CACHE_SCHEMA_VERSION,
        "start": start.isoformat(),
        "end": end.isoformat(),
        "requests": [list(request) for request in sorted(requests)],
        "indicator_columns": (
            list(indicator_columns) if indicator_columns is not None else None
        ),
        "source_revision": {
            "token": str(dict(source_revision or {}).get("token") or ""),
            "source_plan_hash": str(
                dict(source_revision or {}).get("source_plan_hash") or ""
            ),
            "calculation_revision": str(
                dict(source_revision or {}).get("calculation_revision") or ""
            ),
            "corporate_action_revision": str(
                dict(source_revision or {}).get("corporate_action_revision") or ""
            ),
        },
    }
    encoded = json.dumps(identity, separators=(",", ":"), sort_keys=True).encode(
        "utf-8"
    )
    cache_key = hashlib.sha256(encoded).hexdigest()
    cache_root = (runtime_root / "_prepared" / "strategy-frames").resolve()
    resolved_root = runtime_root.resolve()
    if resolved_root != cache_root and resolved_root not in cache_root.parents:
        raise ValueError("Replay prepared-frame cache escaped the runtime root")
    return cache_root / f"v{PREPARED_FRAME_CACHE_SCHEMA_VERSION}-{cache_key}.sqlite3"


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


def _definition_from_manifest(
    manifest: dict[str, Any], *, run_dir: Path
) -> ReplayRunDefinition:
    definition = dict(manifest.get("definition") or {})
    approved = manifest.get("approved_configuration")
    if not isinstance(approved, dict):
        configured_path = Path(
            str(manifest.get("approved_configuration_path") or "approved-configuration.json")
        )
        approved_path = configured_path if configured_path.is_absolute() else run_dir / configured_path
        approved_path = approved_path.resolve()
        if run_dir.resolve() not in approved_path.parents:
            raise ValueError("Historical approved configuration path escaped the run directory")
        if not approved_path.is_file():
            raise ValueError("Historical manifest omitted its approved configuration file")
        approved = json.loads(approved_path.read_text(encoding="utf-8"))
    if not isinstance(approved, dict) or not approved.get("revision_id"):
        raise ValueError("Historical manifest omitted its approved configuration")
    expected_revision_id = str(manifest.get("approved_configuration_revision_id") or "")
    expected_content_hash = str(manifest.get("approved_configuration_content_hash") or "")
    if (
        expected_revision_id
        and str(approved.get("revision_id") or "") != expected_revision_id
    ) or (
        expected_content_hash
        and str(approved.get("content_hash") or "") != expected_content_hash
    ):
        raise ValueError("Historical approved configuration identity changed")
    mode = RunMode(str(definition.get("mode") or ""))
    fixture = None
    if mode == RunMode.BACKTEST_DEBUG:
        fixture_path = run_dir / "debug-fixture.json"
        if not fixture_path.is_file():
            raise ValueError("Backtest Debug restart omitted its fixture")
        fixture_payload = json.loads(fixture_path.read_text(encoding="utf-8"))
        fixture = HistoricalDebugFixture(
            fixture_id=str(fixture_payload.get("fixture_id") or ""),
            market_events=tuple(
                dict(row) for row in fixture_payload.get("market_events") or ()
            ),
            derived_frames=tuple(
                dict(row) for row in fixture_payload.get("derived_frames") or ()
            ),
            signal_events=tuple(
                dict(row) for row in fixture_payload.get("signal_events") or ()
            ),
            watchlist_events=tuple(
                dict(row) for row in fixture_payload.get("watchlist_events") or ()
            ),
        )
        if fixture.content_hash != str(fixture_payload.get("content_hash") or ""):
            raise ValueError("Backtest Debug fixture content hash changed")
    session_date = date.fromisoformat(str(definition.get("session_date") or ""))
    session_end = _checkpoint_time(definition.get("session_end"))
    return ReplayRunDefinition(
        session_date=session_date,
        final_session_date=session_end.astimezone(NEW_YORK).date(),
        start_time=clock_time.fromisoformat(str(definition.get("start_time") or "")),
        end_time=session_end.astimezone(NEW_YORK).time().replace(tzinfo=None),
        initial_cash=float(definition.get("initial_cash") or 0),
        assignment_ids=tuple(str(value) for value in definition.get("assignment_ids") or ()),
        tickers=tuple(str(value) for value in definition.get("tickers") or ()),
        configuration_revision=deepcopy(approved),
        execution_mode=str(definition.get("execution_mode") or "strategy"),
        mode=mode,
        debug_fixture=fixture,
        simulation_profile=str(definition.get("simulation_profile") or "baseline"),
    )


def _simulated_account_ids(definition: ReplayRunDefinition) -> tuple[str, ...]:
    configuration = definition.configuration_revision["payload"]
    bindings = [
        dict(row)
        for row in configuration["accounts"]["bindings"]
        if bool(row.get("enabled", True))
        and definition.mode.value in list(row.get("modes") or [])
    ]
    return tuple(
        f"SIM-{index + 1:02d}-{_slug(str(binding['account_key']))}"
        for index, binding in enumerate(bindings)
    ) or ("SIM-REPLAY",)


def _checkpoint_time(value: Any) -> datetime:
    parsed = value if isinstance(value, datetime) else datetime.fromisoformat(str(value))
    if parsed.tzinfo is None:
        raise ValueError("Restart checkpoint timestamps must be timezone-aware")
    return parsed


def _optional_checkpoint_time(value: Any) -> datetime | None:
    return _checkpoint_time(value) if value else None


def _occurrence_source_values(occurrence: dict[str, Any]) -> dict[str, Any]:
    observed_at = str(
        occurrence.get("available_at")
        or occurrence.get("effective_at")
        or occurrence.get("event_time")
        or ""
    )
    result: dict[str, Any] = {}
    signal_stream_id = str(occurrence.get("signal_stream_id") or "").strip()
    if signal_stream_id:
        result[f"signal.activation.{signal_stream_id}"] = {
            "value": True,
            "observed_at": observed_at,
            "event_id": str(occurrence.get("event_id") or ""),
        }
    squeeze_move = occurrence.get("squeeze_move_pct")
    if squeeze_move is not None:
        record = {"value": squeeze_move, "observed_at": observed_at}
        for identity in (
            "signal.squeeze_move_pct",
            "data.signal.squeeze_move_pct@1:value",
        ):
            result[identity] = record
    for instance_ref, raw in dict(occurrence.get("field_evidence") or {}).items():
        evidence = dict(raw or {})
        value = evidence.get("value")
        if value is None:
            continue
        field_ref = str(evidence.get("field_ref") or "")
        source_id = field_ref.rsplit(":", 1)[-1] if ":" in field_ref else ""
        interval = str(evidence.get("interval") or "")
        aggregation = str(evidence.get("aggregation") or "")
        record = {
            "value": value,
            "observed_at": str(evidence.get("available_at") or observed_at),
        }
        for identity in (str(instance_ref), field_ref, source_id):
            if not identity:
                continue
            result[identity] = record
            if interval:
                result[f"{identity}@{interval}"] = record
                if aggregation:
                    result[f"{identity}@{interval}#{aggregation}"] = record
    for key, value in dict(occurrence.get("evidence") or {}).items():
        if value is not None and str(key) not in result:
            result[str(key)] = {"value": value, "observed_at": observed_at}
    return result


def _market_event_checkpoint(event: MarketEvent) -> dict[str, Any]:
    payload = asdict(event)
    payload["kind"] = event.kind
    for key in ("ingest_ts", "participant_ts", "trf_ts", "ts"):
        if isinstance(payload.get(key), datetime):
            payload[key] = payload[key].isoformat()
    return payload


def _event_after_cursor(event: MarketEvent, cursor: dict[str, Any]) -> bool:
    cursor_time = _checkpoint_time(cursor.get("ts"))
    cursor_key = (
        cursor_time.astimezone(UTC),
        int(cursor.get("sequence") or 0),
        str(cursor.get("kind") or ""),
    )
    event_key = (event.ts.astimezone(UTC), int(event.sequence), event.kind)
    return event_key > cursor_key


def _frame_after_cursor(frame: ReplayDerivedFrame, cursor: dict[str, Any]) -> bool:
    cursor_key = (
        _checkpoint_time(cursor.get("as_of")).astimezone(UTC),
        str(cursor.get("ticker") or ""),
        str(cursor.get("timeframe") or ""),
        int(cursor.get("sequence") or 0),
    )
    frame_key = (
        frame.as_of.astimezone(UTC),
        frame.ticker,
        frame.timeframe,
        int(frame.sequence),
    )
    return frame_key > cursor_key


def _quote_from_checkpoint(payload: dict[str, Any]) -> QuoteEvent:
    if payload.pop("kind", "quote") != "quote":
        raise ValueError("Restart checkpoint quote cache contains a non-quote event")
    payload["conditions"] = tuple(int(value) for value in payload.get("conditions") or ())
    payload["indicators"] = tuple(int(value) for value in payload.get("indicators") or ())
    payload["ingest_ts"] = _checkpoint_time(payload["ingest_ts"])
    payload["ts"] = _checkpoint_time(payload["ts"])
    return QuoteEvent(**payload)


def _strategy_assignment_from_checkpoint(payload: dict[str, Any]) -> StrategyAssignment:
    return StrategyAssignment(
        assignment_id=str(payload.get("assignment_id") or ""),
        strategy_id=str(payload.get("strategy_id") or ""),
        strategy_revision=int(payload.get("strategy_revision") or 0),
        account_id=str(payload.get("account_id") or ""),
        ticker=str(payload.get("ticker") or "").upper(),
        conid=int(payload.get("conid") or 0),
        status=AssignmentStatus(str(payload.get("status") or "")),
        permissions=StrategyPermissions(**dict(payload.get("permissions") or {})),
        parameters=deepcopy(dict(payload.get("parameters") or {})),
        state=deepcopy(dict(payload.get("state") or {})),
        source=str(payload.get("source") or "checkpoint"),
        created_at=_checkpoint_time(payload.get("created_at")),
        updated_at=_checkpoint_time(payload.get("updated_at")),
    )


def _debug_watchlist_membership_timeline(
    rows: tuple[dict[str, Any], ...],
) -> list[dict[str, Any]]:
    """Project exact fixture transitions into causal Watchlist snapshots."""

    transitions: list[dict[str, Any]] = []
    for source in rows:
        row = dict(source)
        watchlist_id = str(row.get("watchlist_id") or "").strip()
        ticker = _ticker(row.get("ticker"))
        event = str(row.get("event") or "added").strip().lower()
        if not watchlist_id:
            raise ValueError("Debug Watchlist event requires watchlist_id")
        if event not in {"added", "removed"}:
            raise ValueError("Debug Watchlist event must be added or removed")
        conid = int(row.get("ibkr_conid") or row.get("conid") or 0)
        if event == "added" and conid <= 0:
            raise ValueError(
                f"Debug Watchlist addition requires a positive point-in-time conid for {ticker}"
            )
        transitions.append(
            {
                **row,
                "effective_at": _debug_time(row.get("effective_at")),
                "event": event,
                "ibkr_conid": conid,
                "ticker": ticker,
                "watchlist_id": watchlist_id,
            }
        )
    transitions.sort(
        key=lambda row: (
            row["effective_at"],
            row["watchlist_id"],
            row["ticker"],
            row["event"],
        )
    )
    members_by_watchlist: dict[str, dict[str, dict[str, Any]]] = {}
    timeline: list[dict[str, Any]] = []
    offset = 0
    while offset < len(transitions):
        effective_at = transitions[offset]["effective_at"]
        end = offset
        while end < len(transitions) and transitions[end]["effective_at"] == effective_at:
            row = transitions[end]
            watchlist = members_by_watchlist.setdefault(row["watchlist_id"], {})
            if row["event"] == "removed":
                watchlist.pop(row["ticker"], None)
            else:
                existing = watchlist.get(row["ticker"])
                if existing and int(existing["ibkr_conid"]) != int(row["ibkr_conid"]):
                    raise ValueError(
                        "Debug Watchlist identity changed for "
                        f"{row['ticker']}: {existing['ibkr_conid']} -> {row['ibkr_conid']}"
                    )
                watchlist[row["ticker"]] = {
                    "ticker": row["ticker"],
                    "ibkr_conid": int(row["ibkr_conid"]),
                }
            end += 1
        union: dict[str, dict[str, Any]] = {}
        for watchlist_id, members in members_by_watchlist.items():
            for ticker, member in members.items():
                current = union.setdefault(
                    ticker,
                    {**member, "watchlist_ids": []},
                )
                if int(current["ibkr_conid"]) != int(member["ibkr_conid"]):
                    raise ValueError(
                        f"Debug Watchlist identity disagrees across lists for {ticker}"
                    )
                current["watchlist_ids"].append(watchlist_id)
        timeline.append(
            {
                "effective_at": effective_at,
                "members": sorted(union.values(), key=lambda row: row["ticker"]),
                "authority": [{
                    "authority": "backtest_debug_fixture",
                    "watchlist_ids": sorted(members_by_watchlist),
                }],
            }
        )
        offset = end
    return timeline


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


def _historical_watchlist_resolution_for_configuration(
    approved: dict[str, Any],
    *,
    as_of: datetime,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
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
        return [], []
    model = dict(approved.get("configuration_model") or {})
    if not model:
        raise ValueError(
            "Historical Watchlist resolution requires the approved configuration model"
        )
    from src.backend.watchlist_runtime_service import resolve_historical_watchlist

    members: dict[str, dict[str, Any]] = {}
    authority: list[dict[str, Any]] = []
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
        authority.append(
            {
                "watchlist_id": str(universe.get("scanner_view_id") or ""),
                "watchlist_name": str(universe.get("name") or ""),
                **dict(resolved.get("authority") or {}),
            }
        )
        for row in resolved.get("members") or []:
            ticker = str(row.get("ticker") or "").strip().upper()
            if ticker:
                members[ticker] = dict(row)
    return [members[ticker] for ticker in sorted(members)], authority


def _historical_watchlist_members_for_configuration(
    approved: dict[str, Any],
    *,
    as_of: datetime,
) -> list[dict[str, Any]]:
    members, _authority = _historical_watchlist_resolution_for_configuration(
        approved,
        as_of=as_of,
    )
    return members


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
    timeline: list[dict[str, Any]] = []
    for as_of in clocks:
        if as_of > local_end:
            continue
        members, authority = _historical_watchlist_resolution_for_configuration(
            approved,
            as_of=as_of,
        )
        timeline.append(
            {
                "effective_at": as_of,
                "members": members,
                "authority": authority,
            }
        )
    return timeline


def _historical_watchlist_membership_timeline_from_plans(
    plans: list[dict[str, Any]],
    *,
    projection_tickers: list[str] | None = None,
) -> list[dict[str, Any]]:
    if not plans:
        return []
    from src.backend.historical_watchlist_feature_service import (
        materialize_historical_watchlist_plans,
    )

    batch = materialize_historical_watchlist_plans(
        plans,
        projection_tickers=projection_tickers,
    )
    materialized_by_watchlist = {
        str(row.get("watchlist_id") or ""): dict(row)
        for row in batch.get("materializations") or []
    }
    transitions = sorted(
        (
            {**dict(event), "_watchlist_id": str(plan.get("watchlist_id") or "")}
            for plan in plans
            for chunk in materialized_by_watchlist.get(
                str(plan.get("watchlist_id") or ""), {}
            ).get("chunks") or []
            for event in dict(chunk).get("transitions") or []
        ),
        key=lambda event: (
            str(event.get("effective_at") or ""),
            str(event.get("_watchlist_id") or ""),
            str(event.get("ticker") or ""),
            str(event.get("event") or ""),
        ),
    )
    authority = []
    assignment_identities: dict[str, dict[str, Any]] = {}
    for plan in plans:
        watchlist_id = str(plan.get("watchlist_id") or "")
        materialized = materialized_by_watchlist.get(watchlist_id)
        if materialized is None:
            raise ValueError(f"Historical Watchlist materialization is missing: {watchlist_id}")
        authority.append(
            {
                "watchlist_id": watchlist_id,
                "plan_hash": str(materialized.get("plan_hash") or ""),
                "materialization_id": str(
                    materialized.get("application_materialization_id")
                    or materialized.get("materialization_id")
                    or ""
                ),
                "qmd_materialization_id": str(
                    materialized.get("materialization_id") or ""
                ),
                "batch_materialization_id": str(
                    batch.get("application_batch_materialization_id")
                    or batch.get("batch_materialization_id")
                    or ""
                ),
                "qmd_batch_materialization_id": str(
                    batch.get("batch_materialization_id") or ""
                ),
                "source_revision": dict(materialized.get("source_revision") or {}),
                "dependency_source_revision": dict(
                    batch.get("dependency_source_revision") or {}
                ),
                "external_feature_revisions": list(
                    materialized.get("external_feature_revisions") or []
                ),
                "identity_revision": dict(
                    materialized.get("identity_revision") or {}
                ),
                "relative_volume_revisions": list(
                    materialized.get("relative_volume_revisions") or []
                ),
                "calculation_revision": str(
                    materialized.get("calculation_revision") or ""
                ),
                "projection_complete": bool(
                    materialized.get("projection_complete", True)
                ),
                "projection_mode": str(
                    materialized.get("projection_mode") or "full"
                ),
                "projection_tickers": list(
                    materialized.get("projection_tickers") or []
                ),
                "source_tickers": list(
                    materialized.get("source_tickers") or []
                ),
            }
        )
        for row in materialized.get("assignment_identities") or []:
            ticker = str(row.get("ticker") or "").strip().upper()
            identity = {
                key: deepcopy(value)
                for key, value in dict(row).items()
                if key != "ticker"
            }
            if not ticker or int(identity.get("ibkr_conid") or 0) <= 0:
                continue
            prior = assignment_identities.get(ticker)
            if prior is not None and prior != identity:
                raise ValueError(
                    f"Historical Watchlists disagree on assignment identity for {ticker}"
                )
            assignment_identities[ticker] = identity
    # Keep the QMD transition representation compressed. Expanding every
    # one-second clock into a full union membership snapshot duplicated rich
    # evidence tens of thousands of times and could consume gigabytes before
    # Replay exposed its runtime. The controller applies these deltas causally.
    timeline: list[dict[str, Any]] = []
    index = 0
    while index < len(transitions):
        effective_at = _historical_watchlist_clock(transitions[index].get("effective_at"))
        clock_transitions: list[dict[str, Any]] = []
        while index < len(transitions):
            event = transitions[index]
            if _historical_watchlist_clock(event.get("effective_at")) != effective_at:
                break
            clock_transitions.append(
                {
                    **{key: deepcopy(value) for key, value in event.items() if key != "_watchlist_id"},
                    "ticker": str(event.get("ticker") or "").strip().upper(),
                    "watchlist_id": str(event.get("_watchlist_id") or ""),
                }
            )
            index += 1
        timeline.append(
            {
                "effective_at": effective_at,
                "transitions": clock_transitions,
                "authority": [dict(row) for row in authority],
            }
        )
    identity_catalog = [
        {"ticker": ticker, **assignment_identities[ticker]}
        for ticker in sorted(assignment_identities)
    ]
    if timeline:
        timeline[0]["assignment_identities"] = identity_catalog
    elif identity_catalog:
        timeline.append(
            {
                "effective_at": _historical_watchlist_clock(plans[0].get("start")),
                "transitions": [],
                "authority": [dict(row) for row in authority],
                "assignment_identities": identity_catalog,
            }
        )
    return timeline


def _historical_watchlist_plans_at_source_native_events(
    plans: list[dict[str, Any]],
    events: list[ReplaySignalEvent],
    *,
    configuration: dict[str, Any],
) -> list[dict[str, Any]]:
    """Scope activation Watchlists to immutable source-native event clocks.

    These Watchlists admit or reject an occurrence; they are not a second
    continuously evaluated strategy. QMD still replays the complete market and
    computes exact cross-sectional membership at every occurrence timestamp.
    """
    activation = dict(configuration.get("signal_activation") or {})
    enabled_streams = [
        dict(stream)
        for stream in activation.get("signal_streams") or []
        if bool(stream.get("enabled", True))
    ]
    if (
        not plans
        or not events
        or not enabled_streams
        or not all(
            str(stream.get("occurrence_source") or "").strip()
            for stream in enabled_streams
        )
    ):
        return plans
    eligible_events = [
        event
        for event in events
        if _strategy_can_enter_at(configuration, event.available_at)
    ]
    if not eligible_events:
        return plans
    scoped: list[dict[str, Any]] = []
    for source in plans:
        plan = deepcopy(source)
        start = _historical_watchlist_clock(plan.get("start"))
        end = _historical_watchlist_clock(plan.get("end"))
        cadence_ms = max(1, int(plan.get("cadence_ms") or 0))
        event_clocks = sorted({
            start
            + timedelta(
                microseconds=(
                    (
                        max(
                            0,
                            int(
                                (event.available_at - start).total_seconds()
                                * 1_000_000
                            ),
                        )
                        + cadence_ms * 1_000
                        - 1
                    )
                    // (cadence_ms * 1_000)
                    * cadence_ms
                    * 1_000
                )
            )
            for event in eligible_events
            if start <= event.available_at < end
        })
        windows = [
            {
                "start": clock.astimezone(UTC).isoformat(),
                "end": min(
                    end,
                    clock + timedelta(milliseconds=cadence_ms),
                ).astimezone(UTC).isoformat(),
            }
            for clock in event_clocks
            if start <= clock < end
        ]
        if not windows:
            scoped.append(plan)
            continue
        plan["evaluation_windows"] = windows
        body = {key: value for key, value in plan.items() if key != "plan_hash"}
        plan["plan_hash"] = "sha256:" + hashlib.sha256(
            json.dumps(
                body,
                sort_keys=True,
                separators=(",", ":"),
                ensure_ascii=True,
            ).encode("utf-8")
        ).hexdigest()
        scoped.append(plan)
    return scoped


def _historical_source_native_signal_identities(
    plans: list[dict[str, Any]],
    events: list[ReplaySignalEvent],
) -> dict[str, dict[str, Any]]:
    """Resolve stable point-in-time identities without replaying market data."""

    tickers = sorted({event.ticker for event in events if event.ticker})
    if not tickers:
        return {}
    if not plans:
        raise ValueError(
            "Source-native historical signals require a causal identity plan"
        )
    from src.backend.historical_watchlist_feature_service import (
        historical_watchlist_external_feature_bundle,
    )

    bundle = historical_watchlist_external_feature_bundle(
        plans[0],
        identity_tickers=tickers,
    )
    intervals_by_ticker: dict[str, list[dict[str, Any]]] = {}
    for interval in bundle.get("identity_intervals") or []:
        ticker = str(interval.get("ticker") or "").strip().upper()
        if ticker:
            intervals_by_ticker.setdefault(ticker, []).append(dict(interval))
    identities: dict[str, dict[str, Any]] = {}
    missing: list[str] = []
    changed: list[str] = []
    events_by_ticker: dict[str, list[ReplaySignalEvent]] = {}
    for event in events:
        events_by_ticker.setdefault(event.ticker, []).append(event)
    for ticker in tickers:
        matching_identities: dict[str, dict[str, Any]] = {}
        for event in events_by_ticker.get(ticker, []):
            matches = [
                dict(interval.get("identity") or {})
                for interval in intervals_by_ticker.get(ticker, [])
                if _historical_watchlist_clock(interval.get("start"))
                <= event.available_at
                < _historical_watchlist_clock(interval.get("end"))
            ]
            if len(matches) != 1 or int(matches[0].get("ibkr_conid") or 0) <= 0:
                missing.append(ticker)
                break
            identity = matches[0]
            matching_identities[
                json.dumps(identity, sort_keys=True, separators=(",", ":"))
            ] = identity
        if ticker in missing:
            continue
        if len(matching_identities) != 1:
            changed.append(ticker)
            continue
        identities[ticker] = next(iter(matching_identities.values()))
    if missing:
        raise ValueError(
            "Source-native historical signals require point-in-time conids: "
            + ", ".join(sorted(set(missing)))
        )
    if changed:
        raise ValueError(
            "Source-native historical signal identity changed during the run: "
            + ", ".join(sorted(set(changed)))
        )
    return identities


def _historical_strategy_quality_candidate_tickers(
    plans: list[dict[str, Any]],
    source_signal_tickers: tuple[str, ...],
) -> set[str]:
    """Return symbols that ever satisfy the Strategy's mandatory quality rule.

    This is only a computation prune. The retained symbols still replay the
    same causal rule at each decision instant; membership never authorizes an
    entry and is never projected as a second activation gate.
    """
    quality_plans = [
        deepcopy(plan)
        for plan in plans
        if str(plan.get("watchlist_id") or "") == "squeeze-tradable-candidates"
    ]
    if not quality_plans:
        raise ValueError(
            "Early Squeeze strategy requires the compiled squeeze quality plan"
        )
    requested = sorted({_ticker(value) for value in source_signal_tickers})
    timeline = _historical_watchlist_membership_timeline_from_plans(
        quality_plans,
        projection_tickers=requested,
    )
    eligible: set[str] = set()
    for snapshot in timeline:
        for row in snapshot.get("members") or []:
            ticker = str(row.get("ticker") or "").strip().upper()
            if ticker:
                eligible.add(ticker)
        for transition in snapshot.get("transitions") or []:
            if str(transition.get("event") or "") not in {
                "added",
                "rank_changed",
            }:
                continue
            ticker = str(transition.get("ticker") or "").strip().upper()
            if ticker:
                eligible.add(ticker)
    return eligible


def _strategy_can_enter_at(configuration: dict[str, Any], event_time: datetime) -> bool:
    profile = dict(configuration.get("strategy_profile") or {})
    lifecycle = dict(profile.get("lifecycle") or {})
    behavior = dict(
        lifecycle.get("trading_behavior")
        or profile.get("trading_behavior")
        or {}
    )
    eligible_sessions = {
        str(value).strip().lower()
        for value in behavior.get("eligible_sessions") or []
        if str(value).strip()
    }
    if not eligible_sessions:
        return True
    local = event_time.astimezone(NEW_YORK)
    local_time = local.timetz().replace(tzinfo=None)
    if clock_time(4, 0) <= local_time < clock_time(9, 30):
        phase = "premarket"
    elif clock_time(9, 30) <= local_time < clock_time(16, 0):
        phase = "regular"
    elif clock_time(16, 0) <= local_time < clock_time(20, 0):
        phase = "after_hours"
    else:
        return False
    if phase not in eligible_sessions:
        return False
    cutoff = str(behavior.get("entry_cutoff_time") or "").strip()
    if cutoff:
        try:
            cutoff_time = clock_time.fromisoformat(cutoff)
        except ValueError as exc:
            raise ValueError(
                f"Strategy entry cutoff time is invalid: {cutoff}"
            ) from exc
        if local_time > cutoff_time:
            return False
    return True


def _strategy_evaluation_end(
    configuration: dict[str, Any],
    *,
    session_start: datetime,
    session_end: datetime,
) -> datetime:
    profile = dict(configuration.get("strategy_profile") or {})
    lifecycle = dict(profile.get("lifecycle") or {})
    behavior = dict(
        lifecycle.get("trading_behavior")
        or profile.get("trading_behavior")
        or {}
    )
    eligible_sessions = {
        str(value).strip().lower()
        for value in behavior.get("eligible_sessions") or []
        if str(value).strip()
    }
    if eligible_sessions != {"premarket"}:
        return session_end
    flatten = str(behavior.get("flatten_time") or "").strip()
    if not flatten:
        return session_end
    try:
        flatten_time = clock_time.fromisoformat(flatten)
    except ValueError as exc:
        raise ValueError(f"Strategy flatten time is invalid: {flatten}") from exc
    local_start = session_start.astimezone(NEW_YORK)
    boundary = datetime.combine(
        local_start.date(), flatten_time, tzinfo=NEW_YORK
    ) + timedelta(seconds=1)
    return min(session_end, max(session_start, boundary))


def _historical_watchlist_transition_row(
    transition: dict[str, Any],
) -> dict[str, Any]:
    watchlist_id = str(transition.get("watchlist_id") or "")
    return {
        "ticker": str(transition.get("ticker") or "").strip().upper(),
        "rank": transition.get("rank"),
        "score": transition.get("score"),
        "membership_reason": transition.get("reason"),
        **deepcopy(dict(transition.get("evidence") or {})),
        **deepcopy(dict(transition.get("identity") or {})),
        "watchlist_ids": [watchlist_id],
    }


def _historical_watchlist_union_rows(
    rows_by_watchlist: dict[str, dict[str, dict[str, Any]]],
) -> list[dict[str, Any]]:
    union: dict[str, dict[str, Any]] = {}
    for watchlist_id, rows in rows_by_watchlist.items():
        for ticker, row in rows.items():
            existing = union.get(ticker)
            if existing is not None:
                if int(existing.get("ibkr_conid") or 0) != int(
                    row.get("ibkr_conid") or 0
                ):
                    raise ValueError(
                        f"Historical Watchlists disagree on point-in-time identity for {ticker}"
                    )
                existing["watchlist_ids"] = [
                    *list(existing.get("watchlist_ids") or []),
                    watchlist_id,
                ]
            else:
                union[ticker] = deepcopy(row)
    return sorted(
        union.values(),
        key=lambda row: (
            int(row.get("rank") or 10**9),
            str(row.get("ticker") or ""),
        ),
    )


def _historical_watchlist_clock(value: Any) -> datetime:
    parsed = datetime.fromisoformat(str(value or "").replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("Historical Watchlist transition clock must be timezone-aware")
    return parsed.astimezone(UTC)


def _historical_watchlist_plans_for_configuration(
    approved: dict[str, Any],
    *,
    start: datetime,
    end: datetime,
) -> list[dict[str, Any]]:
    from src.backend.historical_watchlist_plan import compile_historical_watchlist_plan

    configuration = dict(approved.get("payload") or {})
    model = dict(approved.get("configuration_model") or {})
    universes = [
        dict(row)
        for row in configuration.get("universes") or []
        if bool(row.get("enabled", True)) and str(row.get("source") or "") == "watchlist"
    ]
    resolved_universe = dict(configuration.get("universe") or {})
    run_plan_watchlist_ids = {
        str(value)
        for value in dict(configuration.get("run_plan") or {}).get("watchlist_ids")
        or []
        if str(value)
    }
    resolved_watchlists = {
        str(row.get("watchlist_id") or ""): dict(row)
        for row in resolved_universe.get("watchlist_snapshots") or []
        if str(row.get("watchlist_id") or "")
    }
    existing_watchlist_ids = {
        str(universe.get("scanner_view_id") or "") for universe in universes
    }
    universes.extend(
        {
            "universe_id": f"historical-run-plan-watchlist:{watchlist_id}",
            "name": str(resolved_watchlists.get(watchlist_id, {}).get("name") or watchlist_id),
            "source": "watchlist",
            "scanner_view_id": watchlist_id,
            "enabled": True,
        }
        for watchlist_id in sorted(run_plan_watchlist_ids - existing_watchlist_ids)
    )
    selected_source_watchlists = {
        str(row.get("source_id") or "")
        for row in dict(configuration.get("signal_activation") or {}).get(
            "signal_streams"
        ) or []
        if bool(row.get("enabled", True))
        and str(row.get("source_type") or "") == "watchlist"
        and str(row.get("source_id") or "")
    }
    existing_watchlist_ids = {
        str(universe.get("scanner_view_id") or "") for universe in universes
    }
    universes.extend(
        {
            "universe_id": f"historical-signal-source:{watchlist_id}",
            "name": f"Signal source {watchlist_id}",
            "source": "watchlist",
            "scanner_view_id": watchlist_id,
            "enabled": True,
        }
        for watchlist_id in sorted(selected_source_watchlists - existing_watchlist_ids)
    )
    if universes and not model:
        raise ValueError("Historical Watchlist plans require the approved configuration model")
    return [
        compile_historical_watchlist_plan(
            model,
            str(universe.get("scanner_view_id") or ""),
            start=start,
            end=end,
        )
        for universe in universes
    ]


def _historical_core_signal_plans_for_configuration(
    approved: dict[str, Any],
    *,
    start: datetime,
    end: datetime,
) -> list[dict[str, Any]]:
    from src.backend.historical_watchlist_plan import compile_historical_watchlist_plan

    configuration = dict(approved.get("payload") or {})
    model = deepcopy(dict(approved.get("configuration_model") or {}))
    activation = dict(configuration.get("signal_activation") or {})
    streams = [
        dict(row)
        for row in activation.get("signal_streams") or []
        if bool(row.get("enabled", True))
        and str(row.get("source_type") or "core_scan") == "core_scan"
        and not str(row.get("occurrence_source") or "").strip()
    ]
    if streams and not model:
        raise ValueError("Historical core Signal Streams require the approved configuration model")
    discovery = model.setdefault("market_discovery", {})
    watchlists = list(discovery.get("watchlists") or [])
    plans: list[dict[str, Any]] = []
    for stream in streams:
        if str(stream.get("rearm_policy") or "after_false") != "after_false":
            raise ValueError(
                f"Historical Signal Stream {stream.get('signal_stream_id')} requires after_false rearming"
            )
        virtual_id = f"historical-signal-stream:{stream.get('signal_stream_id')}"
        watchlists.append({
            "watchlist_id": virtual_id,
            "name": str(stream.get("name") or virtual_id),
            "enabled": True,
            "source_scan_id": str(stream.get("source_id") or stream.get("source_scan_id") or "qmd-core-scan"),
            "inclusion_rule_sets": list(stream.get("inclusion_rule_sets") or []),
            "inclusion_operator": str(stream.get("inclusion_operator") or "all"),
            "exclusion_rule_sets": [],
            "ranking_field": "market.liquidity_rank",
            "ranking_direction": "ascending",
            # Every QMD candidate is evaluated. This ceiling bounds only the number of
            # simultaneously matching members retained by the transition reducer.
            "maximum_size": 5_000,
            "refresh_interval_ms": max(1, int(stream.get("refresh_interval_ms") or 1_000)),
            "membership_expiry": "end_of_trading_day",
            "membership_ttl_ms": 0,
            "manual_inclusions": [],
            "manual_exclusions": [],
        })
        discovery["watchlists"] = watchlists
        plan = compile_historical_watchlist_plan(model, virtual_id, start=start, end=end)
        plans.append({
            **plan,
            "signal_stream_id": str(stream.get("signal_stream_id") or ""),
        })
    return plans


def replay_preflight(
    *,
    session_date: date,
    start_time: clock_time,
    initial_cash: float,
    assignment_ids: tuple[str, ...] = (),
    tickers: tuple[str, ...] = (),
    configuration_revision: dict[str, Any] | None = None,
    execution_mode: str = "strategy",
    session_profile_id: str = "",
    execution_route_id: str = "",
) -> dict[str, Any]:
    approved = configuration_revision or (
        candidate_session_configuration_snapshot(
            "replay",
            session_profile_id=session_profile_id,
            execution_route_id=execution_route_id,
        )
        if execution_mode == "manual"
        else replay_configuration_snapshot()
    )
    configuration = approved["payload"]
    definition = ReplayRunDefinition(
        session_date=session_date,
        start_time=start_time,
        initial_cash=initial_cash,
        assignment_ids=assignment_ids,
        tickers=tickers,
        configuration_revision=approved,
        execution_mode=execution_mode,
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
        for row in configuration.get("assignments") or []
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
            *(_ticker(value) for value in tickers),
        }
    )
    run_plan = dict(configuration.get("run_plan") or {})
    signal_streams = [
        dict(row)
        for row in dict(configuration.get("signal_activation") or {}).get(
            "signal_streams"
        )
        or []
        if bool(row.get("enabled", True))
    ]
    watchlist_source_ids = {
        str(value)
        for value in run_plan.get("watchlist_ids") or []
        if str(value)
    }
    watchlist_source_ids.update(
        str(universe.get("scanner_view_id") or universe.get("name") or "")
        for universe in configuration.get("universes") or []
        if bool(universe.get("enabled", True))
        and str(universe.get("source") or "") == "watchlist"
    )
    watchlist_source_ids.discard("")
    strategy_market_sources = bool(
        assignments
        or watchlist_source_ids
        or signal_streams
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
            "immutable_configuration",
            "Immutable Test Candidate" if approved.get("release_state") == "test_candidate" else "Approved configuration",
            bool(approved.get("revision_id") and approved.get("content_hash")),
            f"Revision {approved.get('revision')} · {approved.get('label')} is pinned for the full run."
            if approved.get("revision_id")
            else "No immutable configuration revision is available.",
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
            True,
            (
                f"{len(watchlist_source_ids)} causal Watchlist source(s) are pinned; membership materializes asynchronously during Replay warm-up."
                if watchlist_source_ids
                else "No Watchlist-backed universe is required by this Run Plan."
            ),
            "Pinned Run Plan configuration; no historical market materialization in preflight",
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
            "Strategy market universe" if execution_mode == "strategy" else "Replay symbol",
            strategy_market_sources if execution_mode == "strategy" else bool(resolved_tickers),
            (
                f"The Run Plan will causally admit every ticker from {len(signal_streams)} Signal Stream(s) and {len(watchlist_source_ids)} Watchlist source(s)."
                if execution_mode == "strategy" and strategy_market_sources
                else "The Strategy Run Plan has no enabled Signal Stream, Watchlist, or assignment source."
                if execution_mode == "strategy"
                else f"Manual Replay starts with {', '.join(resolved_tickers[:8])}."
                if resolved_tickers
                else "Manual Replay requires a starting symbol."
            ),
            (
                "Run Plan Signal Streams plus point-in-time Watchlist membership"
                if execution_mode == "strategy"
                else "Explicit manual session symbol"
            ),
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
            required=execution_mode == "strategy" and bool(assignment_ids),
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
        "configuration_release_state": str(approved.get("release_state") or "approved"),
        "run_plan_id": approved.get("run_plan_id", ""),
        "session_profile_id": approved.get("session_profile_id", ""),
        "execution_route_id": approved.get("execution_route_id", ""),
        "execution_mode": execution_mode,
        "available_run_plans": deepcopy(approved.get("available_run_plans") or []),
    }


def backtest_preflight(
    *,
    anchor_date: date,
    session_count: int,
    initial_cash: float = 100_000.0,
    end_time: clock_time = clock_time(20, 0),
    configuration_revision: dict[str, Any] | None = None,
) -> dict[str, Any]:
    if not clock_time(4, 0) < end_time <= clock_time(20, 0):
        raise ValueError("Backtest end time must be after 04:00 and no later than 20:00 New York")
    approved = configuration_revision or backtest_configuration_snapshot()
    base = historical_preflight(
        mode=RunMode.BACKTEST.value,
        anchor_date=anchor_date,
        session_count=session_count,
    )
    configuration = dict(approved.get("payload") or {})
    run_plan = dict(configuration.get("run_plan") or {})
    selected_signal_stream_ids = {
        str(value)
        for value in run_plan.get("signal_stream_ids") or []
        if str(value)
    }
    activated_signal_streams = [
        dict(row)
        for row in dict(configuration.get("signal_activation") or {}).get(
            "signal_streams"
        )
        or []
        if bool(row.get("enabled", True))
        and str(row.get("signal_stream_id") or "") in selected_signal_stream_ids
    ]
    source_native_activation = bool(activated_signal_streams) and all(
        str(row.get("occurrence_source") or "").strip()
        for row in activated_signal_streams
    )
    watchlist_policy = str(
        dict(run_plan.get("activation") or {}).get("watchlist_policy")
        or "any_selected"
    )
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
    resolved_universe = dict(configuration.get("universe") or {})
    if (
        bool(resolved_universe.get("enabled", True))
        and str(resolved_universe.get("source") or "") == "watchlist"
    ):
        watchlists.extend(
            {
                "enabled": True,
                "name": str(row.get("name") or row.get("watchlist_id") or ""),
                "scanner_view_id": str(row.get("watchlist_id") or ""),
                "source": "watchlist",
            }
            for row in resolved_universe.get("watchlist_snapshots") or []
            if str(row.get("watchlist_id") or "")
            and str(row.get("watchlist_id") or "")
            not in {str(existing.get("scanner_view_id") or "") for existing in watchlists}
        )
    watchlist_members: list[dict[str, Any]] = []
    watchlist_plans: list[dict[str, Any]] = []
    watchlist_snapshot_count = 0
    watchlist_error = ""
    if watchlists and sessions and not (
        source_native_activation and watchlist_policy == "not_required"
    ):
        try:
            watchlist_plans = _historical_watchlist_plans_for_configuration(
                approved,
                start=datetime.combine(sessions[0], clock_time(4, 0), tzinfo=NEW_YORK),
                end=datetime.combine(sessions[-1], end_time, tzinfo=NEW_YORK),
            )
            timeline = _historical_watchlist_membership_timeline_from_plans(
                watchlist_plans
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
            "label": "Immutable Test Candidate" if approved.get("release_state") == "test_candidate" else "Approved application revision",
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
                else "The selected configuration has no enabled account binding for Backtest."
            ),
            "evidence": ", ".join(sorted(binding_keys)) or "No Backtest account authority",
            "required": True,
        }
    )
    work_ready = bool(
        assignments
        or watchlist_members
        or (source_native_activation and watchlist_policy == "not_required")
    ) and not watchlist_error
    checks.append(
        {
            "id": "strategy_assignments",
            "label": "Historical strategy population",
            "status": "ready" if work_ready else "blocked",
            "summary": (
                f"{len(activated_signal_streams)} source-native Signal Stream(s) causally seed Strategy evaluation; liquidity and entry rules are evaluated only after each occurrence."
                if source_native_activation and watchlist_policy == "not_required"
                else (
                    f"{len(assignments)} pinned assignment(s) and {len(watchlist_members)} causal Watchlist member(s) across {watchlist_snapshot_count} transition state(s) are configured."
                    if work_ready
                    else watchlist_error or "Backtest needs an active assignment, source-native Signal Stream, or non-empty causal Watchlist universe."
                )
            ),
            "evidence": (
                "Persisted Signal Stream occurrences define the bounded ticker and event-time population; the controller loads causal market and indicator frames only for those tickers."
                if source_native_activation and watchlist_policy == "not_required"
                else "The revisioned Watchlist timeline is pinned and applied at every configured intraday refresh clock before same-clock Strategy evaluation."
            ),
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
        "run_plan_id": approved.get("run_plan_id", ""),
        "available_run_plans": deepcopy(approved.get("available_run_plans") or []),
        "historical_watchlist_plans": watchlist_plans,
        "initial_cash": initial_cash,
        "experiment_end_time": end_time.isoformat(timespec="seconds"),
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
    approved = configuration_revision or backtest_debug_configuration_snapshot()
    configuration = dict(approved.get("payload") or {})
    run_plan = dict(configuration.get("run_plan") or {})
    required_watchlist_ids = [
        str(value) for value in run_plan.get("watchlist_ids") or [] if str(value)
    ]
    watchlist_policy = str(
        dict(run_plan.get("activation") or {}).get("watchlist_policy")
        or "any_selected"
    )
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
            "Immutable Test Candidate" if approved.get("release_state") == "test_candidate" else "Approved application revision",
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
            else "The selected configuration has no Backtest Debug account binding.",
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
        "run_plan_id": approved.get("run_plan_id", ""),
        "available_run_plans": deepcopy(approved.get("available_run_plans") or []),
        "configuration_label": approved.get("label", ""),
        "required_watchlist_ids": required_watchlist_ids,
        "watchlist_policy": watchlist_policy,
    }


def _retryable_historical_stream_error(error: Exception) -> bool:
    if isinstance(error, (TimeoutError, ConnectionClosedError)):
        return True
    detail = str(error).lower()
    return isinstance(error, RuntimeError) and (
        "historical cache byte limit exceeded" in detail
        or "qmd derived stream closed early" in detail
    )


async def _stream_historical_bar_derived_frames(
    *,
    ticker: str,
    timeframe: str,
    start: datetime,
    end: datetime,
    frame_sink: Callable[[list[ReplayDerivedFrame]], Awaitable[None]],
    authority_sink: Callable[[str, dict[str, Any]], None] | None,
    indicator_columns: tuple[str, ...],
    batch_size: int = 1_000,
) -> None:
    """Load completed-bar decisions from the persisted prepared-chart artifact.

    Source-native squeeze strategies evaluate only at completed rule timeframes.
    Replaying raw events once per ticker to recreate the same closed bars is
    redundant; QMD History's revisioned bar artifact is the shared authority.
    """

    def fetch() -> tuple[list[ReplayDerivedFrame], dict[str, Any]]:
        payload = qmd_product_request(
            QmdProductRequest(
                "chart",
                authority="history",
                mode="backtest",
                ticker=ticker,
                timeframe=timeframe,
                start=start.isoformat(),
                end=end.isoformat(),
                as_of=end.isoformat(),
                indicator_columns=indicator_columns,
                allow_persisted_bars=True,
                include_market_signals=False,
                include_structure=False,
                stage="bars",
                limit=50_000,
                timeout_seconds=180,
            )
        ).payload
        bars = [dict(row) for row in payload.get("bars") or []]
        indicators = [dict(row) for row in payload.get("indicators") or []]
        indicator_by_start = {
            str(row.get("bar_start") or ""): row
            for row in indicators
            if str(row.get("bar_start") or "")
        }
        if bars and len(indicator_by_start) < len(bars):
            raise RuntimeError(
                f"Prepared QMD bars omitted indicators for {ticker} {timeframe}: "
                f"bars={len(bars)} indicators={len(indicator_by_start)}"
            )
        frames: list[ReplayDerivedFrame] = []
        for sequence, bar in enumerate(bars, start=1):
            bar_start = str(bar.get("bar_start") or "")
            indicator = dict(indicator_by_start.get(bar_start) or {})
            indicator.setdefault("bar_start", bar_start)
            indicator.setdefault("bar_end", bar.get("bar_end"))
            indicator.setdefault("close", bar.get("close"))
            indicator.setdefault("sym", bar.get("sym") or ticker)
            indicator.setdefault("timeframe", bar.get("timeframe") or timeframe)
            frames.append(
                _replay_derived_frame_from_payload(
                    {
                        "as_of": bar.get("bar_end") or indicator.get("bar_end"),
                        "bar": bar,
                        "indicator": indicator,
                        "sequence": sequence,
                    },
                    ticker=ticker,
                    timeframe=timeframe,
                    fallback_sequence=sequence,
                )
            )
        provenance = dict(payload.get("indicator_provenance") or {})
        source = dict(provenance.get("source") or {})
        authority = {
            "authority": "qmd_history_prepared_closed_bars",
            "revision_token": str(source.get("revision_token") or ""),
            "source_plan_hash": str(source.get("source_plan_hash") or ""),
            "complete_for_history": bool(source.get("complete_for_history")),
            "source_tiers": list(source.get("tiers") or ()),
            "engine_version": str(provenance.get("engine_version") or ""),
            "calculation_revision": str(
                provenance.get("calculation_revision") or ""
            ),
            "event_count": int(source.get("event_count") or 0),
            "indicator_columns": sorted(indicator_columns),
        }
        return frames, authority

    frames, authority = await asyncio.to_thread(fetch)
    for index in range(0, len(frames), batch_size):
        await frame_sink(frames[index : index + batch_size])
    if authority_sink is not None:
        authority_sink(f"derived:{_ticker(ticker)}:{timeframe}", authority)


async def _stream_historical_derived_frames(
    *,
    ticker: str,
    timeframe: str,
    start: datetime,
    end: datetime,
    frame_sink: Callable[[list[ReplayDerivedFrame]], Awaitable[None]],
    authority_sink: Callable[[str, dict[str, Any]], None] | None = None,
    batch_size: int = 1_000,
    indicator_columns: tuple[str, ...] | None = None,
) -> None:
    """Stream one frozen QMD product to bounded run-owned storage."""

    url = qmd_history_websocket_url(
        f"/stream/derived/{urllib.parse.quote(ticker)}",
        {
            "start": start.isoformat(),
            "end": end.isoformat(),
            "timeframe": timeframe,
            "emit": "frames",
            "frame_batch_size": 32,
            "as_of": end.isoformat(),
            "updates_per_second": 0,
            "retain_cache": "false",
            **(
                {"indicator_columns": ",".join(indicator_columns)}
                if indicator_columns
                else {}
            ),
        },
    )
    metadata: dict[str, Any] | None = None
    batch: list[ReplayDerivedFrame] = []
    received = 0
    async with websockets.connect(
        url,
        ping_interval=20,
        ping_timeout=300,
        max_queue=4,
        max_size=64 * 1024 * 1024,
    ) as socket:
        try:
            async for message in socket:
                payload = json.loads(
                    message.decode("utf-8") if isinstance(message, bytes) else message
                )
                if payload.get("error"):
                    raise RuntimeError(
                        f"QMD derived stream failed for {ticker}: {payload['error']}"
                    )
                if payload.get("type") == "metadata":
                    metadata = payload
                    continue
                source_frames = (
                    list(payload.get("frames") or [])
                    if payload.get("type") == "frames_batch"
                    else [payload]
                )
                for source_frame in source_frames:
                    received += 1
                    batch.append(
                        _replay_derived_frame_from_payload(
                            source_frame,
                            ticker=ticker,
                            timeframe=timeframe,
                            fallback_sequence=received,
                        )
                    )
                if len(batch) >= batch_size:
                    await frame_sink(batch)
                    batch = []
        except ConnectionClosedError as exc:
            expected = int((metadata or {}).get("frame_count") or -1)
            if metadata is None or received != expected:
                raise RuntimeError(
                    "QMD derived stream closed early for "
                    f"{ticker} {timeframe}: received_frames={received} "
                    f"expected_frames={expected}; transport={exc}"
                ) from exc
    if batch:
        await frame_sink(batch)
    if metadata is None:
        raise RuntimeError(f"QMD derived frame stream omitted authority metadata for {ticker}")
    expected = int(metadata.get("frame_count") or 0)
    if received != expected:
        raise RuntimeError(
            f"QMD derived frame stream returned {received} of {expected} frames "
            f"for {ticker} {timeframe}"
        )
    authority = _qmd_payload_authority(metadata, authority="qmd_history_derived")
    if authority_sink is not None:
        authority_sink(f"derived:{_ticker(ticker)}:{timeframe}", authority)


async def _historical_derived_frames(
    *,
    ticker: str,
    timeframe: str,
    start: datetime,
    end: datetime,
    authority_sink: Callable[[str, dict[str, Any]], None] | None = None,
    frame_cache: dict[tuple[str, str, str, str], Any] | None = None,
) -> list[ReplayDerivedFrame]:
    cache_key = (_ticker(ticker), timeframe, start.isoformat(), end.isoformat())
    if frame_cache is not None and cache_key in frame_cache:
        cached_frames, cached_authority = frame_cache[cache_key]
        if authority_sink is not None:
            authority_sink(f"derived:{_ticker(ticker)}:{timeframe}", dict(cached_authority))
        return [_copy_replay_frame(frame) for frame in cached_frames]
    url = qmd_history_websocket_url(
        f"/stream/derived/{urllib.parse.quote(ticker)}",
        {
            "start": start.isoformat(),
            "end": end.isoformat(),
            "timeframe": timeframe,
            "emit": "frames",
            "frame_batch_size": 32,
            "as_of": end.isoformat(),
            "updates_per_second": 0,
            # Replay consumes this complete, frozen response into its own run
            # state. Retaining every ticker/timeframe product in QMD History
            # exhausts the service-wide cache before the run can start.
            "retain_cache": "false",
        },
    )
    frames: list[ReplayDerivedFrame] = []
    metadata: dict[str, Any] | None = None
    async with websockets.connect(
        url,
        ping_interval=20,
        # Historical cache builds can be CPU-bound for several minutes while
        # the HTTP health plane remains ready. Keep transport liveness bounded
        # without treating one busy 20-second interval as missing market data.
        ping_timeout=300,
        max_queue=4,
        max_size=64 * 1024 * 1024,
    ) as socket:
        try:
            async for message in socket:
                metadata = _append_historical_derived_message(
                    message,
                    frames=frames,
                    metadata=metadata,
                    ticker=ticker,
                    timeframe=timeframe,
                )
        except ConnectionClosedError as exc:
            expected = int((metadata or {}).get("frame_count") or -1)
            if metadata is None or len(frames) != expected:
                raise RuntimeError(
                    "QMD derived stream closed early for "
                    f"{ticker} {timeframe}: received_frames={len(frames)} "
                    f"expected_frames={expected}; transport={exc}"
                ) from exc
    if metadata is None:
        raise RuntimeError(f"QMD derived frame stream omitted authority metadata for {ticker}")
    authority = _qmd_payload_authority(metadata, authority="qmd_history_derived")
    if authority_sink is not None:
        authority_sink(f"derived:{_ticker(ticker)}:{timeframe}", authority)
    expected = int(metadata.get("frame_count") or 0)
    if len(frames) != expected:
        raise RuntimeError(
            f"QMD derived frame stream returned {len(frames)} of {expected} frames "
            f"for {ticker} {timeframe}"
        )
    if frame_cache is not None:
        frame_cache[cache_key] = (tuple(frames), dict(authority))
    return [_copy_replay_frame(frame) for frame in frames] if frame_cache is not None else frames


def _copy_replay_frame(frame: ReplayDerivedFrame) -> ReplayDerivedFrame:
    return ReplayDerivedFrame(
        as_of=frame.as_of,
        bar=frame.bar,
        indicator=frame.indicator,
        sequence=frame.sequence,
        ticker=frame.ticker,
        timeframe=frame.timeframe,
        signals=dict(frame.signals),
    )


def _append_historical_derived_message(
    message: str | bytes,
    *,
    frames: list[ReplayDerivedFrame],
    metadata: dict[str, Any] | None,
    ticker: str,
    timeframe: str,
) -> dict[str, Any] | None:
    payload = json.loads(
        message.decode("utf-8") if isinstance(message, bytes) else message
    )
    if payload.get("error"):
        raise RuntimeError(f"QMD derived stream failed for {ticker}: {payload['error']}")
    if payload.get("type") == "metadata":
        return payload
    source_frames = (
        list(payload.get("frames") or [])
        if payload.get("type") == "frames_batch"
        else [payload]
    )
    for source_frame in source_frames:
        frames.append(
            _replay_derived_frame_from_payload(
                source_frame,
                ticker=ticker,
                timeframe=timeframe,
                fallback_sequence=len(frames) + 1,
            )
        )
    return metadata


def _replay_derived_frame_from_payload(
    payload: dict[str, Any],
    *,
    ticker: str,
    timeframe: str,
    fallback_sequence: int,
) -> ReplayDerivedFrame:
    bar = dict(payload.get("bar") or {})
    indicator = dict(payload.get("indicator") or {})
    return ReplayDerivedFrame(
        as_of=_aware_datetime(
            payload.get("as_of") or indicator.get("bar_end") or bar.get("bar_end")
        ),
        bar={key: value for key, value in bar.items() if key in _STRATEGY_BAR_FIELDS},
        indicator={
            key: value
            for key, value in indicator.items()
            if key in _STRATEGY_INDICATOR_FIELDS
        },
        sequence=int(payload.get("sequence") or fallback_sequence),
        ticker=_ticker(indicator.get("sym") or bar.get("sym") or ticker),
        timeframe=str(indicator.get("timeframe") or bar.get("timeframe") or timeframe),
    )


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
    evidence = {
        "authority": authority,
        "revision_token": token,
        "source_plan_hash": plan_hash,
        "complete_for_history": bool(revision.get("complete_for_history", False)),
        "source_tiers": list(revision.get("source_tiers") or ()),
        "engine_version": str(cache.get("engine_version") or payload.get("engine_version") or ""),
        "event_count": int(cache.get("event_count") or payload.get("event_count") or 0),
    }
    if isinstance(payload.get("indicator_columns"), list):
        evidence["indicator_columns"] = sorted(
            str(column) for column in payload["indicator_columns"]
        )
    return evidence


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


def _filter_historical_signal_events(
    events: list[ReplaySignalEvent],
    tickers: tuple[str, ...],
) -> list[ReplaySignalEvent]:
    """Apply an explicit UAT population without changing market-wide defaults."""

    selected = {_ticker(value) for value in tickers}
    if not selected:
        return events
    return [event for event in events if event.ticker in selected]


def _filter_loaded_source_native_occurrences(
    loaded_streams: list[tuple[dict[str, Any], dict[str, Any]]],
    tickers: tuple[str, ...],
) -> list[tuple[dict[str, Any], dict[str, Any]]]:
    """Scope source-native persistence itself to an explicit UAT population."""

    selected = {_ticker(value) for value in tickers}
    if not selected:
        return loaded_streams
    return [
        (
            stream,
            {
                **loaded,
                "occurrences": [
                    occurrence
                    for occurrence in loaded.get("occurrences") or []
                    if _ticker(dict(occurrence).get("ticker")) in selected
                ],
            },
        )
        for stream, loaded in loaded_streams
    ]


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


def _optional_datetime(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo is not None else None


def _historical_watchlist_assignment_is_observable(
    *,
    source: str,
    ticker: str,
    active_tickers: set[str],
    strategy_engaged_tickers: set[str],
    position_quantity: float,
) -> bool:
    """Keep a discovered campaign observable for the rest of its run session.

    Source-native signal membership is intentionally transient.  It admits the
    ticker into the Strategy once; it must not become a recurring entry or
    re-entry prerequisite after the occurrence window expires.
    """

    if "historical_watchlist" not in source:
        return True
    return bool(
        ticker in active_tickers
        or ticker in strategy_engaged_tickers
        or float(position_quantity) != 0
    )


def _historical_signal_occurrence(
    stream: dict[str, Any],
    *,
    frame: ReplayDerivedFrame,
    source_values: dict[str, Any],
    columns: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    stream_id = str(stream.get("signal_stream_id") or "")
    identity = f"{stream_id}|{frame.ticker}|{frame.as_of.astimezone(UTC).isoformat()}"
    event_id = hashlib.sha256(identity.encode("utf-8")).hexdigest()
    field_evidence: dict[str, dict[str, Any]] = {}
    evidence: dict[str, Any] = {}
    for column_id in stream.get("columns") or []:
        column = columns.get(str(column_id), {})
        field_ref = str(column.get("field_ref") or "")
        interval = interval_expression(
            dict(stream.get("column_intervals") or {}).get(str(column_id))
        )
        aggregation = str(
            dict(stream.get("column_aggregations") or {}).get(str(column_id)) or ""
        )
        instance_ref = (
            field_instance_ref(field_ref, interval, aggregation) if field_ref else ""
        )
        value = source_values.get(instance_ref) if instance_ref else None
        if value is None and field_ref:
            value = source_values.get(field_ref)
        if isinstance(value, dict):
            value = value.get("value")
        if value is None:
            continue
        key = instance_ref or field_ref or str(column_id)
        field_evidence[key] = {
            "field_ref": field_ref,
            "interval": interval,
            "aggregation": aggregation,
            "value": value,
            "available_at": frame.as_of.astimezone(UTC).isoformat(),
        }
        source_id = str(column.get("source_id") or "")
        if source_id:
            evidence[source_id] = value
    return {
        "schema_version": 1,
        "event_id": event_id,
        "signal_id": event_id,
        "signal_stream_id": stream_id,
        "signal_stream_revision": int(stream.get("revision") or 1),
        "ticker": frame.ticker,
        "effective_at": frame.as_of.astimezone(UTC).isoformat(),
        "event_time": frame.as_of.astimezone(UTC).isoformat(),
        "source_type": str(stream.get("source_type") or "core_scan"),
        "source_id": str(stream.get("source_id") or stream.get("source_scan_id") or ""),
        "field_evidence": field_evidence,
        "evidence": evidence,
        "authority": "qmd_history_causal_signal_reconstruction",
    }


def _positive(value: Any) -> float:
    number = float(value or 0)
    return number if number > 0 else 0.0


def _optional_positive(value: Any) -> float | None:
    number = _positive(value)
    return number or None
