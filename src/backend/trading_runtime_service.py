from __future__ import annotations

import os
import json
import re
import threading
import urllib.parse
from dataclasses import replace
from datetime import date, datetime, time, timedelta
from functools import lru_cache
from pathlib import Path
from typing import Any
from uuid import uuid4
from zoneinfo import ZoneInfo

from pipelines.reference_data.clickhouse_load_market_references import build_condition_token_rows
from src.backend.qmd_gateway_client import (
    ENRICHED_QMD_TIMEFRAMES,
    QmdProductRequest,
    QmdServiceError,
    qmd_history_base_url as historical_gateway_base_url,
    qmd_history_get_json as _historical_gateway_get,
    qmd_history_websocket_url as historical_gateway_websocket_url,
    qmd_product_request,
    qmd_intraday_bar_history,
)
from src.data_provider.calendar import market_sessions
from src.trading_runtime.journal import TradingJournal
from src.trading_runtime.orchestrator import historical_run_window
from src.trading_runtime.runtime import RunMode
from src.trading_runtime.taxonomy import StrategyTaxonomy, taxonomy_catalog_payload
from src.trading_runtime.strategy_engine import (
    AssignmentStatus,
    StrategyAssignment,
    StrategyObservation,
    StrategyPermissions,
)
from src.trading_runtime.strategy_registry import (
    installed_strategy_definitions,
    strategy_executor,
    strategy_executor_optional,
)
from src.trading_runtime.strategy_campaign import (
    StrategyCampaignOrchestrator,
    campaign_state,
)
from src.trading_runtime.strategy_orders import IbkrStrategyOrderPlanner
from src.trading_runtime.domain import InstrumentContract


REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_TRADING_RUNTIME_ROOT = Path(r"D:\TradingML\runtimes\trading")
SUPPORTED_HISTORICAL_TIMEFRAMES = {
    "100ms",
    "1s",
    "5s",
    "10s",
    "30s",
    "1m",
    "5m",
    "1h",
    "1d",
    "1w",
    "1mo",
    "1y",
}
MACRO_CHART_TIMEFRAMES = {"1d", "1w", "1mo", "1y"}
HISTORICAL_CHUNK_MINUTES = 15
MARKET_REFERENCE_DIR = REPO_ROOT / "research" / "market_references" / "massive"
BUILTIN_STRATEGY_LOCK = threading.Lock()


@lru_cache(maxsize=1)
def trading_journal() -> TradingJournal:
    configured = os.environ.get("TRADING_JOURNAL_PATH", "").strip()
    runtime_root = Path(os.environ.get("TRADING_RUNTIME_ROOT", str(DEFAULT_TRADING_RUNTIME_ROOT)))
    path = Path(configured) if configured else runtime_root / "journal.sqlite3"
    return TradingJournal(path)


def close_trading_journal() -> None:
    """Close and evict the process-owned journal, if it was opened.

    The journal is intentionally shared for the process lifetime.  Explicit
    eviction must close the SQLite connection first so application shutdown
    and tests that replace the runtime root do not leak file handles.
    """

    if trading_journal.cache_info().currsize:
        trading_journal().close()
        trading_journal.cache_clear()


def save_strategy_definition(payload: dict[str, Any]) -> dict[str, Any]:
    strategy_id = str(payload.get("strategy_id") or "").strip()
    name = str(payload.get("name") or "").strip()
    implementation = str(payload.get("implementation") or "").strip()
    if not strategy_id or not name or not implementation:
        raise ValueError("strategy_id, name, and implementation are required")
    revision = int(payload.get("revision") or 0)
    if revision <= 0:
        raise ValueError(
            "Strategy definitions are immutable installed executor revisions; revision is required"
        )
    registration = strategy_executor(strategy_id, revision)
    if implementation != registration.implementation:
        raise ValueError(
            f"Strategy definition implementation does not match installed executor {strategy_id}@{revision}"
        )
    automatic = bool(payload.get("automatic", True))
    config = dict(payload.get("config") or {})
    taxonomy = StrategyTaxonomy.from_payload(payload.get("taxonomy") or config.get("taxonomy"))
    if automatic and not (taxonomy.indicators or taxonomy.signals):
        raise ValueError("Automatic strategies must declare at least one indicator or signal input")
    config["taxonomy"] = taxonomy.payload()
    trading_journal().save_strategy(
        strategy_id=strategy_id,
        revision=revision,
        name=name,
        implementation=implementation,
        automatic=automatic,
        enabled=bool(payload.get("enabled", True)),
        config=config,
    )
    return _strategy_definition_payload(trading_journal().strategy(strategy_id, revision) or {})


def list_strategy_definitions(latest_only: bool = True) -> list[dict[str, Any]]:
    ensure_builtin_strategy_definition()
    rows = [
        _strategy_definition_payload(row)
        for row in trading_journal().strategies(latest_only=latest_only)
    ]
    for row in rows:
        registration = strategy_executor_optional(
            str(row.get("strategy_id") or ""), int(row.get("revision") or 0)
        )
        row["executor"] = {
            "installed": registration is not None,
            "schema_version": (
                registration.executor_schema_version if registration is not None else None
            ),
            "key": f"{row.get('strategy_id')}@{row.get('revision')}",
        }
    return rows


def get_strategy_definition(strategy_id: str, revision: int | None = None) -> dict[str, Any]:
    ensure_builtin_strategy_definition()
    result = trading_journal().strategy(strategy_id, revision)
    if result is None:
        raise KeyError(strategy_id)
    payload = _strategy_definition_payload(result)
    registration = strategy_executor_optional(
        str(payload.get("strategy_id") or ""), int(payload.get("revision") or 0)
    )
    payload["executor"] = {
        "installed": registration is not None,
        "schema_version": (
            registration.executor_schema_version if registration is not None else None
        ),
        "key": f"{payload.get('strategy_id')}@{payload.get('revision')}",
    }
    return payload


def trading_taxonomy_catalog() -> dict[str, Any]:
    return taxonomy_catalog_payload()


def ensure_builtin_strategy_definition() -> None:
    with BUILTIN_STRATEGY_LOCK:
        for definition in installed_strategy_definitions():
            strategy_id = str(definition["strategy_id"])
            revision = int(definition["revision"])
            if trading_journal().strategy(strategy_id, revision) is None:
                save_strategy_definition(definition)


def create_strategy_assignment(payload: dict[str, Any]) -> dict[str, Any]:
    ensure_builtin_strategy_definition()
    permissions_payload = dict(payload.get("permissions") or {})
    permissions = StrategyPermissions(
        observe=bool(permissions_payload.get("observe", True)),
        enter=bool(permissions_payload.get("enter", False)),
        add=bool(permissions_payload.get("add", False)),
        reduce=bool(permissions_payload.get("reduce", True)),
        exit=bool(permissions_payload.get("exit", True)),
        reenter=bool(permissions_payload.get("reenter", False)),
    )
    now = datetime.now(ZoneInfo("UTC"))
    ticker = str(payload.get("ticker") or "").strip().upper()
    strategy_id = str(payload.get("strategy_id") or "").strip()
    strategy_revision = int(payload.get("strategy_revision") or 0)
    registration = strategy_executor(strategy_id, strategy_revision)
    state = dict(payload.get("state") or {})
    side = str(
        payload.get("side")
        or state.get("campaign_side")
        or dict(dict(payload.get("parameters") or {}).get("strategy_behavior") or {}).get("side")
        or "long"
    ).lower()
    state.update(
        campaign_state(
            campaign_id=str(payload.get("campaign_id") or state.get("campaign_id") or f"{strategy_id}:{ticker}:{side}"),
            deployment_id=str(payload.get("deployment_id") or state.get("campaign_deployment_id") or ""),
            profile_id=str(payload.get("profile_id") or state.get("campaign_profile_id") or ""),
            book_id=str(payload.get("book_id") or state.get("campaign_book_id") or "default"),
            universe_id=str(payload.get("universe_id") or state.get("campaign_universe_id") or ""),
            side=side,
        )
    )
    if isinstance(payload.get("campaign_policy"), dict):
        state["campaign_policy"] = dict(payload["campaign_policy"])
    assignment = StrategyAssignment(
        assignment_id=str(payload.get("assignment_id") or uuid4()),
        strategy_id=strategy_id,
        strategy_revision=strategy_revision,
        account_id=str(payload.get("account_id") or "").strip(),
        ticker=ticker,
        conid=int(payload.get("conid") or 0),
        status=AssignmentStatus(str(payload.get("status") or AssignmentStatus.WATCHING)),
        permissions=permissions,
        parameters=registration.parameter_resolver(
            dict(payload.get("parameters") or {})
        ),
        state=state,
        source=str(payload.get("source") or "order_entry"),
        created_at=now,
        updated_at=now,
    )
    active_ticker_assignments = list_strategy_assignments(
        ticker=ticker,
        active_only=True,
    )
    if any(
        str(row.get("account_id") or "") == assignment.account_id
        and str(row.get("assignment_id") or "") != assignment.assignment_id
        for row in active_ticker_assignments
    ):
        raise ValueError(
            f"{ticker} already has an active campaign leg for {assignment.account_id}; "
            "opposing sides require separate non-netting accounts"
        )
    StrategyCampaignOrchestrator([*active_ticker_assignments, assignment])
    saved = trading_journal().save_strategy_assignment(assignment.payload())
    trading_journal().append(
        run_id=assignment.assignment_id,
        category="strategy",
        entity_type="strategy_assignment",
        entity_id=assignment.assignment_id,
        account_id=assignment.account_id,
        event_time=now,
        payload={
            "event": "assignment_created",
            "assignment_id": assignment.assignment_id,
            "strategy_id": assignment.strategy_id,
            "strategy_revision": assignment.strategy_revision,
            "ticker": assignment.ticker,
            "status": assignment.status.value,
            "permissions": assignment.permissions.payload(),
        },
    )
    return saved


def list_strategy_assignments(*, account_id: str = "", ticker: str = "", active_only: bool = False) -> list[dict[str, Any]]:
    return trading_journal().strategy_assignments(account_id=account_id, ticker=ticker, active_only=active_only)


def command_strategy_assignment(assignment_id: str, command: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
    row = trading_journal().strategy_assignment(assignment_id)
    if row is None:
        raise KeyError(assignment_id)
    command = command.strip().lower()
    status_map = {
        "arm": AssignmentStatus.WATCHING,
        "resume": AssignmentStatus.WATCHING,
        "pause": AssignmentStatus.PAUSED,
        "disable": AssignmentStatus.DISABLED,
        "complete": AssignmentStatus.COMPLETED,
    }
    if command not in {
        *status_map,
        "disable_after_exit",
        "request_entry",
        "force_entry",
        "request_exit",
        "exit_and_stop",
        "exit_keep_watching",
    }:
        raise ValueError(f"Unsupported strategy assignment command: {command}")
    state = dict(row.get("state") or {})
    status = status_map.get(command, AssignmentStatus(str(row["status"])))
    if command == "disable_after_exit":
        state["disable_after_exit"] = True
    elif command == "request_entry":
        state["manual_entry_requested"] = True
    elif command == "force_entry":
        state["force_entry_requested"] = True
    elif command in {"request_exit", "exit_and_stop", "exit_keep_watching"}:
        state["manual_exit_requested"] = True
        state["disable_after_exit"] = command == "exit_and_stop"
    row = {
        **row,
        "status": status.value,
        "state": state,
        "updated_at": datetime.now(ZoneInfo("UTC")).isoformat(),
    }
    saved = trading_journal().save_strategy_assignment(row)
    trading_journal().append(
        run_id=assignment_id,
        category="strategy",
        entity_type="strategy_assignment_command",
        entity_id=assignment_id,
        account_id=str(row["account_id"]),
        payload={
            "event": "assignment_command",
            "command": command,
            "assignment_id": assignment_id,
            "strategy_id": row["strategy_id"],
            "strategy_revision": row["strategy_revision"],
            "ticker": row["ticker"],
            "status": row["status"],
            "detail": dict(payload or {}),
        },
    )
    return saved


def evaluate_strategy_assignment(assignment_id: str, payload: dict[str, Any]) -> dict[str, Any]:
    row = trading_journal().strategy_assignment(assignment_id)
    if row is None:
        raise KeyError(assignment_id)
    assignment = _assignment_from_row(row)
    state = dict(assignment.state)
    observation_payload = dict(payload)
    observation_payload["ticker"] = assignment.ticker
    observation_payload["manual_entry_request"] = bool(
        observation_payload.get("manual_entry_request") or state.pop("manual_entry_requested", False)
    )
    observation_payload["force_entry"] = bool(
        observation_payload.get("force_entry") or state.pop("force_entry_requested", False)
    )
    evaluation_events = list(observation_payload.get("evaluation_events") or ["indicator_update"])
    if observation_payload["manual_entry_request"] or observation_payload["force_entry"]:
        evaluation_events.append("manual")
    observation_payload["evaluation_events"] = tuple(dict.fromkeys(evaluation_events))
    observation_payload["changed_source_ids"] = tuple(
        observation_payload.get("changed_source_ids") or ()
    )
    observation_payload["observed_at"] = _aware_datetime(observation_payload.get("observed_at"))
    assignment = replace(assignment, state=state)
    observation = StrategyObservation(**observation_payload)
    result = strategy_executor(
        assignment.strategy_id, assignment.strategy_revision
    ).assignment_evaluator(assignment, observation)
    now = datetime.now(ZoneInfo("UTC"))
    updated = replace(assignment, state=result.state, status=result.status, updated_at=now)
    trading_journal().save_strategy_assignment(updated.payload())
    trading_journal().append(
        run_id=assignment.assignment_id,
        category="strategy",
        entity_type="strategy_evaluation",
        entity_id=result.evaluation_payload["event_id"],
        account_id=assignment.account_id,
        event_time=observation.observed_at,
        payload={
            **result.evaluation_payload,
            "event": "strategy_evaluated",
            "observation": observation.payload(),
        },
    )
    order_plan: list[dict[str, Any]] = []
    if result.evaluation.intents:
        instrument = InstrumentContract(
            instrument_id=f"ibkr:{assignment.conid}",
            conid=assignment.conid,
            symbol=assignment.ticker,
            security_type="STK",
            currency=str(payload.get("currency") or "USD"),
            exchange=str(payload.get("exchange") or "SMART"),
        )
        planner = IbkrStrategyOrderPlanner()
        for intent in result.evaluation.intents:
            plan = planner.plan(
                account_id=assignment.account_id,
                instrument=instrument,
                intent=intent,
                strategy_id=assignment.strategy_id,
                strategy_revision=assignment.strategy_revision,
                limit_offset_bps=float(assignment.parameters["execution"]["limit_offset_bps"]),
            )
            order_plan.extend(order.to_cpapi() for order in plan.orders)
    return {
        "assignment": updated.payload(),
        "evaluation": result.evaluation_payload,
        "intents": [intent.payload() for intent in result.evaluation.intents],
        "order_plan": order_plan,
        "orders_submitted": False,
        "submission_note": "The shared runtime submits this plan only after risk validation; this evaluation endpoint never places live orders.",
    }


def strategy_canvas_payload(*, as_of: datetime, ticker: str) -> dict[str, Any]:
    ensure_builtin_strategy_definition()
    assignments = trading_journal().strategy_assignments(ticker=ticker)
    active = next(
        (
            row
            for row in assignments
            if row["status"] not in {"disabled", "completed", "error"}
            and strategy_executor_optional(
                str(row.get("strategy_id") or ""),
                int(row.get("strategy_revision") or 0),
            )
            is not None
        ),
        None,
    )
    definition = (
        get_strategy_definition(
            str(active["strategy_id"]), int(active["strategy_revision"])
        )
        if active is not None
        else next(
            row
            for row in list_strategy_definitions()
            if bool(dict(row.get("executor") or {}).get("installed"))
        )
    )
    records = trading_journal().strategy_records(
        ticker=ticker,
        strategy_id=str(definition["strategy_id"]),
        as_of=as_of,
        limit=5000,
    )
    decisions = []
    for record in records:
        if record.entity_type not in {"strategy_evaluation", "signal"}:
            continue
        if record.payload.get("action") not in {"enter_long", "add_long", "reduce_long", "take_profit", "exit", "hold", "wait"}:
            continue
        metadata = dict(record.payload.get("metadata") or {})
        decisions.append(
            {
                **record.payload,
                "effective_at": record.payload.get("effective_at") or record.payload.get("event_time") or record.event_time.isoformat(),
                "event_id": record.payload.get("event_id") or record.payload.get("signal_id") or record.record_id,
                "reference_price": record.payload.get("reference_price") or metadata.get("reference_price"),
            }
        )
    order_management = [
        {
            **record.payload,
            "category": record.category,
            "entity_type": record.entity_type,
            "entity_id": record.entity_id,
            "event_time": record.event_time.isoformat(),
            "recorded_at": record.recorded_at.isoformat(),
        }
        for record in trading_journal().order_management_records(
            ticker=ticker,
            strategy_id=str(definition["strategy_id"]),
            as_of=as_of,
            limit=1000,
        )
    ]
    return {
        "fixture": False,
        "strategy_id": definition["strategy_id"],
        "name": definition["name"],
        "revision": definition["revision"],
        "automatic": definition["automatic"],
        "state": str(active["status"]) if active else "not_assigned",
        "definition": definition,
        "assignment": active,
        "assignments": assignments,
        "signals": decisions,
        "order_management": order_management,
        "taxonomy": definition.get("taxonomy"),
        "historical_source": "saved_strategy_journal_only",
    }


def strategy_activity_payload(
    *,
    as_of: datetime | None = None,
    strategy_id: str = "",
    run_id: str = "",
    ticker: str = "",
    event_type: str = "",
    limit: int = 500,
) -> dict[str, Any]:
    """Project the durable strategy journal into an operator-facing event list."""
    requested_limit = max(1, min(int(limit), 5000))
    records = trading_journal().strategy_activity_records(
        strategy_id=strategy_id.strip(),
        run_id=run_id.strip(),
        ticker=ticker.strip().upper(),
        as_of=as_of,
        limit=50_000 if event_type else requested_limit,
    )
    rows: list[dict[str, Any]] = []
    for record in records:
        payload = dict(record.payload)
        row_type = _strategy_activity_event_type(record.entity_type)
        if event_type and row_type != event_type:
            continue
        metadata = dict(payload.get("metadata") or {})
        action = str(
            payload.get("action")
            or payload.get("event")
            or payload.get("status")
            or payload.get("state")
            or "observed"
        )
        rows.append(
            {
                "record_id": record.record_id,
                "event_time": record.event_time.isoformat(),
                "recorded_at": record.recorded_at.isoformat(),
                "run_id": record.run_id,
                "sequence": record.sequence,
                "strategy_id": str(payload.get("strategy_id") or ""),
                "strategy_revision": payload.get("strategy_revision"),
                "account_id": record.account_id,
                "ticker": str(payload.get("ticker") or "").upper(),
                "event_type": row_type,
                "action": action,
                "state": str(payload.get("status") or payload.get("state") or ""),
                "reason": str(
                    payload.get("reason")
                    or payload.get("evidence")
                    or metadata.get("reason")
                    or metadata.get("evidence")
                    or ""
                ),
                "score": payload.get("score"),
                "confidence": payload.get("confidence"),
                "reference_price": payload.get("reference_price") or metadata.get("reference_price"),
                "entity_id": record.entity_id,
                "source": str(payload.get("source") or record.entity_type),
            }
        )
        if len(rows) >= requested_limit:
            break
    strategies = sorted({str(row["strategy_id"]) for row in rows if row["strategy_id"]})
    runs = sorted({str(row["run_id"]) for row in rows if row["run_id"]})
    tickers = sorted({str(row["ticker"]) for row in rows if row["ticker"]})
    return {
        "schema_version": 1,
        "as_of": (as_of or datetime.now(ZoneInfo("UTC"))).isoformat(),
        "source": "trading_journal",
        "complete": True,
        "rows": rows,
        "catalog": {
            "strategies": strategies,
            "runs": runs,
            "tickers": tickers,
            "event_types": ["signal", "decision", "campaign_state"],
        },
    }


def _strategy_activity_event_type(entity_type: str) -> str:
    if entity_type == "signal":
        return "signal"
    if entity_type == "strategy_assignment_state":
        return "campaign_state"
    return "decision"


def _assignment_from_row(row: dict[str, Any]) -> StrategyAssignment:
    return StrategyAssignment(
        assignment_id=str(row["assignment_id"]),
        strategy_id=str(row["strategy_id"]),
        strategy_revision=int(row["strategy_revision"]),
        account_id=str(row["account_id"]),
        ticker=str(row["ticker"]),
        conid=int(row["conid"]),
        status=AssignmentStatus(str(row["status"])),
        permissions=StrategyPermissions(**dict(row.get("permissions") or {})),
        parameters=dict(row.get("parameters") or {}),
        state=dict(row.get("state") or {}),
        source=str(row.get("source") or "order_entry"),
        created_at=_aware_datetime(row.get("created_at")),
        updated_at=_aware_datetime(row.get("updated_at")),
    )


def _aware_datetime(value: Any) -> datetime:
    if value in (None, ""):
        return datetime.now(ZoneInfo("UTC"))
    parsed = value if isinstance(value, datetime) else datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("Strategy timestamps must include a timezone")
    return parsed


def _strategy_definition_payload(value: dict[str, Any]) -> dict[str, Any]:
    if not value:
        return value
    config = dict(value.get("config") or {})
    taxonomy_payload = config.get("taxonomy")
    taxonomy = StrategyTaxonomy.from_payload(taxonomy_payload).payload() if taxonomy_payload else None
    return {**value, "config": config, "taxonomy": taxonomy}


def get_trade_annotation(episode_id: str) -> dict[str, Any]:
    return trading_journal().trade_annotation(episode_id) or {
        "episode_id": episode_id,
        "note": "",
        "tags": [],
        "review_status": "unreviewed",
        "setup_override": "",
        "updated_at": None,
    }


def save_trade_annotation(episode_id: str, payload: dict[str, Any]) -> dict[str, Any]:
    normalized_id = str(episode_id or "").strip()
    if not normalized_id:
        raise ValueError("episode_id is required")
    return trading_journal().save_trade_annotation(
        normalized_id,
        note=str(payload.get("note") or "").strip(),
        tags=payload.get("tags") or (),
        review_status=str(payload.get("review_status") or "unreviewed"),
        setup_override=str(payload.get("setup_override") or "").strip(),
    )


def historical_gateway_snapshot() -> dict[str, Any]:
    base_url = historical_gateway_base_url()
    try:
        payload = _historical_gateway_get("/health", {}, timeout=3)
        ready = (
            payload.get("service") == "qmd_history_gateway"
            and payload.get("host_role") == "historical"
            and payload.get("status") == "ready"
            and payload.get("running") is True
        )
        return {
            "base_url": base_url,
            "online": True,
            "ready": ready,
            "status": "ready" if ready else "degraded",
            "health": payload,
        }
    except Exception as exc:
        return {
            "base_url": base_url,
            "online": False,
            "ready": False,
            "status": "offline",
            "error": str(exc),
            "health": {},
        }


def historical_window_preview(
    *,
    mode: str,
    anchor_date: date,
    session_count: int,
    replay_end_date: date | None,
) -> dict[str, Any]:
    resolved_mode = RunMode(mode)
    # Replay is deliberately a single exchange day. Keep this invariant in the
    # backend so an old or external client cannot silently create a multi-day run.
    if resolved_mode == RunMode.REPLAY:
        replay_end_date = anchor_date
    window = historical_run_window(
        resolved_mode,
        anchor_date,
        session_count=session_count,
        replay_end_date=replay_end_date,
    )
    return {
        "mode": resolved_mode.value,
        "anchor_date": anchor_date.isoformat(),
        "anchor_semantics": "inclusive" if resolved_mode == RunMode.REPLAY else "exclusive",
        "start": window.start.isoformat(),
        "end": window.end.isoformat(),
        "sessions": [session.isoformat() for session in window.sessions],
        "session_count": len(window.sessions),
        "source": "qmd_history_gateway",
        "source_url": historical_gateway_base_url(),
        "broker": "simulated_ibkr",
    }


def historical_preflight(
    *,
    mode: str,
    anchor_date: date,
    session_count: int,
) -> dict[str, Any]:
    window = historical_window_preview(
        mode=mode,
        anchor_date=anchor_date,
        session_count=session_count,
        replay_end_date=anchor_date if mode == RunMode.REPLAY.value else None,
    )
    gateway = historical_gateway_snapshot()
    strategies = list_strategy_definitions(latest_only=True)
    automatic_strategies = [row for row in strategies if row.get("automatic") and row.get("enabled")]
    checks: list[dict[str, Any]] = [
        _preflight_check(
            "historical_source",
            "Historical market source",
            "ready" if gateway.get("ready") else "error",
            "QMD History answered with its historical role and canonical event source."
            if gateway.get("ready")
            else "QMD History did not answer with a ready historical identity.",
            gateway.get("health", {}).get("source") or gateway.get("error") or gateway.get("base_url", ""),
            required=True,
        ),
        _preflight_check(
            "session_window",
            "Exchange-day window",
            "ready",
            "One inclusive exchange day, 04:00-20:00 New York."
            if mode == RunMode.REPLAY.value
            else f"{window['session_count']} sessions strictly before the anchor date.",
            f"{window['start']} -> {window['end']}",
            required=True,
        ),
    ]

    coverage: dict[str, Any] = {}
    data_error = ""
    if gateway.get("ready"):
        try:
            payload = _historical_gateway_get(
                "/coverage",
                {"start": window["start"], "end": window["end"]},
                timeout=30,
            )
            if isinstance(payload, dict):
                coverage = payload
            if int(coverage.get("event_count") or 0) <= 0:
                data_error = "No canonical market events were found in the resolved session window."
        except Exception as exc:
            data_error = str(exc)
    elif gateway.get("error"):
        data_error = str(gateway["error"])

    event_count = int(coverage.get("event_count") or 0)
    ticker_count = int(coverage.get("ticker_count") or 0)
    market_ready = bool(event_count > 0 and ticker_count > 0 and not data_error)
    checks.append(
        _preflight_check(
            "market_data",
            "Canonical event coverage",
            "ready" if market_ready else "error",
            (
                f"{event_count:,} events across {ticker_count:,} symbols are recorded for the selected exchange day set."
                if market_ready
                else data_error or "Historical market data could not be verified."
            ),
            (
                f"Coverage: {coverage.get('coverage_table')}; events: {', '.join(coverage.get('source_tables') or [])}"
                if market_ready
                else "No usable sample evidence."
            ),
            required=True,
        )
    )
    checks.append(
        _preflight_check(
            "strategy_authority",
            "Automatic strategy revisions",
            "ready" if automatic_strategies else "blocked",
            f"{len(automatic_strategies)} enabled automatic revision(s) are available."
            if automatic_strategies
            else "No enabled automatic strategy revision exists in the central trading authority.",
            "Required for strategy execution and every backtest; optional for market-only replay.",
            required=mode != RunMode.REPLAY.value,
        )
    )
    checks.append(
        _preflight_check(
            "run_controller",
            "Trading run controller",
            "ready",
            "Replay and Backtest use the shared strategy, Portfolio, OMS, and simulated-broker runtime.",
            "The historical controller preserves one journaled runtime across the selected event-time window.",
            required=mode != RunMode.REPLAY.value,
        )
    )
    strategy_run_ready = bool(
        market_ready
        and (mode == RunMode.REPLAY.value or automatic_strategies)
    )
    return {
        "mode": mode,
        "window": window,
        "gateway": gateway,
        "checks": checks,
        "market_ready": market_ready,
        "strategy_run_ready": strategy_run_ready,
        "automatic_strategy_count": len(automatic_strategies),
        "coverage": coverage,
    }


def historical_bar_chunk(
    *,
    anchor_date: date,
    ticker: str,
    timeframe: str,
    offset_minutes: int,
    window_minutes: int = HISTORICAL_CHUNK_MINUTES,
) -> dict[str, Any]:
    resolved_ticker = _historical_ticker(ticker)
    resolved_timeframe = _historical_timeframe(timeframe)
    if not 0 <= offset_minutes < 960:
        raise ValueError("offset_minutes must be between 0 and 959")
    if not 1 <= window_minutes <= 30:
        raise ValueError("window_minutes must be between 1 and 30")
    window = historical_window_preview(
        mode=RunMode.REPLAY.value,
        anchor_date=anchor_date,
        session_count=1,
        replay_end_date=anchor_date,
    )
    day_start = datetime.fromisoformat(window["start"])
    day_end = datetime.fromisoformat(window["end"])
    chunk_start = day_start + timedelta(minutes=offset_minutes)
    chunk_end = min(chunk_start + timedelta(minutes=window_minutes), day_end)
    snapshot = _historical_gateway_get(
        f"/snapshot/bars/{urllib.parse.quote(resolved_ticker)}",
        {
            "start": chunk_start.isoformat(),
            "end": chunk_end.isoformat(),
            "timeframe": resolved_timeframe,
            "limit": 5_000,
            "event_limit": 1_000_000,
        },
        timeout=45,
    )
    bars = list(snapshot.get("history") or []) if isinstance(snapshot, dict) else []
    if isinstance(snapshot, dict) and snapshot.get("current"):
        bars.append(dict(snapshot["current"]))
    indicators = list(snapshot.get("indicators") or []) if isinstance(snapshot, dict) else []
    structure_events = list(snapshot.get("structure_events") or []) if isinstance(snapshot, dict) else []
    structure_level_history = list(snapshot.get("structure_level_history") or []) if isinstance(snapshot, dict) else []
    return {
        "ticker": resolved_ticker,
        "timeframe": resolved_timeframe,
        "session_date": anchor_date.isoformat(),
        "offset_minutes": offset_minutes,
        "next_offset_minutes": min(960, offset_minutes + window_minutes),
        "complete": chunk_end >= day_end,
        "start": chunk_start.isoformat(),
        "end": chunk_end.isoformat(),
        "bars": bars,
        "indicators": indicators,
        "structure_events": structure_events,
        "structure_level_history": structure_level_history,
        "bar_count": len(bars),
        "source": "qmd_history_gateway",
    }


def historical_latest_coverage() -> dict[str, Any]:
    payload = _historical_gateway_get("/coverage/latest", {}, timeout=15)
    if not isinstance(payload, dict):
        raise RuntimeError("QMD History latest coverage response must be an object")
    return payload


def historical_scanner_derived_snapshot(as_of: datetime) -> dict[str, Any]:
    """Build or reuse QMD's causal full-market derived Scanner projection."""
    if as_of.tzinfo is None:
        raise ValueError("historical Scanner as_of must include a timezone")
    market_date = as_of.astimezone(ZoneInfo("America/New_York")).date()
    window = historical_window_preview(
        mode=RunMode.REPLAY.value,
        anchor_date=market_date,
        session_count=1,
        replay_end_date=market_date,
    )
    payload = qmd_product_request(
        QmdProductRequest(
            "scanner",
            authority="history",
            as_of=as_of.isoformat(),
            end=window["end"],
            start=window["start"],
            timeout_seconds=float(os.environ.get("QMD_HISTORY_SCANNER_TIMEOUT_SECONDS", "1800")),
        ),
        history_get=_historical_gateway_get,
    ).payload
    if not isinstance(payload, dict):
        raise RuntimeError("QMD History Scanner derived response must be an object")
    return payload


def _is_recent_live_chart_session(session_date: date) -> bool:
    """Assign only the current and prior exchange session to QMD Live."""
    today = datetime.now(ZoneInfo("America/New_York")).date()
    sessions = market_sessions(today - timedelta(days=14), today)
    recent = set(sessions[-2:])
    # Before the first calendar session is published for a new environment,
    # the current date remains a valid live-table query and may return empty.
    recent.add(today)
    return session_date in recent


def _can_use_recent_live_chart_session(session_date: date, as_of: str | None) -> bool:
    """Use q_live only when its latest-per-bar state cannot leak future events.

    Completed recent sessions are immutable in q_live, and a genuinely current
    wall-clock request may use the developing session. Historical intraday
    clocks must use QMD History because q_live retains the latest revision of a
    bar rather than every point-in-time revision inside that bar.
    """

    if not as_of:
        return _is_recent_live_chart_session(session_date)
    clock = datetime.fromisoformat(as_of.replace("Z", "+00:00"))
    if clock.tzinfo is None:
        raise ValueError("as_of must include a timezone")
    local_clock = clock.astimezone(ZoneInfo("America/New_York"))
    # A Canvas clock anchored inside this session can safely probe q_live even
    # when the wall clock has advanced beyond its short retention window.  The
    # query below is bounded strictly before the requested clock, so only
    # completed bars are exposed; an empty recent-table result falls back to
    # QMD History's archive/recent source plan.
    if local_clock.date() == session_date:
        return True
    if not _is_recent_live_chart_session(session_date):
        return False
    if local_clock.date() > session_date:
        return True
    if local_clock.date() < session_date:
        return False
    session_end = datetime.combine(
        session_date,
        time(20, 0),
        tzinfo=ZoneInfo("America/New_York"),
    )
    if local_clock >= session_end:
        return True
    return clock >= datetime.now(ZoneInfo("UTC")) - timedelta(minutes=5)


_CHART_TIMEFRAME_MICROSECONDS = {
    "100ms": 100_000,
    "1s": 1_000_000,
    "5s": 5_000_000,
    "10s": 10_000_000,
    "30s": 30_000_000,
    "1m": 60_000_000,
    "5m": 300_000_000,
    "1h": 3_600_000_000,
}

_HISTORICAL_CHART_PAGE_MAX_SECONDS = {
    "100ms": 15 * 60,
    "1s": 30 * 60,
    "5s": 60 * 60,
    "10s": 2 * 60 * 60,
    "30s": 2 * 60 * 60,
    "1m": 2 * 60 * 60,
    "5m": 4 * 60 * 60,
    "1h": 16 * 60 * 60,
}


def _bounded_historical_chart_window(
    *,
    session_start: datetime,
    session_end: datetime,
    as_of: datetime,
    before_bar: str | None,
    timeframe: str,
    row_limit: int,
) -> tuple[datetime, datetime, bool]:
    page_end = min(as_of, session_end)
    if before_bar:
        parsed_before = datetime.fromisoformat(before_bar.replace("Z", "+00:00"))
        if parsed_before.tzinfo is None:
            raise ValueError("before_bar must include a timezone")
        page_end = min(page_end, parsed_before)
    resolution_us = _CHART_TIMEFRAME_MICROSECONDS.get(timeframe)
    if resolution_us is None:
        return session_start, page_end, False
    # The extra 25% supplies indicator warm-up without reconstructing the full
    # raw-event session before the first chart paint.
    span_us = resolution_us * max(1, min(row_limit, 5_000)) * 5 // 4
    span_us = min(
        span_us,
        _HISTORICAL_CHART_PAGE_MAX_SECONDS[timeframe] * 1_000_000,
    )
    page_start = max(session_start, page_end - timedelta(microseconds=span_us))
    return page_start, page_end, page_start > session_start


def _recent_live_bar_history(
    *,
    ticker: str,
    timeframe: str,
    session_date: date,
    as_of: str | None,
    before_bar: str | None,
    row_limit: int,
    stage: str,
) -> dict[str, Any] | None:
    before_candidates: list[datetime] = []
    if as_of:
        as_of_clock = datetime.fromisoformat(as_of.replace("Z", "+00:00"))
        if as_of_clock.tzinfo is None:
            raise ValueError("as_of must include a timezone")
        before_candidates.append(as_of_clock)
    if before_bar:
        cursor = datetime.fromisoformat(before_bar.replace("Z", "+00:00"))
        if cursor.tzinfo is None:
            raise ValueError("before_bar must include a timezone")
        before_candidates.append(cursor)
    before_event_timestamp_us = (
        int(min(before_candidates).timestamp() * 1_000_000)
        if before_candidates
        else None
    )
    try:
        payload = qmd_intraday_bar_history(
            ticker,
            timeframe=timeframe,
            start_date=session_date.isoformat(),
            end_date=session_date.isoformat(),
            before_event_timestamp_us=before_event_timestamp_us,
            row_limit=row_limit,
        )
    except QmdServiceError:
        return None
    bars = [dict(row) for row in payload.get("bars") or [] if isinstance(row, dict)]
    today = datetime.now(ZoneInfo("America/New_York")).date()
    if not bars and session_date != today:
        return None
    bars.sort(key=_bar_start_sort_key)
    has_more_in_session = bool(payload.get("has_more"))
    # Do not block the fast q_live page on a second authority. The cursor is a
    # lazy handoff token: only a subsequent "load earlier" action asks QMD
    # History to resolve the closest older covered session.
    previous_session_before = "" if has_more_in_session else session_date.isoformat()
    return {
        "ticker": ticker,
        "timeframe": timeframe,
        "history": bars,
        "indicators": [],
        "market_signal_events": [],
        "structure_events": [],
        "structure_level_history": [],
        "indicator_provenance": {},
        "indicators_available": False,
        "earliest_session_date": session_date.isoformat() if bars else "",
        "has_more": has_more_in_session or bool(previous_session_before),
        "has_more_in_session": has_more_in_session,
        "next_before": str(bars[0].get("bar_start") or "") if has_more_in_session and bars else "",
        "previous_session_before": previous_session_before,
        "as_of": as_of or datetime.now(tz=ZoneInfo("UTC")).isoformat(),
        "source": "qmd_live_intraday_family_bars_v2",
        "stage": stage,
        "authority": "live",
    }


def historical_bar_history_before(
    *,
    before: date,
    ticker: str,
    timeframe: str,
    row_limit: int = 20_000,
    session_date: date | None = None,
    as_of: str | None = None,
    before_bar: str | None = None,
    indicator_columns: list[str] | None = None,
    stage: str = "full",
) -> dict[str, Any]:
    resolved_ticker = _historical_ticker(ticker)
    resolved_timeframe = _historical_timeframe(timeframe)
    if stage not in {"bars", "full"}:
        raise ValueError("chart stage must be bars or full")
    if resolved_timeframe in MACRO_CHART_TIMEFRAMES:
        return historical_macro_bar_history(
            ticker=resolved_ticker,
            timeframe=resolved_timeframe,
            session_date=session_date or before,
            as_of=as_of,
            before_bar=before_bar,
        )
    requested_session = session_date or before
    # Recent durable bars are the fast first-paint authority.  The full stage
    # still goes through QMD History so requested indicators and structure are
    # calculated from the exact causal event window in the background.
    if stage == "bars" and _can_use_recent_live_chart_session(requested_session, as_of):
        live_payload = _recent_live_bar_history(
            ticker=resolved_ticker,
            timeframe=resolved_timeframe,
            session_date=requested_session,
            as_of=as_of,
            before_bar=before_bar,
            row_limit=row_limit,
            stage=stage,
        )
        if live_payload is not None:
            return live_payload
    coverage = None
    if session_date is None:
        coverage = _historical_gateway_get(
            "/coverage/latest",
            {"before": before.isoformat()},
            timeout=15,
        )
    session_date_text = session_date.isoformat() if session_date else str(coverage.get("session_date") or "") if isinstance(coverage, dict) else ""
    if not session_date_text:
        return {
            "ticker": resolved_ticker,
            "timeframe": resolved_timeframe,
            "history": [],
            "indicators": [],
            "market_signal_events": [],
            "structure_events": [],
            "structure_level_history": [],
            "indicator_provenance": {},
            "earliest_session_date": "",
            "has_more": False,
            "source": "qmd_history_gateway",
            "stage": stage,
        }
    resolved_session_date = date.fromisoformat(session_date_text)
    window = historical_window_preview(
        mode=RunMode.REPLAY.value,
        anchor_date=resolved_session_date,
        session_count=1,
        replay_end_date=resolved_session_date,
    )
    window_start = datetime.fromisoformat(window["start"])
    window_end = datetime.fromisoformat(window["end"])
    resolved_as_of = datetime.fromisoformat(as_of) if as_of else window_end
    if resolved_as_of.tzinfo is None:
        raise ValueError("as_of must include a timezone")
    resolved_as_of = max(window_start, min(resolved_as_of, window_end))
    if resolved_as_of <= window_start:
        previous = _historical_gateway_get(
            "/coverage/latest",
            {"before": resolved_session_date.isoformat()},
            timeout=15,
        )
        previous_session_before = (
            resolved_session_date.isoformat()
            if isinstance(previous, dict) and previous.get("session_date")
            else ""
        )
        return {
            "ticker": resolved_ticker,
            "timeframe": resolved_timeframe,
            "history": [],
            "indicators": [],
            "market_signal_events": [],
            "structure_events": [],
            "structure_level_history": [],
            "indicator_provenance": {},
            "indicators_available": resolved_timeframe in ENRICHED_QMD_TIMEFRAMES,
            "earliest_session_date": "",
            "has_more": bool(previous_session_before),
            "has_more_in_session": False,
            "next_before": "",
            "previous_session_before": previous_session_before,
            "as_of": resolved_as_of.isoformat(),
            "source": "qmd_history_gateway",
            "stage": stage,
        }
    page_start, page_end, has_earlier_window = _bounded_historical_chart_window(
        session_start=window_start,
        session_end=window_end,
        as_of=resolved_as_of,
        before_bar=before_bar,
        timeframe=resolved_timeframe,
        row_limit=row_limit,
    )
    snapshot = qmd_product_request(
        QmdProductRequest(
            "chart",
            authority="history",
            ticker=resolved_ticker,
            timeframe=resolved_timeframe,
            start=page_start.isoformat(),
            end=page_end.isoformat(),
            as_of=page_end.isoformat(),
            before=None,
            indicator_columns=tuple(indicator_columns or ()),
            stage=stage,
            limit=row_limit,
            timeout_seconds=90,
        ),
        history_get=_historical_gateway_get,
    ).payload
    bars = list(snapshot.get("bars") or []) if isinstance(snapshot, dict) else []
    indicators = list(snapshot.get("indicators") or []) if isinstance(snapshot, dict) else []
    market_signal_events = list(snapshot.get("market_signal_events") or []) if isinstance(snapshot, dict) else []
    structure_events = list(snapshot.get("structure_events") or []) if isinstance(snapshot, dict) else []
    structure_level_history = list(snapshot.get("structure_level_history") or []) if isinstance(snapshot, dict) else []
    bars.sort(key=_bar_start_sort_key)
    indicators.sort(key=_bar_start_sort_key)
    snapshot_has_more = bool(snapshot.get("has_more")) if isinstance(snapshot, dict) else False
    has_more_in_session = snapshot_has_more or has_earlier_window
    previous_session_before = ""
    if not has_more_in_session:
        previous = _historical_gateway_get(
            "/coverage/latest",
            {"before": resolved_session_date.isoformat()},
            timeout=15,
        )
        if isinstance(previous, dict) and previous.get("session_date"):
            previous_session_before = resolved_session_date.isoformat()
    return {
        "ticker": resolved_ticker,
        "timeframe": resolved_timeframe,
        "history": bars,
        "indicators": indicators,
        "market_signal_events": market_signal_events,
        "structure_events": structure_events,
        "structure_level_history": structure_level_history,
        "indicator_provenance": dict(snapshot.get("indicator_provenance") or {}) if isinstance(snapshot, dict) else {},
        "indicators_available": bool(snapshot.get("indicators_available")) if isinstance(snapshot, dict) else False,
        "earliest_session_date": session_date_text if bars else "",
        "has_more": has_more_in_session or bool(previous_session_before),
        "has_more_in_session": has_more_in_session,
        "next_before": (
            str(snapshot.get("next_before") or "")
            if snapshot_has_more and isinstance(snapshot, dict)
            else page_start.isoformat() if has_earlier_window else ""
        ),
        "previous_session_before": previous_session_before,
        "as_of": resolved_as_of.isoformat(),
        "source": "qmd_history_gateway",
        "stage": stage,
    }


def historical_macro_bar_history(
    *,
    ticker: str,
    timeframe: str,
    session_date: date,
    as_of: str | None,
    before_bar: str | None = None,
) -> dict[str, Any]:
    resolved_as_of = datetime.fromisoformat(as_of) if as_of else datetime.combine(session_date, time(20, 0), tzinfo=ZoneInfo("America/New_York"))
    if resolved_as_of.tzinfo is None:
        raise ValueError("as_of must include a timezone")
    page_end = resolved_as_of
    if before_bar:
        page_end = datetime.fromisoformat(before_bar.replace("Z", "+00:00"))
        if page_end.tzinfo is None:
            raise ValueError("before_bar must include a timezone")
        page_end = min(page_end, resolved_as_of)
    if timeframe == "1mo":
        # Include the current (possibly partial) month plus the preceding 35
        # months. Older three-year pages are requested only when the user pans.
        month_index = page_end.year * 12 + page_end.month - 1 - 35
        start = page_end.replace(
            year=month_index // 12,
            month=month_index % 12 + 1,
            day=1,
            hour=0,
            minute=0,
            second=0,
            microsecond=0,
        )
    elif timeframe == "1w":
        start = (page_end - timedelta(days=7 * 155)).replace(
            hour=0, minute=0, second=0, microsecond=0
        )
    elif timeframe == "1y":
        start = page_end.replace(
            year=max(1, page_end.year - 19),
            month=1,
            day=1,
            hour=0,
            minute=0,
            second=0,
            microsecond=0,
        )
    else:
        try:
            start = page_end.replace(year=max(1, page_end.year - 3), hour=0, minute=0, second=0, microsecond=0)
        except ValueError:
            # February 29 has no counterpart in non-leap years.
            start = page_end.replace(year=max(1, page_end.year - 3), day=28, hour=0, minute=0, second=0, microsecond=0)
    query_end = page_end if before_bar else resolved_as_of + timedelta(days=1)
    payload = qmd_product_request(
        QmdProductRequest(
            "chart",
            authority="history",
            ticker=ticker,
            timeframe=timeframe,
            start=start.isoformat(),
            end=query_end.isoformat(),
            as_of=resolved_as_of.isoformat(),
            limit=50_000,
            timeout_seconds=30,
        ),
        history_get=_historical_gateway_get,
    ).payload
    rows = [
        {
            "schema_version": 1,
            "session_date": row.get("session_date"),
            "timeframe": timeframe,
            "sym": ticker,
            "bar_start": row.get("bar_start"),
            "bar_end": row.get("bar_end"),
            "is_closed": bool(row.get("is_closed", True)),
            "open": row.get("open"),
            "high": row.get("high"),
            "low": row.get("low"),
            "close": row.get("close"),
            "volume": row.get("size_sum"),
            "vwap": None,
        }
        for row in (payload.get("bars") or [])
        if isinstance(row, dict) and row.get("bar_family") == "trade"
    ] if isinstance(payload, dict) else []
    rows.sort(key=_bar_start_sort_key)
    next_before = str(rows[0].get("bar_start") or "") if rows else ""
    return {
        "ticker": ticker,
        "timeframe": timeframe,
        "history": rows,
        "indicators": [],
        "market_signal_events": [],
        "structure_events": [],
        "structure_level_history": [],
        "indicators_available": False,
        "earliest_session_date": str(rows[0].get("session_date") or "") if rows else "",
        "has_more": bool(next_before),
        "has_more_in_session": False,
        "next_before": next_before,
        "previous_session_before": "",
        "as_of": resolved_as_of.isoformat(),
        "source": payload.get("source", "qmd_history_gateway") if isinstance(payload, dict) else "qmd_history_gateway",
    }


def _bar_start_sort_key(row: dict[str, Any]) -> float:
    value = row.get("bar_start")
    if not isinstance(value, str) or not value:
        return float("inf")
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()
    except ValueError:
        return float("inf")


def historical_day_coverage(anchor_date: date) -> dict[str, Any]:
    window = historical_window_preview(
        mode=RunMode.REPLAY.value,
        anchor_date=anchor_date,
        session_count=1,
        replay_end_date=anchor_date,
    )
    payload = _historical_gateway_get(
        "/coverage",
        {"start": window["start"], "end": window["end"]},
        timeout=15,
    )
    if not isinstance(payload, dict):
        raise RuntimeError("QMD History day coverage response must be an object")
    return payload


def historical_compact_events(
    ticker: str,
    *,
    start: str,
    end: str,
    row_limit: int = 500,
) -> list[dict[str, Any]]:
    resolved_ticker = _historical_ticker(ticker)
    payload = qmd_product_request(
        QmdProductRequest(
            "compact_events",
            authority="history",
            ticker=resolved_ticker,
            start=start,
            end=end,
            limit=row_limit,
            tail=True,
            timeout_seconds=15,
        ),
        history_get=_historical_gateway_get,
    ).payload
    if not isinstance(payload, list):
        raise RuntimeError("QMD History compact-event response must be an array")
    return [row for row in payload if isinstance(row, dict)]


def historical_market_state(ticker: str, *, start: str, end: str) -> dict[str, Any]:
    """Return QMD-derived halt/resume and estimated LULD state at a historical cutoff."""
    resolved_ticker = _historical_ticker(ticker)
    common = {"start": start, "end": end, "as_of": end, "limit": 50_000}
    conditions = _historical_gateway_get(
        f"/snapshot/condition-bars/{urllib.parse.quote(resolved_ticker)}",
        {**common, "resolution": "1s"},
        timeout=90,
    )
    chart = _historical_gateway_get(
        f"/snapshot/chart-bars/{urllib.parse.quote(resolved_ticker)}",
        {**common, "timeframe": "1s", "limit": 1},
        timeout=90,
    )
    rows = list(conditions.get("rows") or []) if isinstance(conditions, dict) else []
    trading_status = "trading"
    status_as_of = end
    for row in sorted(rows, key=lambda item: int(item.get("last_event_timestamp_us") or 0)):
        if row.get("condition_halt_pause_flag"):
            trading_status = "halted"
            status_as_of = str(row.get("bar_end") or status_as_of)
        if row.get("condition_resume_flag"):
            trading_status = "resumed"
            status_as_of = str(row.get("bar_end") or status_as_of)
    bars = list(chart.get("bars") or []) if isinstance(chart, dict) else []
    bar = bars[-1] if bars else {}
    return {
        "as_of": status_as_of,
        "trading_status": trading_status,
        "is_tradable": trading_status != "halted",
        "luld_active": bool(bar.get("estimated_luld_active")),
        "luld_state": str(bar.get("estimated_luld_state") or "unknown"),
        "luld_lower_price": float(bar.get("estimated_luld_lower_price") or 0),
        "luld_upper_price": float(bar.get("estimated_luld_upper_price") or 0),
        "luld_distance_to_lower_pct": float(bar.get("estimated_luld_distance_to_lower_pct") or 0),
        "luld_distance_to_upper_pct": float(bar.get("estimated_luld_distance_to_upper_pct") or 0),
        "source": "qmd-history-gateway",
    }


def historical_ticker_change(ticker: str, *, as_of: str) -> dict[str, Any]:
    """Compare the point-in-time trade price with the prior 04:00-20:00 ET session close."""
    resolved_ticker = _historical_ticker(ticker)
    resolved_as_of = datetime.fromisoformat(as_of)
    if resolved_as_of.tzinfo is None:
        raise ValueError("as_of must include a timezone")
    exchange_as_of = resolved_as_of.astimezone(ZoneInfo("America/New_York"))
    session_date = exchange_as_of.date()
    macro = historical_macro_bar_history(
        ticker=resolved_ticker,
        timeframe="1d",
        session_date=session_date,
        as_of=resolved_as_of.isoformat(),
    )
    prior_rows = [
        row for row in macro.get("history", [])
        if str(row.get("session_date") or "") < session_date.isoformat() and float(row.get("close") or 0) > 0
    ]
    previous = prior_rows[-1] if prior_rows else {}
    previous_close = float(previous.get("close") or 0)
    session_start = datetime.combine(session_date, time(4, 0), tzinfo=ZoneInfo("America/New_York"))
    session_end = datetime.combine(session_date, time(20, 0), tzinfo=ZoneInfo("America/New_York"))
    current_end = min(exchange_as_of, session_end)
    events = historical_compact_events(
        resolved_ticker,
        start=session_start.isoformat(),
        end=current_end.isoformat(),
        row_limit=5_000,
    ) if current_end > session_start else []
    current_price = _latest_compact_price(events)
    absolute_change = current_price - previous_close if current_price > 0 and previous_close > 0 else 0.0
    percent_change = absolute_change / previous_close * 100 if previous_close > 0 and current_price > 0 else 0.0
    return {
        "as_of": resolved_as_of.isoformat(),
        "current_price": current_price or None,
        "previous_close": previous_close or None,
        "previous_session_date": str(previous.get("session_date") or ""),
        "absolute_change": absolute_change if current_price > 0 and previous_close > 0 else None,
        "percent_change": percent_change if current_price > 0 and previous_close > 0 else None,
        "source": "qmd-history-gateway",
        "ticker": resolved_ticker,
    }


def _latest_compact_price(events: list[dict[str, Any]]) -> float:
    latest_quote_midpoint = 0.0
    for event in reversed(events):
        event_meta = int(event.get("event_meta") or 0)
        primary_scale = 10_000 if event_meta & 0x02 else 100
        if event_meta & 0x01:
            price = float(event.get("price_primary_int") or 0) / primary_scale
            if price > 0:
                return price
        if latest_quote_midpoint <= 0:
            secondary_scale = 10_000 if event_meta & 0x04 else 100
            ask = float(event.get("price_primary_int") or 0) / primary_scale
            bid = float(event.get("price_secondary_int") or 0) / secondary_scale
            if ask > 0 and bid > 0 and ask >= bid:
                latest_quote_midpoint = (ask + bid) / 2
    return latest_quote_midpoint


@lru_cache(maxsize=1)
def market_event_references() -> dict[str, dict[str, dict[str, Any]]]:
    exchanges = _reference_rows(MARKET_REFERENCE_DIR / "stock_exchanges.json")
    conditions = build_condition_token_rows(MARKET_REFERENCE_DIR)
    return {
        "exchanges": {
            str(row["dense_id"]): {
                "acronym": str(row.get("acronym") or ""),
                "mic": str(row.get("mic") or ""),
                "name": str(row.get("name") or "Unknown venue"),
                "participant_id": str(row.get("participant_id") or ""),
                "type": str(row.get("type") or ""),
            }
            for row in exchanges
            if isinstance(row.get("dense_id"), int) and row["dense_id"] > 0
        },
        "conditions": {
            str(row["token_id"]): {
                "name": str(row.get("condition") or "Unknown condition"),
                "sip_mapping": str(row.get("sip_mapping") or ""),
                "type": str(row.get("source_family") or ""),
                "update_high_low": bool(row.get("update_high_low")),
                "update_last": bool(row.get("update_last")),
                "update_volume": bool(row.get("update_volume")),
            }
            for row in conditions
            if isinstance(row.get("token_id"), int) and row["token_id"] > 0
        },
    }


def _reference_rows(path: Path) -> list[dict[str, Any]]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    rows = payload.get("results") if isinstance(payload, dict) else None
    if not isinstance(rows, list):
        raise RuntimeError(f"Market reference file must contain a results array: {path}")
    return [row for row in rows if isinstance(row, dict)]


def _historical_ticker(value: str) -> str:
    ticker = value.strip().upper()
    if not re.fullmatch(r"[A-Z0-9.\-]{1,15}", ticker):
        raise ValueError("ticker must contain 1-15 letters, numbers, dots, or hyphens")
    return ticker


def _historical_timeframe(value: str) -> str:
    timeframe = value.strip().lower()
    if timeframe not in SUPPORTED_HISTORICAL_TIMEFRAMES:
        raise ValueError(f"unsupported timeframe {value}")
    return timeframe


def _preflight_check(
    check_id: str,
    label: str,
    status: str,
    summary: str,
    evidence: str,
    *,
    required: bool,
) -> dict[str, Any]:
    return {
        "id": check_id,
        "label": label,
        "status": status,
        "summary": summary,
        "evidence": evidence,
        "required": required,
    }
