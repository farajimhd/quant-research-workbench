from __future__ import annotations

import json
import os
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any


def bar_gpt_base_url() -> str:
    return os.environ.get("BAR_GPT_SERVICE_URL", "http://127.0.0.1:8805").rstrip("/")


_SCOPE_LOCK = threading.RLock()
_SCOPE_CACHE: dict[str, dict[str, Any]] = {}
_SERVICE_BACKOFF_UNTIL = 0.0


def bar_gpt_health(timeout: float = 1.5) -> dict[str, Any]:
    return _request("GET", "/health", timeout=timeout)


def bar_gpt_predictions(ticker: str = "", limit: int = 100, timeout: float = 2.0) -> dict[str, Any]:
    query = urllib.parse.urlencode({"ticker": ticker, "limit": max(1, min(limit, 10_000))})
    return _request("GET", f"/predictions?{query}", timeout=timeout)


def bar_gpt_configuration(timeout: float = 2.0) -> dict[str, Any]:
    return _request("GET", "/configuration", timeout=timeout)


def update_bar_gpt_configuration(payload: dict[str, Any], timeout: float = 5.0) -> dict[str, Any]:
    return _request("PUT", "/configuration", payload=payload, timeout=timeout)


def publish_bar_gpt_scope(
    scope_id: str,
    *,
    mode: str,
    tickers: list[str],
    watchlist_ids: list[str] | None = None,
    trigger_mode: str = "auto",
    clock_us: int | None = None,
    revision: int = 1,
    ttl_ms: int = 30_000,
    source: str = "application",
    timeout: float = 0.75,
) -> dict[str, Any]:
    global _SERVICE_BACKOFF_UNTIL
    payload = {
        "mode": mode,
        "trigger_mode": trigger_mode,
        "tickers": sorted({str(value).strip().upper() for value in tickers if str(value).strip()}),
        "watchlist_ids": sorted({str(value) for value in watchlist_ids or [] if str(value)}),
        "clock_us": clock_us,
        "revision": max(1, int(revision)),
        "ttl_ms": max(1_000, int(ttl_ms)),
        "source": source,
    }
    fingerprint = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    now = time.monotonic()
    with _SCOPE_LOCK:
        if now < _SERVICE_BACKOFF_UNTIL:
            return {"scope_id": scope_id, "status": "unavailable", "error": "BarGPT retry backoff is active"}
        cached = _SCOPE_CACHE.get(scope_id)
        if cached and cached["fingerprint"] == fingerprint and now - cached["attempted_at"] < 5.0:
            return dict(cached["result"])
    try:
        result = _request(
            "PUT",
            f"/scopes/{urllib.parse.quote(scope_id, safe='')}",
            payload=payload,
            timeout=timeout,
        )
    except RuntimeError as exc:
        result = {"scope_id": scope_id, "status": "unavailable", "error": str(exc)}
        with _SCOPE_LOCK:
            _SERVICE_BACKOFF_UNTIL = time.monotonic() + 5.0
    with _SCOPE_LOCK:
        _SCOPE_CACHE[scope_id] = {
            "fingerprint": fingerprint,
            "attempted_at": now,
            "result": result,
        }
    return dict(result)


def remove_bar_gpt_scope(scope_id: str, timeout: float = 2.0) -> dict[str, Any]:
    return _request("DELETE", f"/scopes/{urllib.parse.quote(scope_id, safe='')}", timeout=timeout)


def advance_bar_gpt_scope(
    scope_id: str,
    *,
    mode: str,
    tickers: list[str],
    watchlist_ids: list[str] | None = None,
    clock_us: int,
    revision: int = 1,
    ttl_ms: int = 60_000,
    source: str = "application",
    timeout: float = 120.0,
) -> dict[str, Any]:
    payload = {
        "mode": mode,
        "trigger_mode": "manual",
        "tickers": sorted({str(value).strip().upper() for value in tickers if str(value).strip()}),
        "watchlist_ids": sorted({str(value) for value in watchlist_ids or [] if str(value)}),
        "clock_us": int(clock_us),
        "revision": max(1, int(revision)),
        "ttl_ms": max(1_000, int(ttl_ms)),
        "source": source,
    }
    return _request(
        "POST",
        f"/scopes/{urllib.parse.quote(scope_id, safe='')}/advance",
        payload=payload,
        timeout=timeout,
    )


def _request(
    method: str,
    path: str,
    *,
    payload: dict[str, Any] | None = None,
    timeout: float,
) -> dict[str, Any]:
    request = urllib.request.Request(
        bar_gpt_base_url() + path,
        data=(json.dumps(payload, separators=(",", ":")).encode("utf-8") if payload is not None else None),
        headers={"Content-Type": "application/json"},
        method=method,
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            value = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"BarGPT HTTP {exc.code}: {detail}") from exc
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise RuntimeError(f"BarGPT service unavailable: {exc}") from exc
    if not isinstance(value, dict):
        raise RuntimeError("BarGPT response is not a JSON object")
    return value
