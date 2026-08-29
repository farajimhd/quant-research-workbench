from __future__ import annotations

import json
import hashlib
import math
import os
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Collection, Iterable, Literal
from zoneinfo import ZoneInfo

from dotenv import load_dotenv

from src.request_context import (
    ContextThreadPoolExecutor as ThreadPoolExecutor,
    causal_identity,
    current_request_headers,
    current_request_query,
)


REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_QMD_BASE_URL = "http://127.0.0.1:8795"
DEFAULT_QMD_HISTORY_BASE_URL = "http://127.0.0.1:8801"
ENRICHED_QMD_TIMEFRAMES = frozenset({"100ms", "1s", "5s", "10s", "30s", "1m", "5m", "1h"})
MACRO_QMD_TIMEFRAMES = frozenset({"1d", "1w", "1mo", "1y"})
DEFAULT_HISTORICAL_WATCHLIST_TIMEOUT_SECONDS = 900

QmdProduct = Literal["chart", "compact_events", "scanner"]
QmdAuthority = Literal["auto", "live", "history"]
QmdMode = Literal["live", "replay", "backtest", "debug"]


@dataclass(frozen=True, slots=True)
class QmdProductRequest:
    product: QmdProduct
    authority: QmdAuthority = "auto"
    mode: QmdMode = "live"
    ticker: str = ""
    timeframe: str = ""
    start: str | None = None
    end: str | None = None
    as_of: str | None = None
    before: str | None = None
    indicator_columns: tuple[str, ...] = ()
    allow_persisted_bars: bool = True
    include_market_signals: bool = True
    include_structure: bool = True
    stage: str = "full"
    limit: int = 500
    tail: bool = False
    after_sequence: int | None = None
    timeout_seconds: float | None = None


@dataclass(frozen=True, slots=True)
class QmdProductResponse:
    schema_version: int
    product: QmdProduct
    authority: Literal["live", "history"]
    endpoint: str
    payload: Any
    complete: bool | None = None
    warnings: tuple[str, ...] = ()
    coverage_status: str = ""
    source_revision: str = ""


class QmdServiceError(RuntimeError):
    """Typed backend boundary error for QMD Live and QMD History transport."""

    def __init__(
        self,
        *,
        service: str,
        operation: str,
        path: str,
        code: str,
        message: str,
        retryable: bool,
        upstream_status: int | None = None,
    ) -> None:
        super().__init__(message)
        self.service = service
        self.operation = operation
        self.path = path
        self.code = code
        self.retryable = retryable
        self.upstream_status = upstream_status

    def as_detail(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "message": str(self),
            "operation": self.operation,
            "path": self.path,
            "retryable": self.retryable,
            "service": self.service,
            "upstream_status": self.upstream_status,
        }


def qmd_product_request(
    request: QmdProductRequest,
    *,
    live_get: Callable[..., Any] | None = None,
    history_get: Callable[..., Any] | None = None,
) -> QmdProductResponse:
    authority, endpoint, params = _qmd_product_route(request)
    timeout = request.timeout_seconds or (3 if authority == "live" else 90)
    resolved_live_get = live_get or qmd_get_json
    resolved_history_get = history_get or qmd_history_get_json
    if authority == "history" and request.product in {"chart", "compact_events", "scanner"}:
        payload = _qmd_composed_history_product(
            request,
            endpoint=endpoint,
            params=params,
            history_get=resolved_history_get,
            live_get=resolved_live_get,
            timeout=timeout,
        )
    else:
        payload = (
            resolved_live_get(endpoint, params, timeout=int(timeout))
            if authority == "live"
            else resolved_history_get(endpoint, params, timeout=timeout)
        )
    complete, warnings, coverage_status, source_revision = _qmd_response_metadata(payload)
    return QmdProductResponse(
        schema_version=2,
        product=request.product,
        authority=authority,
        endpoint=endpoint,
        payload=payload,
        complete=complete,
        warnings=warnings,
        coverage_status=coverage_status,
        source_revision=source_revision,
    )


def _qmd_composed_history_product(
    request: QmdProductRequest,
    *,
    endpoint: str,
    params: dict[str, Any],
    history_get: Callable[..., Any],
    live_get: Callable[..., Any],
    timeout: float,
) -> Any:
    historical = history_get(endpoint, params, timeout=timeout)
    if request.product == "compact_events":
        if not isinstance(historical, list):
            raise RuntimeError("QMD History compact-event response must be an array")
        return historical
    if not isinstance(historical, dict):
        raise RuntimeError(f"QMD History {request.product} response must be an object")
    plan = _qmd_source_plan(request, history_get=history_get, timeout=timeout)
    intervals = _qmd_live_intervals(plan)
    if not intervals:
        return historical
    timeframe = request.timeframe.strip().lower() or "1m"
    if request.product == "chart" and timeframe in MACRO_QMD_TIMEFRAMES:
        return _qmd_chart_live_continuation(
            request,
            historical=historical,
            intervals=intervals,
            plan=plan,
            live_get=live_get,
            timeout=timeout,
        )
    return _qmd_native_continuation_metadata(historical, plan)


def _qmd_native_continuation_metadata(
    historical: dict[str, Any], plan: dict[str, Any]
) -> dict[str, Any]:
    payload = dict(historical)
    revision = payload.get("source_revision")
    if not isinstance(revision, dict):
        cache = payload.get("cache")
        revision = cache.get("source_revision") if isinstance(cache, dict) else None
    request_complete = bool(
        isinstance(revision, dict)
        and revision.get("request_complete")
        and revision.get("live_continuation_sequence") is not None
    )
    payload["complete"] = request_complete
    payload["coverage_status"] = (
        "complete_with_live_continuation" if request_complete else "incomplete"
    )
    payload["source_plan"] = plan
    if isinstance(revision, dict):
        payload["source_revision"] = revision
    if not request_complete:
        warnings = list(payload.get("warnings") or [])
        warnings.append(
            {
                "code": "qmd_native_continuation_incomplete",
                "message": "QMD History did not certify the current-live source segment as complete.",
            }
        )
        payload["warnings"] = warnings
    return payload


def _qmd_source_plan(
    request: QmdProductRequest,
    *,
    history_get: Callable[..., Any],
    timeout: float,
) -> dict[str, Any]:
    params: dict[str, Any] = {"start": request.start, "end": request.end}
    ticker = request.ticker.strip().upper()
    if ticker:
        params["tickers"] = ticker
    plan = history_get("/source-plan", params, timeout=timeout)
    if not isinstance(plan, dict):
        raise RuntimeError("QMD History source plan must be an object")
    return plan


def _qmd_live_intervals(plan: dict[str, Any]) -> list[tuple[datetime, datetime]]:
    return [
        (
            _validate_window_timestamp("source segment start", str(segment.get("start"))),
            _validate_window_timestamp("source segment end", str(segment.get("end"))),
        )
        for segment in plan.get("segments") or []
        if isinstance(segment, dict) and segment.get("tier") == "current_live"
    ]


def _qmd_row_in_intervals(
    row: dict[str, Any],
    intervals: Collection[tuple[datetime, datetime]],
    *keys: str,
) -> bool:
    raw = next((row.get(key) for key in keys if row.get(key)), None)
    if raw is None:
        return False
    try:
        timestamp = _validate_window_timestamp("live continuation row", str(raw))
    except ValueError:
        return False
    return any(start <= timestamp < end for start, end in intervals)


def _qmd_merge_timestamp_rows(
    historical: Collection[Any],
    live: Collection[Any],
    *,
    identity_keys: tuple[str, ...],
) -> list[dict[str, Any]]:
    merged: dict[tuple[str, ...], dict[str, Any]] = {}
    for source in (historical, live):
        for row in source:
            if not isinstance(row, dict):
                continue
            identity = tuple(str(row.get(key) or "").upper() for key in identity_keys)
            if all(identity):
                merged[identity] = dict(row)
    return sorted(
        merged.values(),
        key=lambda row: tuple(str(row.get(key) or "") for key in identity_keys),
    )


def _qmd_continuation_metadata(payload: dict[str, Any], plan: dict[str, Any]) -> None:
    warnings = list(payload.get("warnings") or [])
    warnings.append(
        {
            "code": "live_snapshot_continuation",
            "message": (
                "Current-live rows were composed from bounded QMD Live snapshots; "
                "the response is not a pinned replay source."
            ),
        }
    )
    payload["complete"] = False
    payload["coverage_status"] = "live_snapshot_continuation"
    payload["source_plan"] = plan
    payload["warnings"] = warnings


def _qmd_chart_live_continuation(
    request: QmdProductRequest,
    *,
    historical: dict[str, Any],
    intervals: list[tuple[datetime, datetime]],
    plan: dict[str, Any],
    live_get: Callable[..., Any],
    timeout: float,
) -> dict[str, Any]:
    payload = dict(historical)
    ticker = request.ticker.strip().upper()
    timeframe = request.timeframe.strip().lower() or "1m"
    live_timeout = int(min(timeout, 10))
    limit = max(1, min(int(request.limit), 50_000))
    if timeframe in MACRO_QMD_TIMEFRAMES:
        live = live_get(
            f"/snapshot/macro-bars/{urllib.parse.quote(ticker)}",
            {"limit": limit, "timeframe": timeframe},
            timeout=live_timeout,
        )
        live_rows = live.get("rows") or [] if isinstance(live, dict) else []
        filtered = [
            row for row in live_rows
            if isinstance(row, dict) and _qmd_row_in_intervals(row, intervals, "bar_start")
        ]
        payload["bars"] = _qmd_merge_timestamp_rows(
            payload.get("bars") or [], filtered, identity_keys=("bar_start", "ticker", "timeframe")
        )[-limit:]
    else:
        live_bars = live_get(
            f"/snapshot/bars/{urllib.parse.quote(ticker)}",
            {"limit": limit, "timeframe": timeframe},
            timeout=live_timeout,
        )
        bar_rows = []
        if isinstance(live_bars, dict):
            bar_rows.extend(live_bars.get("history") or [])
            if isinstance(live_bars.get("current"), dict):
                bar_rows.append(live_bars["current"])
        filtered_bars = [
            row for row in bar_rows
            if isinstance(row, dict) and _qmd_row_in_intervals(row, intervals, "bar_start")
        ]
        payload["bars"] = _qmd_merge_timestamp_rows(
            payload.get("bars") or [], filtered_bars, identity_keys=("bar_start", "sym", "timeframe")
        )[-limit:]
        if request.stage == "full":
            live_indicators = live_get(
                f"/snapshot/indicators/{urllib.parse.quote(ticker)}",
                {"limit": limit, "timeframe": timeframe},
                timeout=live_timeout,
            )
            indicator_rows = []
            if isinstance(live_indicators, dict):
                indicator_rows.extend(live_indicators.get("history") or [])
                if isinstance(live_indicators.get("current"), dict):
                    indicator_rows.append(live_indicators["current"])
            filtered_indicators = [
                row for row in indicator_rows
                if isinstance(row, dict) and _qmd_row_in_intervals(row, intervals, "bar_start")
            ]
            payload["indicators"] = _qmd_merge_timestamp_rows(
                payload.get("indicators") or [],
                filtered_indicators,
                identity_keys=("bar_start", "sym", "timeframe"),
            )[-limit:]
            payload["indicators_available"] = bool(payload["indicators"])
    _qmd_continuation_metadata(payload, plan)
    return payload


def _qmd_response_metadata(
    payload: Any,
) -> tuple[bool | None, tuple[str, ...], str, str]:
    if not isinstance(payload, dict):
        return None, (), "", ""
    complete_value = payload.get("complete")
    complete = complete_value if isinstance(complete_value, bool) else None
    warning_rows = payload.get("warnings") or []
    if isinstance(warning_rows, (str, dict)):
        warning_rows = [warning_rows]
    elif not isinstance(warning_rows, (list, tuple)):
        warning_rows = []
    warnings = tuple(
        str(row.get("message") or row.get("detail") or row.get("code") or "").strip()
        if isinstance(row, dict)
        else str(row).strip()
        for row in warning_rows
        if (isinstance(row, dict) or str(row).strip())
    )
    coverage = payload.get("coverage")
    coverage_status = str(
        payload.get("coverage_status")
        or (coverage.get("status") if isinstance(coverage, dict) else "")
        or ""
    )
    revision = payload.get("source_revision")
    source_revision = str(
        revision.get("token") if isinstance(revision, dict) else revision or ""
    )
    return complete, tuple(value for value in warnings if value), coverage_status, source_revision


def _qmd_product_route(
    request: QmdProductRequest,
) -> tuple[Literal["live", "history"], str, dict[str, Any]]:
    if request.product not in {"chart", "compact_events", "scanner"}:
        raise ValueError(f"unsupported QMD product: {request.product}")
    if request.stage not in {"bars", "full"}:
        raise ValueError("QMD chart stage must be bars or full")
    if request.mode not in {"live", "replay", "backtest", "debug"}:
        raise ValueError("QMD mode must be live, replay, backtest, or debug")
    limit = max(1, min(int(request.limit), 50_000))
    has_window = bool(request.start or request.end or request.as_of)
    authority: Literal["live", "history"] = (
        "history" if request.authority == "history" or request.authority == "auto" and has_window else "live"
    )
    if request.authority == "live" and has_window:
        raise ValueError("Live QMD product requests cannot carry a historical window")
    if authority == "history":
        if not request.start or not request.end:
            raise ValueError("Historical QMD product requests require start and end")
        window_start = _validate_window_timestamp("start", request.start)
        window_end = _validate_window_timestamp("end", request.end)
        if window_start >= window_end:
            raise ValueError("QMD historical window start must precede end")
        if request.as_of:
            _validate_window_timestamp("as_of", request.as_of)

    ticker = request.ticker.strip().upper()
    if request.product != "scanner" and not ticker:
        raise ValueError(f"ticker is required for QMD {request.product}")
    quoted_ticker = urllib.parse.quote(ticker)
    if request.product == "scanner":
        if authority == "live":
            return authority, "/snapshot/scanner", {"limit": min(limit, 25_000)}
        return authority, "/snapshot/scanner-derived", {
            "as_of": request.as_of or request.end,
            "end": request.end,
            "start": request.start,
        }
    if request.product == "compact_events":
        if authority == "history" and request.after_sequence:
            raise ValueError("Historical compact-event pages use their source-owned cursor")
        return authority, (
            f"/snapshot/compact-event-page/{quoted_ticker}"
            if authority == "live"
            else f"/snapshot/compact-events/{quoted_ticker}"
        ), (
            {
                "limit": limit,
                "after_arrival_sequence": (
                    max(0, int(request.after_sequence))
                    if request.after_sequence is not None
                    else None
                ),
            }
            if authority == "live"
            else {"start": request.start, "end": request.end, "limit": limit, "tail": str(request.tail).lower()}
        )
    timeframe = request.timeframe.strip().lower() or "1m"
    if authority == "live":
        if timeframe in MACRO_QMD_TIMEFRAMES:
            return authority, f"/snapshot/macro-bars/{quoted_ticker}", {"limit": limit, "timeframe": timeframe}
        if timeframe in ENRICHED_QMD_TIMEFRAMES:
            return authority, f"/snapshot/bars/{quoted_ticker}", {"timeframe": timeframe, "limit": limit}
        return authority, f"/snapshot/family-bars/{quoted_ticker}", {
            "family": "trade", "limit": limit, "price_only": True, "resolution": timeframe,
        }
    endpoint = (
        f"/snapshot/chart-macro-bars/{quoted_ticker}"
        if timeframe in MACRO_QMD_TIMEFRAMES
        else f"/snapshot/chart-bars/{quoted_ticker}"
    )
    return authority, endpoint, {
        "start": request.start,
        "end": request.end,
        "as_of": request.as_of or request.end,
        "before": request.before,
        "indicator_columns": ",".join(dict.fromkeys(request.indicator_columns)) or None,
        "allow_persisted_bars": str(request.allow_persisted_bars).lower(),
        "include_market_signals": str(request.include_market_signals).lower(),
        "include_structure": str(request.include_structure).lower(),
        "mode": request.mode,
        "stage": request.stage,
        "timeframe": timeframe,
        "limit": limit,
    }


def _validate_window_timestamp(label: str, value: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError(f"QMD {label} must be an ISO timestamp") from exc
    if parsed.tzinfo is None:
        raise ValueError(f"QMD {label} must include a timezone")
    return parsed


def load_qmd_env() -> None:
    for env_path in (Path.cwd() / ".env", REPO_ROOT / ".env"):
        if env_path.exists():
            load_dotenv(env_path, override=False)
    load_dotenv(override=False)


def qmd_base_url() -> str:
    load_qmd_env()
    return os.environ.get("REAL_LIVE_QMD_GATEWAY_URL") or os.environ.get("QMD_GATEWAY_URL") or DEFAULT_QMD_BASE_URL


def qmd_enabled() -> bool:
    load_qmd_env()
    return os.environ.get("REAL_LIVE_QMD_GATEWAY_ENABLED", "1").strip().lower() not in {"0", "false", "no", "off"}


def qmd_history_base_url() -> str:
    load_qmd_env()
    configured = os.environ.get("QMD_HISTORY_GATEWAY_URL", "").strip()
    if configured:
        return configured.rstrip("/")
    bind = os.environ.get("QMD_HISTORY_BIND", "127.0.0.1:8801").strip()
    if bind.startswith("http://") or bind.startswith("https://"):
        return bind.rstrip("/")
    host, separator, port = bind.rpartition(":")
    resolved_host = host if separator else bind
    resolved_port = port if separator else "8801"
    if resolved_host in {"0.0.0.0", "::", "[::]"}:
        resolved_host = "127.0.0.1"
    return f"http://{resolved_host}:{resolved_port}" if resolved_host else DEFAULT_QMD_HISTORY_BASE_URL


def qmd_get_json(path: str, params: dict[str, Any] | None = None, *, timeout: int = 3) -> Any:
    if not qmd_enabled():
        raise _qmd_disabled_error("GET", path)
    return _qmd_service_get_json(
        qmd_base_url(),
        path,
        params,
        timeout=timeout,
        service_label="QMD",
    )


def qmd_history_get_json(
    path: str,
    params: dict[str, Any] | None = None,
    *,
    timeout: float = 3,
) -> Any:
    return _qmd_service_get_json(
        qmd_history_base_url(),
        path,
        params,
        timeout=timeout,
        service_label="QMD History",
    )


def qmd_history_post_json(
    path: str,
    payload: dict[str, Any],
    *,
    timeout: float = 10,
) -> Any:
    url = f"{qmd_history_base_url().rstrip('/')}/{path.lstrip('/')}"
    request = urllib.request.Request(
        url,
        data=json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8"),
        method="POST",
        headers={
            "Accept": "application/json",
            "Content-Type": "application/json",
            **current_request_headers(),
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            text = response.read().decode("utf-8")
    except urllib.error.HTTPError as exc:
        raise _qmd_http_error("QMD History", "POST", path, url, exc) from exc
    except (urllib.error.URLError, TimeoutError) as exc:
        raise _qmd_transport_error(
            "QMD History", "POST", path, url, exc,
            timeout=timeout,
            base_url=qmd_history_base_url(),
        ) from exc
    return _qmd_decode_json(text, service_label="QMD History", operation="POST", path=path)


def qmd_validate_historical_watchlist_plan(plan: dict[str, Any]) -> dict[str, Any]:
    payload = qmd_history_post_json(
        "/plans/watchlist-timeline/validate",
        plan,
        timeout=10,
    )
    if not isinstance(payload, dict) or not bool(payload.get("valid")):
        raise RuntimeError("QMD History rejected the historical Watchlist plan")
    if str(payload.get("plan_hash") or "") != str(plan.get("plan_hash") or ""):
        raise RuntimeError("QMD History returned a different historical Watchlist plan hash")
    return payload


def qmd_historical_source_revision(
    *, start: str, end: str, tickers: list[str] | tuple[str, ...] = ()
) -> dict[str, Any]:
    payload = qmd_history_get_json(
        "/source-revision",
        {
            "start": start,
            "end": end,
            "tickers": ",".join(
                sorted({
                    str(ticker).strip().upper()
                    for ticker in tickers
                    if str(ticker).strip()
                })
            ) or None,
        },
        timeout=30,
    )
    if not isinstance(payload, dict):
        raise RuntimeError("QMD History returned an invalid source revision")
    if not str(payload.get("token") or "") or not str(
        payload.get("source_plan_hash") or ""
    ):
        raise RuntimeError("QMD History source revision has no stable identity")
    if not bool(payload.get("complete_for_history")) or not bool(
        payload.get("request_complete")
    ):
        raise RuntimeError("QMD History source revision is incomplete")
    return payload


def qmd_materialize_historical_watchlist_timeline(
    plan: dict[str, Any],
    *,
    external_feature_revisions: list[dict[str, Any]],
    external_feature_intervals: list[dict[str, Any]],
) -> dict[str, Any]:
    payload = qmd_history_post_json(
        "/materialize/watchlist-timeline",
        {
            "plan": plan,
            "external_feature_revisions": external_feature_revisions,
            "external_feature_intervals": external_feature_intervals,
        },
        timeout=_historical_watchlist_timeout_seconds(),
    )
    if not isinstance(payload, dict):
        raise RuntimeError("QMD History returned an invalid historical Watchlist timeline")
    if str(payload.get("plan_hash") or "") != str(plan.get("plan_hash") or ""):
        raise RuntimeError("QMD History materialized a different historical Watchlist plan")
    if not str(payload.get("materialization_id") or "").startswith("sha256:"):
        raise RuntimeError("QMD History Watchlist timeline has no materialization identity")
    if not isinstance(payload.get("chunks"), list):
        raise RuntimeError("QMD History Watchlist timeline has no chunk projection")
    return payload


def qmd_materialize_historical_watchlist_timelines(
    requests: list[dict[str, Any]],
) -> dict[str, Any]:
    payload = qmd_history_post_json(
        "/materialize/watchlist-timelines",
        {"requests": requests},
        timeout=_historical_watchlist_timeout_seconds(),
    )
    if not isinstance(payload, dict):
        raise RuntimeError("QMD History returned an invalid Watchlist timeline batch")
    if not str(payload.get("batch_materialization_id") or "").startswith("sha256:"):
        raise RuntimeError("QMD History Watchlist batch has no materialization identity")
    materializations = payload.get("materializations")
    if not isinstance(materializations, list) or len(materializations) != len(requests):
        raise RuntimeError("QMD History Watchlist batch returned the wrong plan count")
    expected = {
        str(dict(request.get("plan") or {}).get("watchlist_id") or ""):
        str(dict(request.get("plan") or {}).get("plan_hash") or "")
        for request in requests
    }
    actual = {
        str(dict(row).get("watchlist_id") or ""):
        str(dict(row).get("plan_hash") or "")
        for row in materializations
    }
    if actual != expected:
        raise RuntimeError("QMD History Watchlist batch changed plan identity")
    if any(
        not str(dict(row).get("materialization_id") or "").startswith("sha256:")
        or not isinstance(dict(row).get("chunks"), list)
        for row in materializations
    ):
        raise RuntimeError("QMD History Watchlist batch contains an invalid timeline")
    return payload


def _historical_watchlist_timeout_seconds() -> int:
    try:
        configured = int(
            os.environ.get(
                "QMD_HISTORICAL_WATCHLIST_TIMEOUT_SECONDS",
                DEFAULT_HISTORICAL_WATCHLIST_TIMEOUT_SECONDS,
            )
        )
    except (TypeError, ValueError):
        configured = DEFAULT_HISTORICAL_WATCHLIST_TIMEOUT_SECONDS
    return max(300, min(configured, 1_800))


def _qmd_service_get_json(
    base_url: str,
    path: str,
    params: dict[str, Any] | None,
    *,
    timeout: float,
    service_label: str,
) -> Any:
    query = urllib.parse.urlencode({key: value for key, value in (params or {}).items() if value is not None})
    url = f"{base_url.rstrip('/')}/{path.lstrip('/')}"
    if query:
        url = f"{url}?{query}"
    request = urllib.request.Request(
        url,
        method="GET",
        headers={"Accept": "application/json", **current_request_headers()},
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            text = response.read().decode("utf-8")
    except urllib.error.HTTPError as exc:
        raise _qmd_http_error(service_label, "GET", path, url, exc) from exc
    except (urllib.error.URLError, TimeoutError) as exc:
        raise _qmd_transport_error(
            service_label, "GET", path, url, exc,
            timeout=timeout,
            base_url=base_url,
        ) from exc
    return _qmd_decode_json(text, service_label=service_label, operation="GET", path=path)


def qmd_put_json(path: str, payload: dict[str, Any], *, timeout: int = 3) -> Any:
    if not qmd_enabled():
        raise _qmd_disabled_error("PUT", path)
    url = f"{qmd_base_url().rstrip('/')}{path}"
    request = urllib.request.Request(
        url,
        data=json.dumps(payload, separators=(",", ":")).encode("utf-8"),
        method="PUT",
        headers={"Accept": "application/json", "Content-Type": "application/json", **current_request_headers()},
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            text = response.read().decode("utf-8")
    except urllib.error.HTTPError as exc:
        raise _qmd_http_error("QMD", "PUT", path, url, exc) from exc
    except (urllib.error.URLError, TimeoutError) as exc:
        raise _qmd_transport_error(
            "QMD", "PUT", path, url, exc, timeout=timeout
        ) from exc
    return _qmd_decode_json(text, service_label="QMD", operation="PUT", path=path)


def qmd_post_json(path: str, payload: dict[str, Any], *, timeout: int = 3) -> Any:
    if not qmd_enabled():
        raise _qmd_disabled_error("POST", path)
    url = f"{qmd_base_url().rstrip('/')}/{path.lstrip('/')}"
    request = urllib.request.Request(
        url,
        data=json.dumps(payload, separators=(",", ":"), default=str).encode("utf-8"),
        method="POST",
        headers={"Accept": "application/json", "Content-Type": "application/json", **current_request_headers()},
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            text = response.read().decode("utf-8")
    except urllib.error.HTTPError as exc:
        raise _qmd_http_error("QMD", "POST", path, url, exc) from exc
    except (urllib.error.URLError, TimeoutError) as exc:
        raise _qmd_transport_error("QMD", "POST", path, url, exc, timeout=timeout) from exc
    return _qmd_decode_json(text, service_label="QMD", operation="POST", path=path)


def qmd_configure_signal_streams(payload: dict[str, Any]) -> dict[str, Any]:
    response = qmd_put_json("/signal-streams/configuration", payload, timeout=10)
    if not isinstance(response, dict):
        raise RuntimeError("QMD returned an invalid Signal Stream configuration response")
    return response


def qmd_evaluate_signal_streams(payload: dict[str, Any]) -> dict[str, Any]:
    response = qmd_post_json("/signal-streams/evaluate", payload, timeout=30)
    if not isinstance(response, dict):
        raise RuntimeError("QMD returned an invalid Signal Stream evaluation response")
    return response


def qmd_append_signal_stream_rows(signal_stream_id: str, rows: list[dict[str, Any]]) -> dict[str, Any]:
    response = qmd_post_json(
        "/signal-streams/external",
        {"signal_stream_id": signal_stream_id, "rows": rows},
        timeout=30,
    )
    if not isinstance(response, dict):
        raise RuntimeError("QMD returned an invalid external Signal Stream response")
    return response


def qmd_signal_stream_snapshot(
    *, signal_stream_id: str = "", as_of: str = "", after_sequence: int | None = None, limit: int = 5000
) -> dict[str, Any]:
    payload = qmd_get_json(
        "/snapshot/signal-streams",
        {
            "signal_stream_id": signal_stream_id or None,
            "as_of": as_of or None,
            "after_sequence": after_sequence,
            "limit": max(1, min(int(limit), 50_000)),
        },
        timeout=5,
    )
    if not isinstance(payload, dict):
        raise RuntimeError("QMD returned an invalid Signal Stream snapshot")
    return payload


def qmd_delete_json(path: str, *, timeout: int = 3) -> Any:
    if not qmd_enabled():
        raise _qmd_disabled_error("DELETE", path)
    url = f"{qmd_base_url().rstrip('/')}{path}"
    request = urllib.request.Request(
        url,
        method="DELETE",
        headers={"Accept": "application/json", **current_request_headers()},
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            text = response.read().decode("utf-8")
    except urllib.error.HTTPError as exc:
        raise _qmd_http_error("QMD", "DELETE", path, url, exc) from exc
    except (urllib.error.URLError, TimeoutError) as exc:
        raise _qmd_transport_error(
            "QMD", "DELETE", path, url, exc, timeout=timeout
        ) from exc
    return _qmd_decode_json(text, service_label="QMD", operation="DELETE", path=path)


def _qmd_http_error(
    service_label: str,
    operation: str,
    path: str,
    url: str,
    error: urllib.error.HTTPError,
) -> QmdServiceError:
    body = error.read().decode("utf-8", errors="replace")
    return QmdServiceError(
        service=service_label,
        operation=operation,
        path=path,
        code="qmd_upstream_http_error",
        message=(
            f"{service_label} {operation} {safe_qmd_url(url)} failed with HTTP "
            f"{error.code}: {body[:500]}"
        ),
        retryable=error.code in {408, 425, 429} or error.code >= 500,
        upstream_status=error.code,
    )


def _qmd_unavailable_error(
    service_label: str,
    operation: str,
    path: str,
    url: str,
    error: BaseException,
    *,
    base_url: str = "",
) -> QmdServiceError:
    if service_label == "QMD History" and base_url:
        message = (
            f"QMD History gateway is not reachable at {base_url.rstrip('/')}. "
            "Start scripts/run_qmd_history_gateway.ps1 and wait for its /health status to be ready."
        )
    else:
        reason = getattr(error, "reason", None) or str(error) or type(error).__name__
        message = (
            f"{service_label} {operation} {safe_qmd_url(url)} failed: {reason}"
        )
    return QmdServiceError(
        service=service_label,
        operation=operation,
        path=path,
        code="qmd_upstream_unavailable",
        message=message,
        retryable=True,
    )


def _qmd_transport_error(
    service_label: str,
    operation: str,
    path: str,
    url: str,
    error: BaseException,
    *,
    timeout: float,
    base_url: str = "",
) -> QmdServiceError:
    reason = getattr(error, "reason", None)
    if isinstance(error, TimeoutError) or isinstance(reason, TimeoutError):
        timeout_label = f"{timeout:g}"
        return QmdServiceError(
            service=service_label,
            operation=operation,
            path=path,
            code="qmd_upstream_timeout",
            message=(
                f"{service_label} {operation} {path} timed out after "
                f"{timeout_label} seconds."
            ),
            retryable=True,
        )
    return _qmd_unavailable_error(
        service_label,
        operation,
        path,
        url,
        error,
        base_url=base_url,
    )


def _qmd_decode_json(
    text: str,
    *,
    service_label: str,
    operation: str,
    path: str,
) -> Any:
    try:
        return json.loads(text) if text.strip() else {}
    except json.JSONDecodeError as exc:
        raise QmdServiceError(
            service=service_label,
            operation=operation,
            path=path,
            code="qmd_invalid_json",
            message=f"{service_label} {operation} {path} returned invalid JSON.",
            retryable=False,
        ) from exc


def _qmd_disabled_error(operation: str, path: str) -> QmdServiceError:
    return QmdServiceError(
        service="QMD",
        operation=operation,
        path=path,
        code="qmd_disabled",
        message="QMD gateway is disabled by REAL_LIVE_QMD_GATEWAY_ENABLED.",
        retryable=False,
    )


def qmd_websocket_url(path: str, params: dict[str, Any] | None = None) -> str:
    if not qmd_enabled():
        raise _qmd_disabled_error("STREAM", path)
    return _qmd_service_websocket_url(qmd_base_url(), path, params, service_label="QMD")


def qmd_history_websocket_url(path: str, params: dict[str, Any] | None = None) -> str:
    return _qmd_service_websocket_url(
        qmd_history_base_url(), path, params, service_label="QMD History"
    )


def _qmd_service_websocket_url(
    base_url: str,
    path: str,
    params: dict[str, Any] | None,
    *,
    service_label: str,
) -> str:
    parsed = urllib.parse.urlsplit(base_url.rstrip("/"))
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise RuntimeError(f"{service_label} gateway URL must use http or https.")
    query_values = {key: value for key, value in (params or {}).items() if value is not None}
    for key, value in current_request_query().items():
        query_values.setdefault(key, value)
    query = urllib.parse.urlencode(query_values)
    target_path = f"{parsed.path.rstrip('/')}/{path.lstrip('/')}"
    return urllib.parse.urlunsplit(("wss" if parsed.scheme == "https" else "ws", parsed.netloc, target_path, query, ""))


def qmd_status() -> dict[str, Any]:
    payload = qmd_get_json("/health", timeout=2)
    if not isinstance(payload, dict):
        raise RuntimeError("QMD health response was not an object.")
    payload.setdefault("base_url", qmd_base_url().rstrip("/"))
    payload.setdefault("provider", "qmd-gateway")
    return payload


def qmd_service_status() -> dict[str, Any]:
    """Return QMD's standardized Service Core snapshot without altering health consumers."""
    payload = qmd_get_json("/snapshot/status", timeout=2)
    if not isinstance(payload, dict):
        raise RuntimeError("QMD Service Core status response was not an object.")
    return payload


def qmd_live_market_state(ticker: str) -> dict[str, Any]:
    payload = qmd_get_json(
        f"/snapshot/live-market-state/{urllib.parse.quote(ticker.strip().upper())}",
        timeout=3,
    )
    if not isinstance(payload, dict):
        raise RuntimeError("QMD live market-state response was not an object.")
    return payload


def qmd_active_halts_by_ticker(tickers: Collection[str] = ()) -> dict[str, dict[str, Any]]:
    """Return the current exchange-halt state for the requested ticker set."""

    requested = {
        str(ticker or "").strip().upper()
        for ticker in tickers
        if str(ticker or "").strip()
    }
    payload = qmd_get_json(
        "/snapshot/live-market-state",
        {"include_recent": "false", "limit": 5_000},
        timeout=3,
    )
    active = _active_halts_by_ticker(payload)
    if not requested:
        return active
    return {ticker: state for ticker, state in active.items() if ticker in requested}


def qmd_live_market_state_history(
    *,
    start: datetime,
    end: datetime,
    event_type: str = "",
    event_status: str = "",
    limit: int = 5_000,
) -> dict[str, Any]:
    """Read durable QMD market-state transitions for a bounded causal window."""

    if start.tzinfo is None or end.tzinfo is None:
        raise ValueError("QMD live market-state history bounds must be timezone-aware")
    if start > end:
        raise ValueError("QMD live market-state history start must not follow end")
    payload = qmd_get_json(
        "/history/live-market-state",
        {
            "start": start.astimezone(timezone.utc).isoformat(),
            "end": end.astimezone(timezone.utc).isoformat(),
            "event_type": event_type or None,
            "event_status": event_status or None,
            "limit": max(1, min(int(limit), 50_000)),
        },
        timeout=5,
    )
    if not isinstance(payload, dict) or not isinstance(payload.get("rows"), list):
        raise RuntimeError("QMD live market-state history returned an invalid envelope")
    if payload.get("complete") is not True:
        raise RuntimeError("QMD live market-state history exceeded its bounded result limit")
    return payload


def qmd_ticker_state(ticker: str) -> dict[str, Any]:
    """Return the versioned live-memory state for one ticker."""
    symbol = ticker.strip().upper()
    if not symbol:
        raise ValueError("ticker is required for QMD ticker state")
    payload = qmd_get_json(
        f"/snapshot/ticker-state/{urllib.parse.quote(symbol)}",
        timeout=3,
    )
    if not isinstance(payload, dict):
        raise RuntimeError("QMD ticker state returned an invalid envelope")
    return payload


def qmd_computation_demand() -> dict[str, Any]:
    """Return QMD's active focused-computation leases and demand estimate."""
    payload = qmd_get_json("/computation-targets", timeout=3)
    if not isinstance(payload, dict):
        raise RuntimeError("QMD computation demand returned an invalid envelope")
    return payload


def qmd_computation_requirements(
    *,
    include_live_details: bool = True,
    live_get: Callable[..., Any] = qmd_get_json,
    history_get: Callable[..., Any] = qmd_history_get_json,
) -> dict[str, Any]:
    """Compose live and historical computation requirements without merging authorities."""
    errors: dict[str, str] = {}
    payloads: dict[str, dict[str, Any]] = {}
    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = {
            "qmd_gateway": executor.submit(
                live_get,
                "/computation-targets" if include_live_details else "/computation-targets/summary",
                timeout=3,
            ),
            "qmd_history": executor.submit(history_get, "/snapshot/cache", timeout=3),
        }
        for authority, future in futures.items():
            try:
                value = future.result()
                if not isinstance(value, dict):
                    raise RuntimeError(f"{authority} returned an invalid computation envelope")
                payloads[authority] = value
            except Exception as exc:
                errors[authority] = str(exc)

    requirements: list[dict[str, Any]] = []
    live_payload = payloads.get("qmd_gateway", {})
    for row in live_payload.get("requirements") or []:
        if isinstance(row, dict):
            requirements.append({**row, "authority": "qmd_gateway"})
    if not requirements and isinstance(live_payload.get("requirement_ref_counts"), dict):
        requirements.extend(
            {
                "authority": "qmd_gateway",
                "requirement_id": str(requirement_id),
                "ref_count": int(ref_count or 0),
                "details_unavailable": True,
            }
            for requirement_id, ref_count in live_payload["requirement_ref_counts"].items()
        )

    history_payload = payloads.get("qmd_history", {})
    for row in history_payload.get("requirements") or []:
        if isinstance(row, dict):
            requirements.append({**row, "authority": "qmd_history"})
    requirements.sort(
        key=lambda row: (
            str(row.get("authority") or ""),
            str(row.get("requirement_id") or ""),
        )
    )
    live_requirement_count = (
        int(live_payload.get("active_requirement_count") or 0)
        if "active_requirement_count" in live_payload
        else sum(1 for row in requirements if row.get("authority") == "qmd_gateway")
    )
    offline_requirement_count = sum(
        1 for row in requirements if row.get("authority") == "qmd_history"
    )
    return {
        "schema_version": 1,
        "as_of": datetime.now(timezone.utc).isoformat(),
        "complete": not errors,
        "authorities": {
            "qmd_gateway": "available" if "qmd_gateway" in payloads else "unavailable",
            "qmd_history": "available" if "qmd_history" in payloads else "unavailable",
        },
        "active_requirement_count": live_requirement_count + offline_requirement_count,
        "live_requirement_count": live_requirement_count,
        "offline_requirement_count": offline_requirement_count,
        "live_demand": payloads.get("qmd_gateway"),
        "requirements": requirements,
        "errors": errors,
    }


def qmd_scanner_snapshot(
    row_limit: int = 250,
    *,
    enrichments: Collection[str] = (),
) -> dict[str, Any]:
    """Return the compact Core Scanner projection.

    Watchlist-level indicators and signal lifecycle rows are deliberately
    opt-in. Fetching them for every ordinary scanner refresh defeats the
    computational funnel even when QMD has already materialized the values.
    """
    requested = frozenset(str(value).strip().lower() for value in enrichments)
    supported = frozenset({"indicators", "signals", "signal_events"})
    unknown = requested - supported
    if unknown:
        raise ValueError(f"Unsupported QMD scanner enrichment(s): {', '.join(sorted(unknown))}")

    cross_section_limit = 5_000
    scanner_limit = max(1, min(int(row_limit or 250), 25_000))
    requests: dict[str, tuple[str, dict[str, Any]]] = {
        "scanner": ("/snapshot/scanner", {"limit": scanner_limit}),
        # Abnormal market state is sparse and joined once per scanner snapshot.
        # Excluding transition history keeps this all-symbol projection bounded.
        "market_state": (
            "/snapshot/live-market-state",
            {"include_recent": "false", "limit": 5_000},
        ),
    }
    if "signals" in requested:
        requests["signals"] = ("/snapshot/signals", {"limit": cross_section_limit})
    if "signal_events" in requested:
        requests["signal_events"] = ("/snapshot/signal-events", {"limit": row_limit})
    if "indicators" in requested:
        requests["indicators"] = (
            "/snapshot/scanner-indicators",
            {"limit": cross_section_limit, "timeframe": "10s"},
        )
    with ThreadPoolExecutor(max_workers=len(requests)) as executor:
        futures = {
            key: executor.submit(qmd_get_json, path, params, timeout=3)
            for key, (path, params) in requests.items()
        }
        responses = {key: future.result() for key, future in futures.items()}
    snapshot_payload = responses["scanner"]
    market_state_payload = responses.get("market_state", {})
    active_signal_payload = responses.get("signals", {})
    signal_event_payload = responses.get("signal_events", {})
    indicator_payload = responses.get("indicators", {})
    snapshot_rows = snapshot_payload.get("rows", []) if isinstance(snapshot_payload, dict) else []
    rows = [normalize_qmd_symbol_snapshot(row) for row in snapshot_rows if isinstance(row, dict)]
    active_rows = [
        normalize_qmd_market_signal(row)
        for row in (
            active_signal_payload.get("rows", [])
            if isinstance(active_signal_payload, dict)
            else []
        )
        if isinstance(row, dict)
    ]
    strongest_by_ticker: dict[str, dict[str, Any]] = {}
    for signal in active_rows:
        ticker = str(signal.get("ticker") or "")
        current = strongest_by_ticker.get(ticker)
        signal_rank = (
            float_value(signal.get("signal_rank_score")),
            float_value(signal.get("signal_confidence")),
        )
        current_rank = (
            float_value(current.get("signal_rank_score")) if current else -1.0,
            float_value(current.get("signal_confidence")) if current else -1.0,
        )
        if current is None or signal_rank > current_rank:
            strongest_by_ticker[ticker] = signal
    active_counts: dict[str, int] = {}
    for signal in active_rows:
        ticker = str(signal.get("ticker") or "")
        active_counts[ticker] = active_counts.get(ticker, 0) + 1
    indicator_by_ticker = {
        str(row.get("sym") or "").strip().upper(): normalize_qmd_indicator_scanner_row(row)
        for row in (
            indicator_payload.get("rows", [])
            if isinstance(indicator_payload, dict)
            else []
        )
        if isinstance(row, dict) and str(row.get("sym") or "").strip()
    }
    halt_by_ticker = _active_halts_by_ticker(market_state_payload)
    rows = [
        {
            **row,
            **indicator_by_ticker.get(str(row.get("ticker") or ""), {}),
            **strongest_by_ticker.get(str(row.get("ticker") or ""), {}),
            **halt_by_ticker.get(
                str(row.get("ticker") or ""),
                {
                    "market_is_halted": False,
                    "trading_status": "trading",
                },
            ),
            "active_signal_count": active_counts.get(str(row.get("ticker") or ""), 0),
            "signal_rank_score": float_value(
                strongest_by_ticker.get(str(row.get("ticker") or ""), {}).get(
                    "signal_rank_score"
                )
            ),
        }
        for row in rows
    ]
    payload = qmd_scanner_payload(
        rows,
        snapshot_payload if isinstance(snapshot_payload, dict) else {},
        row_limit,
        source="scanner",
    )
    payload["signal_rows"] = [
        normalize_qmd_market_signal(row)
        for row in (
            signal_event_payload.get("rows", [])
            if isinstance(signal_event_payload, dict)
            else []
        )
        if isinstance(row, dict)
    ]
    payload["signal_row_count"] = len(payload["signal_rows"])
    payload["computation_scope"] = "core_scan"
    payload["included_enrichments"] = sorted(requested)
    return payload


def _active_halts_by_ticker(payload: Any) -> dict[str, dict[str, Any]]:
    """Project only exchange halt conditions, not broader tradability warnings."""

    if not isinstance(payload, dict):
        return {}
    result: dict[str, dict[str, Any]] = {}
    for event in payload.get("active") or []:
        if not isinstance(event, dict) or str(event.get("event_type") or "") != "condition_halt":
            continue
        ticker = str(event.get("ticker") or "").strip().upper()
        if not ticker:
            continue
        current = result.get(ticker)
        source_at = str(event.get("source_event_ts_utc") or "")
        if current is not None and str(current.get("halt_source_at") or "") >= source_at:
            continue
        result[ticker] = {
            "market_is_halted": True,
            "trading_status": "halted",
            "halt_reason": str(event.get("block_reason") or "condition_halt"),
            "halt_started_at": event.get("event_start_utc"),
            "halt_source_at": event.get("source_event_ts_utc"),
            "halt_source_conditions": list(event.get("source_conditions") or []),
        }
    return result


def qmd_historical_scanner_snapshot(
    *,
    as_of: str,
    lookback_minutes: int = 30,
    timeout_seconds: float = 120.0,
) -> dict[str, Any]:
    cutoff = _validate_window_timestamp("as_of", as_of).astimezone(timezone.utc)
    lookback = max(1, min(int(lookback_minutes), 390))
    response = qmd_product_request(
        QmdProductRequest(
            "scanner",
            authority="history",
            start=(cutoff - timedelta(minutes=lookback)).isoformat(),
            end=cutoff.isoformat(),
            as_of=cutoff.isoformat(),
            timeout_seconds=timeout_seconds,
        )
    )
    if not isinstance(response.payload, dict):
        raise RuntimeError("QMD History Scanner response was not an object")
    return response.payload


def qmd_historical_scanner_market_snapshot(
    *,
    as_of: str,
    lookback_minutes: int = 15,
    timeout_seconds: float = 85.0,
) -> dict[str, Any]:
    """Return the lightweight causal cross-section owned by QMD History.

    This endpoint aggregates only current market fields in ClickHouse. It is
    intentionally separate from scanner-derived, whose ordered event replay
    computes indicators and signals and may continue asynchronously.
    """

    cutoff = _validate_window_timestamp("as_of", as_of).astimezone(timezone.utc)
    lookback = max(5, min(int(lookback_minutes), 120))
    payload = qmd_history_get_json(
        "/snapshot/scanner-market",
        {
            "as_of": cutoff.isoformat(),
            "end": cutoff.isoformat(),
            "start": (cutoff - timedelta(minutes=lookback)).isoformat(),
        },
        timeout=timeout_seconds,
    )
    if not isinstance(payload, dict):
        raise RuntimeError("QMD History Scanner market response was not an object")
    return payload


def qmd_market_signals(
    symbol: str,
    *,
    include_history: bool = False,
    row_limit: int = 250,
) -> dict[str, Any]:
    ticker = symbol.strip().upper()
    if not ticker:
        raise ValueError("symbol is required for QMD market signals.")
    path = "/snapshot/signal-events" if include_history else "/snapshot/signals"
    payload = qmd_get_json(path, {"limit": row_limit}, timeout=3)
    source_rows = payload.get("rows", []) if isinstance(payload, dict) else []
    rows = [
        normalize_qmd_market_signal(row)
        for row in source_rows
        if isinstance(row, dict) and str(row.get("ticker") or "").strip().upper() == ticker
    ]
    return {
        "as_of": payload.get("as_of") if isinstance(payload, dict) else None,
        "last_sequence": int(payload.get("last_sequence") or 0) if isinstance(payload, dict) else 0,
        "mode": "lifecycle_history" if include_history else "active",
        "row_count": len(rows),
        "rows": rows,
        "source": "qmd-gateway",
        "ticker": ticker,
    }


def qmd_scanner_indicators(
    *,
    timeframe: str = "1s",
    row_limit: int = 25_000,
    fields: Iterable[str] | None = None,
) -> list[dict[str, Any]]:
    requested_fields = sorted({str(value).strip() for value in fields or [] if str(value).strip()})
    params: dict[str, Any] = {
        "limit": max(1, min(int(row_limit), 25_000)),
        "timeframe": timeframe,
    }
    if requested_fields:
        params["fields"] = ",".join(requested_fields)
    payload = qmd_get_json(
        "/snapshot/scanner-indicators",
        params,
        timeout=3,
    )
    source_rows = payload.get("rows") or [] if isinstance(payload, dict) else []
    return [
        normalize_qmd_indicator_scanner_row(row)
        for row in source_rows
        if isinstance(row, dict)
    ]


def qmd_scanner_macro_bars(
    *, timeframe: str, row_limit: int = 25_000
) -> list[dict[str, Any]]:
    if timeframe not in {"1d", "1w", "1mo"}:
        raise ValueError(f"Unsupported QMD scanner macro interval: {timeframe}")
    payload = qmd_get_json(
        "/snapshot/scanner-macro-bars",
        {"limit": max(1, min(int(row_limit), 25_000)), "timeframe": timeframe},
        timeout=3,
    )
    grouped: dict[str, list[dict[str, Any]]] = {}
    for row in payload.get("rows") or [] if isinstance(payload, dict) else []:
        if not isinstance(row, dict):
            continue
        ticker = str(row.get("ticker") or "").strip().upper()
        if ticker:
            grouped.setdefault(ticker, []).append(row)
    projected: list[dict[str, Any]] = []
    for ticker, rows in grouped.items():
        rows.sort(key=lambda row: str(row.get("bar_end") or ""))
        current = rows[-1]
        previous_close = float_value(rows[-2].get("close")) if len(rows) > 1 else 0.0
        close = float_value(current.get("close"))
        high = float_value(current.get("high"))
        low = float_value(current.get("low"))
        projected.append({
            **current,
            "ticker": ticker,
            "sym": ticker,
            "volume": float_value(current.get("size_sum")),
            "trade_count": int(current.get("event_count") or 0),
            "price_change_pct": ((close / previous_close) - 1.0) * 100.0 if previous_close > 0 else None,
            "high_low_range_pct": ((high / low) - 1.0) * 100.0 if low > 0 else None,
            "indicator_interval": timeframe,
            "indicator_timeframe": timeframe,
            "indicator_as_of": str(current.get("bar_end") or ""),
            "indicator_type": "qmd",
            "indicator_producer": "qmd",
            "indicator_input_basis": "bar_derived",
            "indicator_calculation_window": timeframe,
            "indicator_evaluation_mode": "developing",
            "indicator_update_trigger": "market_event",
            "indicator_publication_cadence": "on_change",
        })
    return projected


def qmd_bars(symbol: str, *, timeframe: str = "1m", row_limit: int = 500) -> dict[str, Any]:
    if not symbol.strip():
        raise ValueError("symbol is required for QMD bars.")
    payload = qmd_get_json(f"/snapshot/bars/{urllib.parse.quote(symbol.strip().upper())}", {"timeframe": timeframe, "limit": row_limit}, timeout=3)
    return payload if isinstance(payload, dict) else {"ticker": symbol.upper(), "timeframe": timeframe, "history": [], "current": None}


def qmd_intraday_bar_history(
    symbol: str,
    *,
    timeframe: str,
    start_date: str,
    end_date: str,
    before_event_timestamp_us: int | None = None,
    row_limit: int = 20_000,
) -> dict[str, Any]:
    """Read durable recent-session bars from QMD Live's q_live authority."""

    ticker = symbol.strip().upper()
    if not ticker:
        raise ValueError("symbol is required for QMD intraday bar history.")
    params: dict[str, Any] = {
        "timeframe": timeframe,
        "start_date": start_date,
        "end_date": end_date,
        "limit": max(1, min(int(row_limit), 50_000)),
    }
    if before_event_timestamp_us is not None:
        params["before_event_timestamp_us"] = max(0, int(before_event_timestamp_us))
    payload = qmd_get_json(
        f"/snapshot/intraday-bar-history/{urllib.parse.quote(ticker)}",
        params,
        timeout=15,
    )
    if not isinstance(payload, dict):
        raise RuntimeError("QMD Live intraday bar history returned an invalid envelope")
    return payload


def qmd_persisted_indicators(
    symbol: str,
    *,
    timeframe: str,
    start_date: str,
    end_date: str,
    row_limit: int = 20_000,
) -> dict[str, Any]:
    """Read certified recent closed-bar indicators without activating computation."""

    ticker = symbol.strip().upper()
    if not ticker:
        raise ValueError("symbol is required for persisted QMD indicators.")
    payload = qmd_get_json(
        f"/snapshot/persisted-indicators/{urllib.parse.quote(ticker)}",
        {
            "timeframe": timeframe,
            "start_date": start_date,
            "end_date": end_date,
            "limit": max(1, min(int(row_limit), 50_000)),
        },
        timeout=15,
    )
    if not isinstance(payload, dict):
        raise RuntimeError("QMD Live persisted indicators returned an invalid envelope")
    return payload


def qmd_compact_event_page(
    symbol: str,
    *,
    after_arrival_sequence: int = 0,
    row_limit: int = 250,
) -> dict[str, Any]:
    """Return one bounded, versioned live compact-event continuation page."""
    ticker = symbol.strip().upper()
    if not ticker:
        raise ValueError("symbol is required for QMD compact events.")
    payload = qmd_product_request(
        QmdProductRequest(
            "compact_events",
            ticker=ticker,
            limit=row_limit,
            after_sequence=max(0, int(after_arrival_sequence)),
        )
    ).payload
    if not isinstance(payload, dict):
        raise RuntimeError("QMD live compact-event page returned an invalid envelope")
    rows = [row for row in payload.get("events") or [] if isinstance(row, dict)]
    return {**payload, "events": rows}


def qmd_compact_events(symbol: str, *, row_limit: int = 250) -> list[dict[str, Any]]:
    """Compatibility projection of the stable live compact-event page."""
    ticker = symbol.strip().upper()
    if not ticker:
        raise ValueError("symbol is required for QMD compact events.")
    payload = qmd_product_request(
        QmdProductRequest("compact_events", ticker=ticker, limit=row_limit)
    ).payload
    if not isinstance(payload, dict):
        return []
    return [row for row in payload.get("events") or [] if isinstance(row, dict)]


def qmd_chart_bars(symbol: str, *, timeframe: str = "1m", row_limit: int = 500) -> dict[str, Any]:
    response = qmd_product_request(
        QmdProductRequest(
            "chart",
            ticker=symbol,
            timeframe=timeframe,
            limit=row_limit,
        )
    )
    payload = response.payload
    if timeframe in MACRO_QMD_TIMEFRAMES:
        return normalize_qmd_macro_bar_snapshot(payload, symbol=symbol, timeframe=timeframe)
    if timeframe in ENRICHED_QMD_TIMEFRAMES:
        return payload if isinstance(payload, dict) else {"ticker": symbol.upper(), "timeframe": timeframe, "history": [], "current": None}
    return normalize_qmd_family_bar_snapshot(payload, symbol=symbol, timeframe=timeframe)


def qmd_macro_bars(symbol: str, *, timeframe: str, row_limit: int = 500) -> dict[str, Any]:
    if not symbol.strip():
        raise ValueError("symbol is required for QMD macro bars.")
    payload = qmd_get_json(
        f"/snapshot/macro-bars/{urllib.parse.quote(symbol.strip().upper())}",
        {"limit": row_limit, "timeframe": timeframe},
        timeout=3,
    )
    return normalize_qmd_macro_bar_snapshot(payload, symbol=symbol, timeframe=timeframe)


def normalize_qmd_macro_bar_snapshot(payload: Any, *, symbol: str, timeframe: str) -> dict[str, Any]:
    rows = payload.get("rows", []) if isinstance(payload, dict) else []
    normalized = [
        normalize_qmd_macro_bar(row, timeframe=timeframe)
        for row in rows
        if isinstance(row, dict) and is_qmd_trade_price_bar(row)
    ]
    normalized.sort(key=lambda row: str(row.get("bar_start") or ""))
    current = normalized[-1] if normalized and not normalized[-1]["is_closed"] else None
    return {
        "ticker": symbol.strip().upper(),
        "timeframe": timeframe,
        "history": normalized[:-1] if current is not None else normalized,
        "current": current,
    }


def normalize_qmd_macro_bar(row: dict[str, Any], *, timeframe: str) -> dict[str, Any]:
    return {
        "schema_version": row.get("schema_version"),
        "session_date": row.get("session_date"),
        "timeframe": timeframe,
        "sym": str(row.get("ticker") or "").upper(),
        "bar_start": row.get("bar_start"),
        "bar_end": row.get("bar_end"),
        "is_closed": row.get("state") != "partial",
        "open": row.get("open"),
        "high": row.get("high"),
        "low": row.get("low"),
        "close": row.get("close"),
        "volume": row.get("size_sum"),
        "vwap": None,
    }


def normalize_qmd_family_bar_snapshot(payload: Any, *, symbol: str, timeframe: str) -> dict[str, Any]:
    rows = payload.get("rows", []) if isinstance(payload, dict) else []
    normalized = [
        normalize_qmd_family_bar(row, timeframe=timeframe)
        for row in rows
        if isinstance(row, dict) and is_qmd_trade_price_bar(row)
    ]
    normalized.sort(key=lambda row: row["bar_start"])
    current = normalized[-1] if normalized and not normalized[-1]["is_closed"] else None
    history = normalized[:-1] if current is not None else normalized
    return {
        "ticker": symbol.strip().upper(),
        "timeframe": timeframe,
        "history": history,
        "current": current,
    }


def normalize_qmd_family_bar(row: dict[str, Any], *, timeframe: str) -> dict[str, Any]:
    return {
        "schema_version": row.get("schema_version"),
        "session_date": row.get("local_date"),
        "timeframe": timeframe,
        "sym": str(row.get("ticker") or "").upper(),
        "bar_start": row.get("bar_start"),
        "bar_end": row.get("bar_end"),
        "is_closed": row.get("state") != "partial",
        "open": row.get("open"),
        "high": row.get("high"),
        "low": row.get("low"),
        "close": row.get("close"),
        "volume": row.get("size_sum"),
        "vwap": None,
    }


def is_qmd_trade_price_bar(row: dict[str, Any]) -> bool:
    if row.get("bar_family") != "trade":
        return False
    try:
        open_price, high, low, close = (
            float(row[field]) for field in ("open", "high", "low", "close")
        )
    except (KeyError, TypeError, ValueError):
        return False
    return (
        all(math.isfinite(value) and value > 0 for value in (open_price, high, low, close))
        and high >= max(open_price, close)
        and low <= min(open_price, close)
        and high >= low
    )


def qmd_indicator_capabilities(indicator_columns: str | Iterable[str] | None) -> list[str]:
    if isinstance(indicator_columns, str):
        columns = {value.strip().lower() for value in indicator_columns.split(",") if value.strip()}
    else:
        columns = {str(value).strip().lower() for value in (indicator_columns or ()) if str(value).strip()}
    capabilities: list[str] = []
    if columns.intersection({"rsi_14", "macd_line", "macd_signal", "macd_histogram", "price_vs_vwap_pct"}):
        capabilities.append("momentum_core")
    if any(
        value.startswith(("ema_", "sma_", "close_sma_", "price_vs_ema", "trend_score"))
        for value in columns
    ):
        capabilities.append("trend_moving_averages")
    if any(value.startswith(("atr_", "bollinger_", "realized_volatility")) for value in columns):
        capabilities.append("volatility_core")
    if any(value.startswith("flow_structure_composite") for value in columns):
        capabilities.append("flow_structure_composite")
    # Base-bar values (VWAP, return, and volume) are already maintained by the
    # universal/core funnel. A scoped core_bars lease keeps the request
    # contract explicit without materializing unrelated derived families.
    return capabilities or ["core_bars"]


def qmd_indicators(
    symbol: str,
    *,
    timeframe: str = "1m",
    row_limit: int = 500,
    indicator_columns: str | Iterable[str] | None = None,
) -> dict[str, Any]:
    if not symbol.strip():
        raise ValueError("symbol is required for QMD indicators.")
    ticker = symbol.strip().upper()
    requested_fields = (
        [value.strip() for value in indicator_columns.split(",") if value.strip()]
        if isinstance(indicator_columns, str)
        else [str(value).strip() for value in (indicator_columns or ()) if str(value).strip()]
    )
    target_id = f"chart:{ticker}:{timeframe}"
    capabilities = qmd_indicator_capabilities(indicator_columns)
    parameter_hash = hashlib.sha256(
        json.dumps(
            {"capabilities": capabilities, "timeframes": [timeframe]},
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
    ).hexdigest()
    lineage = causal_identity(
        correlation_seed=target_id,
        causation_seed=f"chart-request:{ticker}:{timeframe}",
    )
    # Structure focus activation may causally advance a persisted checkpoint
    # through QMD History. Match the gateway's 60-second advancement budget
    # instead of aborting the request after the ordinary 3-second read budget.
    qmd_put_json(
        "/computation-targets",
        {
            "target_id": target_id,
            "owner": "backend.chart",
            "scope": "request",
            "tickers": [ticker],
            "capabilities": capabilities,
            "timeframes": [timeframe],
            "parameter_hash": parameter_hash,
            "anchor": "new_york_session",
            "source_revision": "advancing_live",
            "ttl_seconds": 300,
            **lineage,
        },
        timeout=70,
    )
    payload = qmd_get_json(
        f"/snapshot/indicators/{urllib.parse.quote(ticker)}",
        {
            "timeframe": timeframe,
            "limit": row_limit,
            "fields": ",".join(dict.fromkeys(requested_fields)) or None,
        },
        timeout=3,
    )
    return payload if isinstance(payload, dict) else {"ticker": symbol.upper(), "timeframe": timeframe, "history": [], "current": None, "tick": None}


def qmd_catalogs() -> dict[str, Any]:
    paths = {
        "capability_catalog": "/capability-catalog",
        "definition_catalog": "/definition-catalog",
        "indicator_catalog": "/indicator-catalog",
        "signal_catalog": "/signal-catalog",
    }
    with ThreadPoolExecutor(max_workers=len(paths)) as executor:
        futures = {
            key: executor.submit(qmd_get_json, path, timeout=3)
            for key, path in paths.items()
        }
        catalogs = {key: future.result() for key, future in futures.items()}
    normalized = {
        key: value if isinstance(value, list) else []
        for key, value in catalogs.items()
        if key != "definition_catalog"
    }
    definition_catalog = catalogs.get("definition_catalog")
    normalized["definition_catalog"] = (
        definition_catalog if isinstance(definition_catalog, dict) else {}
    )
    content_hash = hashlib.sha256(
        json.dumps(normalized, separators=(",", ":"), sort_keys=True).encode("utf-8")
    ).hexdigest()
    return normalized | {
        "provider": "qmd-gateway",
        "authority": "qmd_runtime_catalog",
        "schema_version": 1,
        "content_hash": content_hash,
    }


def market_clock_projection(clock: dict[str, Any]) -> dict[str, Any]:
    """Project QMD's clock once and derive deterministic calendar components."""

    projection = {
        "clock_observed_at": clock.get("observed_at"),
        "utc_date": clock.get("utc_date"),
        "utc_time": clock.get("utc_time"),
        "exchange_date": clock.get("exchange_date"),
        "market_time": clock.get("exchange_time"),
        "trading_date": clock.get("trading_date"),
        "market_timezone": clock.get("timezone"),
        "market_weekday": clock.get("weekday"),
        "session_id": clock.get("session_id"),
        "expected_previous_session_date": clock.get("previous_session_date"),
        "session_phase": clock.get("session_phase"),
        "session_open_at": clock.get("session_open_at"),
        "session_close_at": clock.get("session_close_at"),
        "minutes_since_open": clock.get("minutes_since_open"),
        "minutes_until_close": clock.get("minutes_until_close"),
        "is_trading_day": clock.get("is_trading_day"),
        "is_early_close": clock.get("is_early_close"),
        "market_status": clock.get("market_status"),
        "market_is_open": clock.get("market_is_open"),
        "market_feed_status": clock.get("market_feed_status"),
    }
    try:
        exchange_date = datetime.strptime(str(clock.get("exchange_date") or ""), "%Y-%m-%d").date()
    except ValueError:
        exchange_date = None
    try:
        exchange_time = datetime.strptime(str(clock.get("exchange_time") or "").split(".", 1)[0], "%H:%M:%S").time()
    except ValueError:
        exchange_time = None
    if exchange_date is not None:
        tomorrow = exchange_date + timedelta(days=1)
        iso = exchange_date.isocalendar()
        projection.update({
            "calendar_year": exchange_date.year,
            "calendar_quarter": (exchange_date.month - 1) // 3 + 1,
            "month_number": exchange_date.month,
            "month_name": exchange_date.strftime("%B"),
            "iso_week": iso.week,
            "day_of_month": exchange_date.day,
            "day_of_year": int(exchange_date.strftime("%j")),
            "weekday_number": iso.weekday,
            "is_weekend": iso.weekday >= 6,
            "is_month_start": exchange_date.day == 1,
            "is_month_end": tomorrow.month != exchange_date.month,
            "is_quarter_start": exchange_date.day == 1 and exchange_date.month in {1, 4, 7, 10},
            "is_quarter_end": tomorrow.month in {1, 4, 7, 10} and tomorrow.day == 1,
        })
    if exchange_time is not None:
        projection.update({
            "market_hour": exchange_time.hour,
            "market_minute": exchange_time.minute,
            "market_second": exchange_time.second,
            "minutes_since_midnight": exchange_time.hour * 60 + exchange_time.minute,
        })
    return projection


def historical_market_clock_projection(as_of: datetime) -> dict[str, Any]:
    """Return clock fields that are safe to derive without inventing a holiday state."""

    if as_of.tzinfo is None:
        raise ValueError("Historical market clock must be timezone-aware")
    utc = as_of.astimezone(timezone.utc)
    local = utc.astimezone(ZoneInfo("America/New_York"))
    return market_clock_projection({
        "observed_at": utc.isoformat(),
        "utc_date": utc.date().isoformat(),
        "utc_time": utc.strftime("%H:%M:%S.%f")[:-3],
        "exchange_date": local.date().isoformat(),
        "exchange_time": local.strftime("%H:%M:%S"),
        "trading_date": local.date().isoformat(),
        "timezone": "America/New_York",
        "weekday": local.strftime("%A"),
        "session_id": local.date().isoformat(),
    })


def qmd_scanner_payload(rows: list[dict[str, Any]], raw_payload: dict[str, Any], row_limit: int, *, source: str) -> dict[str, Any]:
    now = datetime.now(timezone.utc)
    rows = rows[: max(1, min(int(row_limit or 250), 25_000))]
    clock = dict(raw_payload.get("market_clock") or {})
    clock_projection = market_clock_projection(clock)
    rows = [{**row, **clock_projection} for row in rows]
    return {
        "provider": "qmd-gateway",
        "source": source,
        "session_date": str(clock.get("trading_date") or now.date().isoformat()),
        "market_time": str(clock.get("exchange_time") or now.strftime("%H:%M")),
        "market_clock": clock,
        "rows": rows,
        "row_count": len(rows),
        "market_rows": rows,
        "market_row_count": len(rows),
        "status": {
            "as_of": raw_payload.get("as_of"),
            "base_url": qmd_base_url().rstrip("/"),
            "total_symbols": raw_payload.get("total_symbols"),
            "source": source,
        },
    }


def normalize_qmd_symbol_snapshot(row: dict[str, Any]) -> dict[str, Any]:
    ticker = str(row.get("ticker") or "").upper()
    last_price = float_value(row.get("last_price"))
    bid = float_value(row.get("bid"))
    ask = float_value(row.get("ask"))
    spread = float_value(row.get("spread"))
    spread_bps = spread / last_price * 10_000 if spread > 0 and last_price > 0 else 0.0
    trade_rate_10s = float_value(row.get("trade_rate_10s"))
    trade_rate_60s = float_value(row.get("trade_rate_60s"))
    day_dollar_volume = float_value(row.get("day_dollar_volume"))
    day_volume = float_value(row.get("day_volume"))
    liquidity_score = float_value(row.get("liquidity_score"))
    liquidity_rank = int(float_value(row.get("liquidity_rank"))) or None
    previous_close = optional_float(row.get("previous_close"))
    return {
        "ticker": ticker,
        "symbol": ticker,
        "bar_time_market": str(row.get("last_event_ts") or ""),
        "market_event_at": str(row.get("last_event_ts") or ""),
        "market_event_age_ms": row.get("event_age_ms"),
        "market_quality_state": row.get("quality_state"),
        "market_quality_flags": row.get("quality_flags"),
        "market_degradation_reason": row.get("degradation_reason"),
        "current_open": last_price,
        "last_close": last_price,
        "bid": bid or None,
        "ask": ask or None,
        "spread_bps_abs": spread_bps or None,
        "spread_bps": spread_bps or None,
        "last_day_volume_so_far": day_volume,
        "last_day_dollar_volume_so_far": day_dollar_volume,
        "last_transactions": int(float_value(row.get("day_trade_count"))),
        "trade_rate_10s": trade_rate_10s,
        "trade_rate_60s": trade_rate_60s,
        "trade_accel_10s_60s": trade_rate_10s - trade_rate_60s,
        "provider": "qmd-gateway",
        "live_priority": liquidity_score,
        "last_price": last_price,
        "previous_close": previous_close,
        "change_pct": (
            (last_price / previous_close - 1) * 100
            if previous_close is not None and previous_close > 0 and last_price > 0
            else None
        ),
        "volume": day_volume,
        "session_dollar_volume": day_dollar_volume,
        "vwap": day_dollar_volume / day_volume if day_volume > 0 else None,
        "liquidity_rank": liquidity_rank,
        "liquidity_score": liquidity_score,
        "liquidity_eligible": bool(row.get("liquidity_eligible")),
        "liquidity_eligibility_reasons": list(row.get("liquidity_eligibility_reasons") or []),
    }


def normalize_qmd_scanner_primitive(row: dict[str, Any]) -> dict[str, Any]:
    close = float_value(row.get("close"))
    score = float_value(row.get("score"))
    return {
        "ticker": str(row.get("ticker") or "").upper(),
        "bar_time_market": str(row.get("detected_at") or ""),
        "timeframe": str(row.get("timeframe") or ""),
        "current_open": close,
        "last_close": close,
        "last_vwap": float_value(row.get("vwap")),
        "spread_bps_abs": optional_float(row.get("spread_bps")),
        "scanner_score": score,
        "signal_type": str(row.get("primitive_key") or ""),
        "market_state": str(row.get("side_bias") or ""),
        "live_reasons": str(row.get("trigger_reason") or ""),
        "live_risks": str(row.get("reject_reason") or ""),
        "last_day_volume_so_far": float_value(row.get("volume")),
        "last_day_dollar_volume_so_far": float_value(row.get("dollar_volume")),
        "trade_rate_10s": float_value(row.get("trade_rate")),
        "quote_rate_10s": float_value(row.get("quote_rate")),
        "tape_imbalance": float_value(row.get("tape_imbalance")),
        "liquidity_score": float_value(row.get("liquidity_score")),
        "provider": "qmd-gateway",
        "live_priority": score,
    }


def normalize_qmd_market_signal(row: dict[str, Any]) -> dict[str, Any]:
    evidence = row.get("evidence") if isinstance(row.get("evidence"), dict) else {}
    clock = row.get("clock") if isinstance(row.get("clock"), dict) else {}
    score = float_value(row.get("score"))
    rank_score = (
        float_value(row.get("rank_score"))
        if row.get("rank_score") is not None
        else abs(score)
    )
    confidence = float_value(row.get("confidence"))
    return {
        "ticker": str(row.get("ticker") or "").upper(),
        "bar_time_market": str(row.get("effective_at") or ""),
        "event_time": str(row.get("effective_at") or ""),
        "timeframe": str(row.get("working_timeframe") or ""),
        "working_timeframe": str(row.get("working_timeframe") or ""),
        "confirmation_timeframe": row.get("confirmation_timeframe"),
        "current_open": float_value(evidence.get("close")),
        "last_close": float_value(evidence.get("close")),
        "last_vwap": float_value(evidence.get("vwap")),
        "spread_bps_abs": optional_float(evidence.get("spread_bps")),
        "signal_id": str(row.get("signal_id") or ""),
        "signal_event_id": str(row.get("event_id") or ""),
        "signal_version": int(row.get("signal_version") or 1),
        "signal_type": str(row.get("signal_key") or ""),
        "signal_domain": str(row.get("domain") or "market"),
        "signal_producer": str(row.get("producer") or "qmd"),
        "signal_state": str(row.get("state") or ""),
        "direction": str(row.get("direction") or "neutral"),
        "market_state": str(row.get("direction") or "neutral"),
        "signal_score": score,
        "signal_rank_score": rank_score,
        "signal_confidence": confidence,
        "scanner_score": rank_score,
        "live_reasons": str(row.get("trigger_reason") or ""),
        "live_risks": str(row.get("resolution_reason") or ""),
        "evidence": str(row.get("trigger_reason") or ""),
        "source": "QMD market signal",
        "last_day_volume_so_far": float_value(evidence.get("volume")),
        "last_day_dollar_volume_so_far": float_value(evidence.get("dollar_volume")),
        "trade_rate_10s": float_value(evidence.get("trade_rate")),
        "quote_rate_10s": float_value(evidence.get("quote_rate")),
        "tape_imbalance": float_value(evidence.get("tape_imbalance")),
        "liquidity_score": float_value(evidence.get("liquidity_score")),
        "provider": "qmd-gateway",
        "live_priority": rank_score,
        "input_basis": str(clock.get("input_basis") or "bar_derived"),
        "calculation_window": str(clock.get("calculation_window") or row.get("working_timeframe") or ""),
        "evaluation_mode": str(clock.get("evaluation_mode") or "closed_only"),
        "update_trigger": str(clock.get("update_trigger") or "bar_close"),
        "publication_cadence": str(clock.get("publication_cadence") or "bar_close"),
        "publication_interval_ms": clock.get("publication_interval_ms"),
    }


def normalize_qmd_indicator_scanner_row(row: dict[str, Any]) -> dict[str, Any]:
    payload = dict(row)
    payload.pop("qmd_structure_active_levels", None)
    payload.pop("qmd_structure_timeframe_states", None)
    payload.pop("qmd_structure_unified_levels", None)
    payload["ticker"] = str(row.get("sym") or "").strip().upper()
    payload["indicator_timeframe"] = str(row.get("timeframe") or "")
    payload["indicator_as_of"] = str(row.get("bar_end") or "")
    payload["indicator_type"] = "qmd"
    payload["indicator_producer"] = "qmd"
    payload["indicator_input_basis"] = "event_native"
    payload["indicator_calculation_window"] = str(row.get("timeframe") or "")
    payload["indicator_evaluation_mode"] = "closed_only"
    payload["indicator_update_trigger"] = "bar_close"
    payload["indicator_publication_cadence"] = "bar_close"
    payload["indicator_publication_interval_ms"] = None
    return payload


def optional_float(value: Any) -> float | None:
    number = float_value(value)
    return number if number else None


def float_value(value: Any) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return 0.0
    return number if number == number else 0.0


def safe_qmd_url(url: str) -> str:
    parsed = urllib.parse.urlsplit(url)
    return urllib.parse.urlunsplit((parsed.scheme, parsed.netloc, parsed.path, parsed.query, ""))
