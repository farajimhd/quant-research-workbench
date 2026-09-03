from __future__ import annotations

import json
import ssl
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from typing import Any, Iterable
from urllib import error, parse, request


RETRY_HTTP_CODES = {408, 425, 429, 500, 502, 503, 504}


@dataclass(frozen=True, slots=True)
class MassiveTickerResult:
    tickers: list[dict[str, Any]]
    pages: int
    saturated: bool


@dataclass(frozen=True, slots=True)
class MassiveTickerEventsResult:
    identifier: str
    name: str
    events: list[dict[str, Any]]
    status: str
    request_id: str


class MassiveReferenceClient:
    def __init__(self, *, base_url: str, api_key: str, page_limit: int, max_pages: int) -> None:
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key.strip()
        self.page_limit = min(max(1, int(page_limit)), 1_000)
        self.max_pages = max(1, int(max_pages))
        if not self.api_key:
            raise RuntimeError("MASSIVE_API_KEY is required for Massive reference sync")

    def fetch_active_us_stock_tickers(self) -> MassiveTickerResult:
        return self.fetch_us_stock_tickers(active=True)

    def fetch_us_stock_tickers(self, *, active: bool) -> MassiveTickerResult:
        params = {
            "market": "stocks",
            "locale": "us",
            "active": "true" if active else "false",
            "limit": str(self.page_limit),
            "sort": "ticker",
            "order": "asc",
            "apiKey": self.api_key,
        }
        next_url: str | None = self.base_url + "/v3/reference/tickers?" + parse.urlencode(params)
        pages = 0
        rows: list[dict[str, Any]] = []
        while next_url and pages < self.max_pages:
            pages += 1
            payload = fetch_json(next_url, user_agent="quant-reference-gateway-massive/1.0")
            for item in payload.get("results") or []:
                if isinstance(item, dict):
                    rows.append(item)
            raw_next = payload.get("next_url")
            next_url = append_api_key(str(raw_next), self.api_key) if raw_next else None
        return MassiveTickerResult(tickers=rows, pages=pages, saturated=bool(next_url))

    def fetch_ticker_overview(self, ticker: str) -> dict[str, Any]:
        url = self.base_url + "/v3/reference/tickers/" + parse.quote(ticker, safe="") + "?" + parse.urlencode({"apiKey": self.api_key})
        payload = fetch_json(url, user_agent="quant-reference-gateway-massive/1.0", allow_not_found=True)
        result = payload.get("results")
        return result if isinstance(result, dict) else {}

    def fetch_ticker_events(self, identifier: str) -> MassiveTickerEventsResult:
        clean_identifier = str(identifier or "").strip()
        if not clean_identifier:
            raise ValueError("ticker-event identifier is required")
        url = (
            self.base_url
            + "/vX/reference/tickers/"
            + parse.quote(clean_identifier, safe="")
            + "/events?"
            + parse.urlencode({"types": "ticker_change", "apiKey": self.api_key})
        )
        payload = fetch_json(
            url,
            user_agent="quant-reference-gateway-massive/1.0",
            allow_not_found=True,
        )
        if not isinstance(payload, dict):
            raise RuntimeError("Massive ticker-events response is not an object")
        result = payload.get("results")
        if result is None:
            result = {}
        if not isinstance(result, dict):
            raise RuntimeError("Massive ticker-events results is not an object")
        raw_events = result.get("events") or []
        if not isinstance(raw_events, list):
            raise RuntimeError("Massive ticker-events events is not an array")
        events = [event for event in raw_events if isinstance(event, dict)]
        if len(events) != len(raw_events):
            raise RuntimeError("Massive ticker-events response contains a non-object event")
        return MassiveTickerEventsResult(
            identifier=clean_identifier,
            name=str(result.get("name") or "").strip(),
            events=events,
            status=str(payload.get("status") or "").strip(),
            request_id=str(payload.get("request_id") or "").strip(),
        )


class IbkrReferenceClient:
    def __init__(self, *, base_url: str, timeout_seconds: int = 8) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout_seconds = timeout_seconds

    def search_stock_contracts(self, ticker: str) -> list[dict[str, Any]]:
        result = fetch_json(
            self.base_url + "/iserver/secdef/search",
            method="POST",
            payload={"symbol": ticker, "secType": "STK", "name": False},
            timeout=self.timeout_seconds,
            allow_self_signed=True,
            user_agent="quant-reference-gateway-ibkr/1.0",
        )
        rows = result if isinstance(result, list) else result.get("results", []) if isinstance(result, dict) else []
        return [row for row in rows if isinstance(row, dict)]

    def fetch_security_definitions(self, conids: Iterable[int | str]) -> list[dict[str, Any]]:
        normalized = sorted({int(value) for value in conids if str(value).strip().isdigit() and int(value) > 0})
        if not normalized:
            return []
        if len(normalized) > 200:
            raise ValueError("IBKR security-definition requests are limited to 200 conids")
        result = fetch_json(
            self.base_url + "/trsrv/secdef?" + parse.urlencode({"conids": ",".join(str(value) for value in normalized)}),
            timeout=self.timeout_seconds,
            allow_self_signed=True,
            user_agent="quant-reference-gateway-ibkr/1.0",
        )
        rows = result.get("secdef", []) if isinstance(result, dict) else []
        return [row for row in rows if isinstance(row, dict)]

    def fetch_all_stock_conids(self, exchange: str) -> list[dict[str, Any]]:
        result = fetch_json(
            self.base_url + "/trsrv/all-conids?" + parse.urlencode({"exchange": str(exchange).strip().upper()}),
            timeout=max(30, self.timeout_seconds),
            allow_self_signed=True,
            user_agent="quant-reference-gateway-ibkr/1.0",
        )
        return [row for row in result if isinstance(row, dict)] if isinstance(result, list) else []

    def auth_status(self) -> dict[str, Any]:
        result = fetch_json(
            self.base_url + "/iserver/auth/status",
            timeout=self.timeout_seconds,
            allow_self_signed=True,
            user_agent="quant-reference-gateway-ibkr/1.0",
        )
        return result if isinstance(result, dict) else {"raw_status": result}


def append_api_key(url: str, api_key: str) -> str:
    if "apiKey=" in url:
        return url
    return url + ("&" if "?" in url else "?") + parse.urlencode({"apiKey": api_key})


def fetch_json(
    url: str,
    *,
    method: str = "GET",
    payload: dict[str, Any] | None = None,
    timeout: int = 60,
    allow_self_signed: bool = False,
    allow_not_found: bool = False,
    user_agent: str,
) -> Any:
    body = None if payload is None else json.dumps(payload).encode("utf-8")
    headers = {"Accept": "application/json", "User-Agent": user_agent}
    if body is not None:
        headers["Content-Type"] = "application/json"
    req = request.Request(url, data=body, method=method, headers=headers)
    context = ssl._create_unverified_context() if allow_self_signed and url.startswith("https://") else None
    response_text = ""
    for attempt in range(1, 5):
        try:
            with request.urlopen(req, timeout=timeout, context=context) as response:  # noqa: S310
                response_text = response.read().decode("utf-8", errors="replace")
                break
        except error.HTTPError as exc:
            response_text = exc.read().decode("utf-8", errors="replace")
            if exc.code == 404 and allow_not_found:
                break
            if exc.code not in RETRY_HTTP_CODES or attempt >= 4:
                raise RuntimeError(f"{method} {safe_url(url)} failed with HTTP {exc.code}: {response_text[:500]}") from exc
            time.sleep(retry_sleep_seconds(exc, attempt))
        except (TimeoutError, error.URLError):
            if attempt >= 4:
                raise
            time.sleep(retry_sleep_seconds(None, attempt))
    if not response_text.strip():
        return {}
    return json.loads(response_text)


def retry_sleep_seconds(exc: error.HTTPError | None, attempt: int) -> float:
    retry_after = exc.headers.get("Retry-After", "") if exc is not None else ""
    parsed = parse_retry_after_seconds(retry_after)
    if parsed is not None:
        return min(300.0, parsed)
    return min(60.0, 1.0 * (2 ** (attempt - 1)))


def parse_retry_after_seconds(value: str) -> float | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        return float(text)
    except ValueError:
        try:
            parsed = parsedate_to_datetime(text)
        except Exception:
            return None
        return max(0.0, (parsed.astimezone(UTC) - datetime.now(UTC)).total_seconds())


def safe_url(url: str) -> str:
    parsed = parse.urlsplit(url)
    params = parse.parse_qsl(parsed.query, keep_blank_values=True)
    redacted = [(key, "redacted" if key.lower() in {"apikey", "api_key", "token"} else value) for key, value in params]
    return parse.urlunsplit((parsed.scheme, parsed.netloc, parsed.path, parse.urlencode(redacted), parsed.fragment))
