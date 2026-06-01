from __future__ import annotations

import json
import os
import ssl
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from dotenv import load_dotenv


REPO_ROOT = Path(__file__).resolve().parents[2]
NEW_YORK = ZoneInfo("America/New_York")
DEFAULT_IBKR_BASE_URL = "https://localhost:5000/v1/api"
DEFAULT_MASSIVE_BASE_URL = "https://api.massive.com"


@dataclass(frozen=True)
class LiveAccountConfig:
    account_type: str
    account_id: str


def load_live_env() -> None:
    for env_path in (Path.cwd() / ".env", REPO_ROOT / ".env"):
        if env_path.exists():
            load_dotenv(env_path, override=False)
    load_dotenv(override=False)


def configured_account(account_type: str) -> LiveAccountConfig:
    load_live_env()
    normalized = normalize_account_type(account_type)
    env_name = "IBKR_PAPER_ACCOUNT_ID" if normalized == "paper" else "IBKR_CASH_ACCOUNT_ID"
    return LiveAccountConfig(account_type=normalized, account_id=os.environ.get(env_name, "").strip())


def normalize_account_type(account_type: str) -> str:
    normalized = (account_type or "paper").strip().lower()
    if normalized not in {"paper", "cash"}:
        raise ValueError("account_type must be paper or cash")
    return normalized


def live_trading_preflight(account_type: str = "paper") -> dict[str, Any]:
    account = configured_account(account_type)
    massive = check_massive_connection()
    ibkr = check_ibkr_connection(account)
    checks = [massive, *ibkr["checks"]]
    ready = all(check["status"] == "ready" for check in checks)
    return {
        "ready": ready,
        "account_type": account.account_type,
        "account_id": mask_account_id(account.account_id),
        "checks": checks,
        "broker": {
            "base_url": ibkr_base_url(),
            "authenticated": ibkr["authenticated"],
            "selected_account": mask_account_id(account.account_id),
        },
        "data": {
            "provider": "massive",
            "base_url": massive_base_url(),
        },
    }


def check_massive_connection() -> dict[str, Any]:
    api_key = massive_api_key()
    if not api_key:
        return {
            "id": "massive_api_key",
            "label": "Massive API key",
            "status": "blocked",
            "message": "Set MASSIVE_API_KEY in .env.",
        }
    try:
        payload = massive_get_json("/v3/reference/tickers", {"market": "stocks", "active": "true", "limit": "1"}, timeout=8)
        count = len(payload.get("results") or [])
        return {
            "id": "massive_rest",
            "label": "Massive market data",
            "status": "ready" if count else "blocked",
            "message": "REST connection is ready." if count else "Massive REST responded without stock ticker data.",
        }
    except Exception as exc:
        return {
            "id": "massive_rest",
            "label": "Massive market data",
            "status": "blocked",
            "message": str(exc),
        }


def check_ibkr_connection(account: LiveAccountConfig) -> dict[str, Any]:
    checks: list[dict[str, Any]] = []
    if not account.account_id:
        checks.append(
            {
                "id": f"ibkr_{account.account_type}_account_env",
                "label": f"IBKR {account.account_type} account",
                "status": "blocked",
                "message": f"Set {'IBKR_PAPER_ACCOUNT_ID' if account.account_type == 'paper' else 'IBKR_CASH_ACCOUNT_ID'} in .env.",
            }
        )
        return {"authenticated": False, "checks": checks}

    authenticated = False
    try:
        status = ibkr_get_json("/iserver/auth/status", timeout=5)
        authenticated = bool(status.get("authenticated") or status.get("competing") is False and status.get("connected"))
        checks.append(
            {
                "id": "ibkr_auth",
                "label": "IBKR session",
                "status": "ready" if authenticated else "blocked",
                "message": "Authenticated Client Portal session is available." if authenticated else "Authenticate in Client Portal Gateway first.",
            }
        )
    except Exception as exc:
        checks.append(
            {
                "id": "ibkr_gateway",
                "label": "IBKR gateway",
                "status": "blocked",
                "message": str(exc),
            }
        )
        return {"authenticated": False, "checks": checks}

    try:
        accounts_payload = ibkr_get_json("/iserver/accounts", timeout=6)
        account_ids = ibkr_account_ids(accounts_payload)
        found = account.account_id in account_ids
        checks.append(
            {
                "id": "ibkr_account",
                "label": "IBKR account access",
                "status": "ready" if found else "blocked",
                "message": "Configured account is available." if found else "Configured account was not returned by IBKR.",
                "details": {"available_accounts": [mask_account_id(value) for value in account_ids]},
            }
        )
    except Exception as exc:
        checks.append(
            {
                "id": "ibkr_account",
                "label": "IBKR account access",
                "status": "blocked",
                "message": str(exc),
            }
        )

    try:
        summary = ibkr_get_json(f"/portfolio/{urllib.parse.quote(account.account_id, safe='')}/summary", timeout=6)
        checks.append(
            {
                "id": "ibkr_portfolio",
                "label": "IBKR portfolio",
                "status": "ready",
                "message": "Portfolio summary is readable.",
                "details": compact_portfolio_summary(summary),
            }
        )
    except Exception as exc:
        checks.append(
            {
                "id": "ibkr_portfolio",
                "label": "IBKR portfolio",
                "status": "blocked",
                "message": str(exc),
            }
        )
    return {"authenticated": authenticated, "checks": checks}


def live_massive_scanner_snapshot(row_limit: int = 250) -> dict[str, Any]:
    payload = massive_get_json("/v2/snapshot/locale/us/markets/stocks/tickers", {}, timeout=20)
    tickers = payload.get("tickers") or payload.get("results") or []
    rows = [normalize_massive_snapshot(row) for row in tickers]
    rows = [row for row in rows if row["ticker"] and row["current_open"] > 0]
    rows.sort(key=lambda row: (row["live_priority"], row["last_day_volume_so_far"]), reverse=True)
    rows = rows[: max(1, min(int(row_limit or 250), 1000))]
    now = datetime.now(NEW_YORK)
    columns = [
        "ticker",
        "current_open",
        "last_price",
        "bid",
        "ask",
        "spread_bps",
        "last_day_current_change_pct",
        "last_day_volume_so_far",
        "last_transactions",
        "last_bar_dollar_volume",
        "live_priority",
        "live_setup_group",
        "provider",
    ]
    return {
        "snapshot": {
            "bar_time": now.strftime("%H:%M"),
            "columns": columns,
            "feature_groups": ["massive_live_snapshot", "nbbo", "tape_proxy"],
            "reason": "" if rows else "Massive snapshot returned no tradable stock rows.",
            "row_count": len(rows),
            "rows": rows,
            "session_date": now.date().isoformat(),
            "timeframe": "live",
        }
    }


def submit_ibkr_order(account_type: str, order: dict[str, Any], *, preview: bool = False) -> dict[str, Any]:
    account = configured_account(account_type)
    if not account.account_id:
        raise RuntimeError(f"Missing configured IBKR {account.account_type} account id.")
    payload = {"orders": [ibkr_order_payload(order, account.account_id)]}
    path = f"/iserver/account/{urllib.parse.quote(account.account_id, safe='')}/orders"
    if preview:
        path += "/whatif"
    result = ibkr_post_json(path, payload, timeout=10)
    return {
        "account_type": account.account_type,
        "account_id": mask_account_id(account.account_id),
        "preview": preview,
        "request": redact_order_payload(payload),
        "result": result,
    }


def ibkr_portfolio(account_type: str) -> dict[str, Any]:
    account = configured_account(account_type)
    if not account.account_id:
        raise RuntimeError(f"Missing configured IBKR {account.account_type} account id.")
    account_path = urllib.parse.quote(account.account_id, safe="")
    positions = ibkr_get_json(f"/portfolio/{account_path}/positions/0", timeout=8)
    summary = ibkr_get_json(f"/portfolio/{account_path}/summary", timeout=8)
    orders = ibkr_get_json("/iserver/account/orders", timeout=8)
    return {
        "account_type": account.account_type,
        "account_id": mask_account_id(account.account_id),
        "positions": positions if isinstance(positions, list) else positions.get("positions", []),
        "summary": summary,
        "orders": orders,
    }


def ibkr_order_payload(order: dict[str, Any], account_id: str) -> dict[str, Any]:
    symbol = str(order.get("symbol") or order.get("ticker") or "").strip().upper()
    if not symbol:
        raise ValueError("Order symbol is required.")
    side = str(order.get("side") or "BUY").strip().upper()
    if side not in {"BUY", "SELL"}:
        raise ValueError("Order side must be BUY or SELL.")
    quantity = int(float(order.get("quantity") or 0))
    if quantity <= 0:
        raise ValueError("Order quantity must be positive.")
    order_type = str(order.get("type") or "LMT").strip().upper()
    if order_type == "LIMIT":
        order_type = "LMT"
    if order_type == "MARKET":
        order_type = "MKT"
    conid = int(order["conid"]) if order.get("conid") else lookup_ibkr_stock_conid(symbol)
    payload: dict[str, Any] = {
        "acctId": account_id,
        "conid": conid,
        "ticker": symbol,
        "secType": "STK",
        "orderType": order_type,
        "side": side,
        "quantity": quantity,
        "tif": str(order.get("tif") or "DAY").upper(),
        "outsideRTH": bool(order.get("outsideRTH", True)),
    }
    price = float(order.get("limit") or order.get("price") or 0)
    if order_type == "LMT":
        if price <= 0:
            raise ValueError("Limit orders require a positive limit price.")
        payload["price"] = round(price, 4)
    stop = float(order.get("stop") or 0)
    if stop > 0 and order_type in {"STP", "STP LMT"}:
        payload["auxPrice"] = round(stop, 4)
    return payload


def lookup_ibkr_stock_conid(symbol: str) -> int:
    payload = ibkr_post_json("/iserver/secdef/search", {"symbol": symbol, "secType": "STK", "name": False}, timeout=8)
    candidates = payload if isinstance(payload, list) else payload.get("results", []) if isinstance(payload, dict) else []
    for candidate in candidates:
        if not isinstance(candidate, dict):
            continue
        candidate_symbol = str(candidate.get("symbol") or candidate.get("ticker") or "").upper()
        if candidate_symbol and candidate_symbol != symbol:
            continue
        conid = candidate.get("conid") or candidate.get("con_id")
        try:
            return int(conid)
        except (TypeError, ValueError):
            continue
    raise RuntimeError(f"Could not resolve IBKR stock conid for {symbol}.")


def massive_api_key() -> str:
    load_live_env()
    for name in ("MASSIVE_API_KEY", "MASSIVE_STOCK_API_KEY", "POLYGON_API_KEY"):
        value = os.environ.get(name, "").strip()
        if value:
            return value
    return ""


def massive_base_url() -> str:
    load_live_env()
    return os.environ.get("MASSIVE_BASE_URL", DEFAULT_MASSIVE_BASE_URL).rstrip("/")


def ibkr_base_url() -> str:
    load_live_env()
    return os.environ.get("IBKR_CPAPI_BASE_URL", DEFAULT_IBKR_BASE_URL).rstrip("/")


def massive_get_json(path: str, params: dict[str, str], *, timeout: int) -> dict[str, Any]:
    api_key = massive_api_key()
    if not api_key:
        raise RuntimeError("MASSIVE_API_KEY is not configured.")
    query = {**params, "apiKey": api_key}
    return http_json("GET", f"{massive_base_url()}{path}?{urllib.parse.urlencode(query)}", timeout=timeout)


def ibkr_get_json(path: str, *, timeout: int) -> Any:
    return http_json("GET", f"{ibkr_base_url()}{path}", timeout=timeout, allow_self_signed=True)


def ibkr_post_json(path: str, payload: dict[str, Any], *, timeout: int) -> Any:
    return http_json("POST", f"{ibkr_base_url()}{path}", payload=payload, timeout=timeout, allow_self_signed=True)


def http_json(method: str, url: str, payload: dict[str, Any] | None = None, *, timeout: int, allow_self_signed: bool = False) -> Any:
    body = None if payload is None else json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(url, data=body, method=method, headers={"Content-Type": "application/json", "Accept": "application/json"})
    context = ssl._create_unverified_context() if allow_self_signed and url.startswith("https://") else None
    try:
        with urllib.request.urlopen(request, timeout=timeout, context=context) as response:
            text = response.read().decode("utf-8")
    except urllib.error.HTTPError as exc:
        text = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"{method} {safe_url(url)} failed with HTTP {exc.code}: {text[:500]}") from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(f"{method} {safe_url(url)} failed: {exc.reason}") from exc
    if not text.strip():
        return {}
    try:
        return json.loads(text)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"{method} {safe_url(url)} returned non-JSON: {text[:200]}") from exc


def safe_url(url: str) -> str:
    parsed = urllib.parse.urlsplit(url)
    params = urllib.parse.parse_qsl(parsed.query, keep_blank_values=True)
    redacted = [(key, "redacted" if key.lower() in {"apikey", "api_key", "token"} else value) for key, value in params]
    return urllib.parse.urlunsplit((parsed.scheme, parsed.netloc, parsed.path, urllib.parse.urlencode(redacted), parsed.fragment))


def ibkr_account_ids(payload: Any) -> list[str]:
    if isinstance(payload, dict):
        raw = payload.get("accounts") or payload.get("acctIds") or payload.get("selectedAccount") or []
        if isinstance(raw, str):
            return [raw]
        if isinstance(raw, list):
            return [str(item.get("id") if isinstance(item, dict) else item) for item in raw if item]
    if isinstance(payload, list):
        return [str(item.get("id") if isinstance(item, dict) else item) for item in payload if item]
    return []


def normalize_massive_snapshot(item: dict[str, Any]) -> dict[str, Any]:
    ticker = str(item.get("ticker") or item.get("T") or "").upper()
    day = item.get("day") or {}
    last_trade = item.get("lastTrade") or item.get("last_trade") or {}
    last_quote = item.get("lastQuote") or item.get("last_quote") or {}
    price = float_value(last_trade.get("p") or item.get("lastTradePrice") or day.get("c") or 0)
    day_open = float_value(day.get("o") or price)
    volume = float_value(day.get("v") or 0)
    transactions = float_value(day.get("n") or 0)
    bid = float_value(last_quote.get("p") or last_quote.get("bp") or 0)
    ask = float_value(last_quote.get("P") or last_quote.get("ap") or 0)
    spread_bps = ((ask - bid) / price * 10_000) if ask > 0 and bid > 0 and price > 0 and ask >= bid else None
    raw_change_pct = item.get("todaysChangePerc")
    change_pct = float_value(raw_change_pct) / 100 if raw_change_pct is not None else 0.0
    if raw_change_pct is None and day_open > 0 and price > 0:
        change_pct = (price / day_open) - 1
    dollar_volume = volume * price if price > 0 else 0
    live_priority = change_pct * 100 + min(dollar_volume / 1_000_000, 100) + min(transactions / 1_000, 50)
    return {
        "ticker": ticker,
        "current_open": price,
        "last_price": price,
        "bid": bid or None,
        "ask": ask or None,
        "spread_bps": spread_bps,
        "last_day_current_change_pct": change_pct,
        "last_day_volume_so_far": volume,
        "last_transactions": transactions,
        "last_bar_dollar_volume": dollar_volume,
        "live_priority": live_priority,
        "live_setup_group": "massive-live",
        "provider": "massive",
    }


def float_value(value: Any) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return 0.0
    return number if number == number else 0.0


def compact_portfolio_summary(summary: Any) -> dict[str, Any]:
    if not isinstance(summary, dict):
        return {}
    wanted = ("netliquidation", "availablefunds", "buyingpower", "cashbalance", "totalcashvalue")
    compact: dict[str, Any] = {}
    for key, value in summary.items():
        lower = str(key).lower()
        if any(name in lower for name in wanted):
            compact[str(key)] = value
    return compact


def redact_order_payload(payload: dict[str, Any]) -> dict[str, Any]:
    return json.loads(json.dumps(payload))


def mask_account_id(account_id: str) -> str:
    if not account_id:
        return ""
    if len(account_id) <= 4:
        return "*" * len(account_id)
    return f"{account_id[:2]}***{account_id[-3:]}"
