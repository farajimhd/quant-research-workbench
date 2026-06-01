from __future__ import annotations

import json
import os
import re
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
class RealLiveAccount:
    account_key: str
    account_class: str
    account_id: str
    label: str
    trading_mode: str

    @property
    def account_type(self) -> str:
        return self.account_key


def load_real_live_env() -> None:
    for env_path in (Path.cwd() / ".env", REPO_ROOT / ".env"):
        if env_path.exists():
            load_dotenv(env_path, override=False)
    load_dotenv(override=False)


def normalize_account_key(account_key: str) -> str:
    normalized = re.sub(r"[^a-z0-9_-]+", "-", (account_key or "").strip().lower()).strip("-")
    if not normalized:
        raise ValueError("account key is required")
    return normalized


def configured_real_live_accounts() -> list[RealLiveAccount]:
    load_real_live_env()
    accounts: dict[str, RealLiveAccount] = {}

    json_config = os.environ.get("IBKR_ACCOUNTS_JSON", "").strip()
    if json_config:
        parsed = json.loads(json_config)
        if isinstance(parsed, dict):
            items = parsed.items()
        elif isinstance(parsed, list):
            items = enumerate(parsed)
        else:
            raise ValueError("IBKR_ACCOUNTS_JSON must be a list or object")
        for fallback_key, item in items:
            if isinstance(item, dict):
                account = account_from_config({"key": fallback_key, **item} if not item.get("key") else item)
                accounts[account.account_key] = account

    for name, value in os.environ.items():
        match = re.fullmatch(r"IBKR_ACCOUNT_([A-Z0-9_]+)_ID", name)
        if not match:
            continue
        key = normalize_account_key(match.group(1))
        accounts[key] = account_from_config(
            {
                "account_id": value,
                "account_class": os.environ.get(f"IBKR_ACCOUNT_{match.group(1)}_CLASS", key),
                "key": key,
                "label": os.environ.get(f"IBKR_ACCOUNT_{match.group(1)}_LABEL", key.replace("-", " ").title()),
                "trading_mode": os.environ.get(f"IBKR_ACCOUNT_{match.group(1)}_MODE", "paper" if "paper" in key else "live"),
            }
        )

    legacy_accounts = [
        ("paper", "Paper", "paper", "paper", os.environ.get("IBKR_PAPER_ACCOUNT_ID", "")),
        ("cash", "Cash", "cash", "live", os.environ.get("IBKR_CASH_ACCOUNT_ID", "")),
        ("margin", "Margin", "margin", "live", os.environ.get("IBKR_MARGIN_ACCOUNT_ID", "")),
        ("rrsp", "RRSP", "rrsp", "live", os.environ.get("IBKR_RRSP_ACCOUNT_ID", "")),
    ]
    for key, label, account_class, trading_mode, account_id in legacy_accounts:
        if key not in accounts:
            accounts[key] = RealLiveAccount(account_key=key, account_class=account_class, account_id=account_id.strip(), label=label, trading_mode=trading_mode)

    return list(accounts.values())


def account_from_config(item: dict[str, Any]) -> RealLiveAccount:
    key = normalize_account_key(str(item.get("key") or item.get("name") or item.get("account_key") or item.get("account_class") or item.get("type") or ""))
    account_class = normalize_account_key(str(item.get("account_class") or item.get("type") or key))
    trading_mode = normalize_account_key(str(item.get("trading_mode") or item.get("mode") or ("paper" if account_class == "paper" else "live")))
    return RealLiveAccount(
        account_key=key,
        account_class=account_class,
        account_id=str(item.get("account_id") or item.get("id") or item.get("account") or "").strip(),
        label=str(item.get("label") or key.replace("-", " ").title()).strip(),
        trading_mode="paper" if trading_mode == "paper" else "live",
    )


def resolve_real_live_accounts(account_keys: str | list[str] | None = None, account_type: str = "paper") -> list[RealLiveAccount]:
    accounts = configured_real_live_accounts()
    by_key = {account.account_key: account for account in accounts}
    selected_keys = parse_account_keys(account_keys)
    if not selected_keys:
        legacy_key = normalize_account_key(account_type or "paper")
        if legacy_key in by_key:
            selected_keys = [legacy_key]
        else:
            selected_keys = [account.account_key for account in accounts if account.account_class == legacy_key or account.trading_mode == legacy_key][:1]
    selected: list[RealLiveAccount] = []
    missing: list[str] = []
    for key in selected_keys:
        account = by_key.get(key)
        if account:
            selected.append(account)
        else:
            missing.append(key)
    if missing:
        raise ValueError(f"Unknown configured IBKR account key(s): {', '.join(missing)}")
    if not selected:
        raise ValueError("Select at least one configured IBKR account.")
    return selected


def parse_account_keys(account_keys: str | list[str] | None) -> list[str]:
    if isinstance(account_keys, str):
        raw_items = re.split(r"[,|]", account_keys)
    elif isinstance(account_keys, list):
        raw_items = account_keys
    else:
        raw_items = []
    return [normalize_account_key(str(item)) for item in raw_items if str(item).strip()]


def configured_real_live_account(account_type: str) -> RealLiveAccount:
    return resolve_real_live_accounts(account_type=account_type)[0]


def real_live_preflight(account_type: str = "paper", account_keys: str | list[str] | None = None) -> dict[str, Any]:
    accounts = configured_real_live_accounts()
    selected_accounts = resolve_real_live_accounts(account_keys, account_type)
    checks = [check_massive_rest()]
    for account in selected_accounts:
        checks.extend(check_ibkr(account))
    return {
        "ready": all(check["status"] == "ready" for check in checks),
        "account_type": selected_accounts[0].account_key,
        "account_id": ", ".join(mask_account_id(account.account_id) for account in selected_accounts if account.account_id),
        "accounts": [public_account(account) for account in accounts],
        "selected_account_keys": [account.account_key for account in selected_accounts],
        "selected_accounts": [public_account(account) for account in selected_accounts],
        "checks": checks,
        "data_provider": {"name": "massive", "base_url": massive_base_url()},
        "broker": {"name": "ibkr_client_portal", "base_url": ibkr_base_url()},
    }


def real_live_scanner_snapshot(row_limit: int = 250) -> dict[str, Any]:
    payload = massive_get_json("/v2/snapshot/locale/us/markets/stocks/tickers", {}, timeout=20)
    tickers = payload.get("tickers") or payload.get("results") or []
    rows = [normalize_massive_ticker_snapshot(item) for item in tickers]
    rows = [row for row in rows if row["symbol"] and row["last_price"] > 0]
    rows.sort(key=lambda row: (row["live_priority"], row["day_volume"]), reverse=True)
    rows = rows[: max(1, min(int(row_limit or 250), 1000))]
    now = datetime.now(NEW_YORK)
    return {
        "provider": "massive",
        "session_date": now.date().isoformat(),
        "market_time": now.strftime("%H:%M"),
        "rows": rows,
        "row_count": len(rows),
    }


def real_live_portfolio(account_type: str, account_keys: str | list[str] | None = None) -> dict[str, Any]:
    selected_accounts = resolve_real_live_accounts(account_keys, account_type)
    portfolios = [real_live_portfolio_for_account(account) for account in selected_accounts]
    return {
        "account_type": selected_accounts[0].account_key,
        "account_id": ", ".join(portfolio["account_id"] for portfolio in portfolios if portfolio.get("account_id")),
        "accounts": [public_account(account) for account in selected_accounts],
        "portfolios": portfolios,
        "summary": {portfolio["account_key"]: portfolio.get("summary", {}) for portfolio in portfolios},
        "positions": [position for portfolio in portfolios for position in portfolio.get("positions", [])],
        "orders": [order for portfolio in portfolios for order in portfolio.get("orders", [])],
    }


def real_live_portfolio_for_account(account: RealLiveAccount) -> dict[str, Any]:
    if not account.account_id:
        raise RuntimeError(f"Missing configured IBKR {account.label} account id.")
    account_path = urllib.parse.quote(account.account_id, safe="")
    raw_positions = ibkr_get_json(f"/portfolio/{account_path}/positions/0", timeout=8)
    raw_summary = ibkr_get_json(f"/portfolio/{account_path}/summary", timeout=8)
    raw_orders = ibkr_get_json("/iserver/account/orders", timeout=8)
    return {
        "account_key": account.account_key,
        "account_type": account.account_key,
        "account_class": account.account_class,
        "account_id": mask_account_id(account.account_id),
        "label": account.label,
        "trading_mode": account.trading_mode,
        "summary": normalize_account_summary(raw_summary),
        "positions": normalize_positions(raw_positions if isinstance(raw_positions, list) else raw_positions.get("positions", []), account),
        "orders": normalize_ibkr_orders(raw_orders, account),
    }


def submit_real_live_order(account_type: str, order: dict[str, Any], *, preview: bool = False, account_keys: str | list[str] | None = None) -> dict[str, Any]:
    selected_accounts = resolve_real_live_accounts(account_keys, account_type)
    results = [submit_real_live_order_for_account(account, order, preview=preview) for account in selected_accounts]
    return {
        "account_type": selected_accounts[0].account_key,
        "account_id": ", ".join(result["account_id"] for result in results if result.get("account_id")),
        "accounts": [public_account(account) for account in selected_accounts],
        "preview": preview,
        "results": results,
        "submitted_orders": [result["submitted_order"] for result in results],
    }


def submit_real_live_order_for_account(account: RealLiveAccount, order: dict[str, Any], *, preview: bool = False) -> dict[str, Any]:
    if not account.account_id:
        raise RuntimeError(f"Missing configured IBKR {account.label} account id.")
    ibkr_order = ibkr_order_payload(order, account.account_id)
    payload = {"orders": [ibkr_order]}
    path = f"/iserver/account/{urllib.parse.quote(account.account_id, safe='')}/orders"
    if preview:
        path += "/whatif"
    result = ibkr_post_json(path, payload, timeout=10)
    return {
        "account_key": account.account_key,
        "account_type": account.account_key,
        "account_class": account.account_class,
        "account_id": mask_account_id(account.account_id),
        "label": account.label,
        "preview": preview,
        "submitted_order": normalize_submitted_order(order, result, account),
        "broker_response": result,
    }


def check_massive_rest() -> dict[str, Any]:
    if not massive_api_key():
        return {"id": "massive_api_key", "label": "Massive API key", "status": "blocked", "message": "Set MASSIVE_API_KEY in .env."}
    try:
        payload = massive_get_json("/v3/reference/tickers", {"market": "stocks", "active": "true", "limit": "1"}, timeout=8)
        ready = bool(payload.get("results"))
        return {"id": "massive_rest", "label": "Massive REST", "status": "ready" if ready else "blocked", "message": "Massive REST is reachable." if ready else "Massive returned no reference rows."}
    except Exception as exc:
        return {"id": "massive_rest", "label": "Massive REST", "status": "blocked", "message": str(exc)}


def check_ibkr(account: RealLiveAccount) -> list[dict[str, Any]]:
    if not account.account_id:
        return [{"id": f"{account.account_key}_ibkr_account_env", "label": f"{account.label} account", "status": "blocked", "message": f"Set an account id for {account.account_key} in .env."}]
    checks: list[dict[str, Any]] = []
    try:
        status = ibkr_get_json("/iserver/auth/status", timeout=5)
        authenticated = bool(status.get("authenticated") or (status.get("connected") and status.get("competing") is False))
        checks.append({"id": f"{account.account_key}_ibkr_auth", "label": f"{account.label} session", "status": "ready" if authenticated else "blocked", "message": "Authenticated Client Portal session is available." if authenticated else "Authenticate Client Portal Gateway first."})
    except Exception as exc:
        return [{"id": f"{account.account_key}_ibkr_gateway", "label": f"{account.label} gateway", "status": "blocked", "message": str(exc)}]
    try:
        accounts = ibkr_account_ids(ibkr_get_json("/iserver/accounts", timeout=6))
        checks.append({"id": f"{account.account_key}_ibkr_account", "label": f"{account.label} access", "status": "ready" if account.account_id in accounts else "blocked", "message": "Configured account is available." if account.account_id in accounts else "Configured account was not returned by IBKR.", "details": {"available_accounts": [mask_account_id(item) for item in accounts]}})
    except Exception as exc:
        checks.append({"id": f"{account.account_key}_ibkr_account", "label": f"{account.label} access", "status": "blocked", "message": str(exc)})
    try:
        ibkr_get_json(f"/portfolio/{urllib.parse.quote(account.account_id, safe='')}/summary", timeout=6)
        checks.append({"id": f"{account.account_key}_ibkr_portfolio", "label": f"{account.label} portfolio", "status": "ready", "message": "Portfolio summary is readable."})
    except Exception as exc:
        checks.append({"id": f"{account.account_key}_ibkr_portfolio", "label": f"{account.label} portfolio", "status": "blocked", "message": str(exc)})
    return checks


def ibkr_order_payload(order: dict[str, Any], account_id: str) -> dict[str, Any]:
    symbol = str(order.get("symbol") or "").strip().upper()
    if not symbol:
        raise ValueError("Order symbol is required.")
    side = str(order.get("side") or "BUY").strip().upper()
    if side not in {"BUY", "SELL"}:
        raise ValueError("Order side must be BUY or SELL.")
    quantity = int(float(order.get("quantity") or 0))
    if quantity <= 0:
        raise ValueError("Order quantity must be positive.")
    order_type = normalize_order_type(str(order.get("order_type") or "LMT"))
    payload: dict[str, Any] = {
        "acctId": account_id,
        "conid": int(order["conid"]) if order.get("conid") else lookup_ibkr_stock_conid(symbol),
        "ticker": symbol,
        "secType": "STK",
        "orderType": order_type,
        "side": side,
        "quantity": quantity,
        "tif": str(order.get("time_in_force") or "DAY").upper(),
        "outsideRTH": bool(order.get("outside_rth", True)),
    }
    limit_price = float(order.get("limit_price") or 0)
    if order_type == "LMT":
        if limit_price <= 0:
            raise ValueError("Limit orders require limit_price.")
        payload["price"] = round(limit_price, 4)
    return payload


def normalize_order_type(value: str) -> str:
    normalized = value.strip().upper()
    if normalized == "LIMIT":
        return "LMT"
    if normalized == "MARKET":
        return "MKT"
    if normalized not in {"LMT", "MKT"}:
        raise ValueError("Only LMT and MKT orders are supported by the live page for now.")
    return normalized


def normalize_submitted_order(order: dict[str, Any], response: Any, account: RealLiveAccount | None = None) -> dict[str, Any]:
    broker_order_id = broker_id_from_response(response)
    quantity = int(float(order.get("quantity") or 0))
    return {
        "account_key": account.account_key if account else "",
        "account_label": account.label if account else "",
        "client_order_id": str(order.get("client_order_id") or ""),
        "broker_order_id": broker_order_id,
        "symbol": str(order.get("symbol") or "").upper(),
        "side": str(order.get("side") or "").upper(),
        "order_type": normalize_order_type(str(order.get("order_type") or "LMT")),
        "quantity": quantity,
        "limit_price": float(order.get("limit_price") or 0) or None,
        "time_in_force": str(order.get("time_in_force") or "DAY").upper(),
        "status": "NEEDS_REPLY" if response_requires_reply(response) else "SUBMITTED",
        "submitted_at": datetime.now(NEW_YORK).isoformat(),
        "filled_quantity": 0,
        "remaining_quantity": quantity,
        "avg_fill_price": None,
        "last_fill_price": None,
        "fills": [],
        "raw_broker_response": response,
    }


def normalize_ibkr_orders(payload: Any, account: RealLiveAccount | None = None) -> list[dict[str, Any]]:
    raw_orders = payload.get("orders", []) if isinstance(payload, dict) else payload if isinstance(payload, list) else []
    return [normalize_ibkr_order(item, account) for item in raw_orders if isinstance(item, dict)]


def normalize_ibkr_order(item: dict[str, Any], account: RealLiveAccount | None = None) -> dict[str, Any]:
    quantity = float_value(item.get("totalSize") or item.get("quantity") or item.get("size"))
    filled = float_value(item.get("filledQuantity") or item.get("filled") or 0)
    avg_price = float_value(item.get("avgPrice") or item.get("avg_fill_price") or 0)
    return {
        "account_key": account.account_key if account else "",
        "account_label": account.label if account else "",
        "broker_order_id": str(item.get("orderId") or item.get("order_id") or item.get("id") or ""),
        "symbol": str(item.get("ticker") or item.get("symbol") or "").upper(),
        "side": str(item.get("side") or "").upper(),
        "order_type": str(item.get("orderType") or item.get("order_type") or ""),
        "quantity": quantity,
        "filled_quantity": filled,
        "remaining_quantity": max(0.0, quantity - filled),
        "avg_fill_price": avg_price or None,
        "last_fill_price": float_value(item.get("lastExecutionPrice")) or None,
        "status": str(item.get("status") or "UNKNOWN").upper(),
        "submitted_at": item.get("lastExecutionTime") or item.get("submitted_at") or "",
        "raw_broker_order": item,
    }


def normalize_positions(rows: list[dict[str, Any]], account: RealLiveAccount | None = None) -> list[dict[str, Any]]:
    return [normalize_position(row, account) for row in rows if isinstance(row, dict)]


def normalize_position(row: dict[str, Any], account: RealLiveAccount | None = None) -> dict[str, Any]:
    symbol = str(row.get("ticker") or row.get("symbol") or row.get("contractDesc") or "").split(" ")[0].upper()
    quantity = float_value(row.get("position") or row.get("quantity"))
    avg_price = float_value(row.get("avgCost") or row.get("averageCost"))
    mark = float_value(row.get("mktPrice") or row.get("marketPrice"))
    return {
        "account_key": account.account_key if account else "",
        "account_label": account.label if account else "",
        "symbol": symbol,
        "quantity": quantity,
        "avg_price": avg_price,
        "mark_price": mark,
        "market_value": float_value(row.get("mktValue") or row.get("marketValue")),
        "unrealized_pnl": float_value(row.get("unrealizedPnl")),
        "realized_pnl": float_value(row.get("realizedPnl")),
        "raw_broker_position": row,
    }


def normalize_account_summary(summary: Any) -> dict[str, Any]:
    if not isinstance(summary, dict):
        return {}
    return {str(key): value for key, value in summary.items() if any(token in str(key).lower() for token in ("netliquidation", "availablefunds", "buyingpower", "cashbalance", "totalcashvalue"))}


def public_account(account: RealLiveAccount) -> dict[str, Any]:
    return {
        "account_key": account.account_key,
        "account_class": account.account_class,
        "account_id": mask_account_id(account.account_id),
        "configured": bool(account.account_id),
        "label": account.label,
        "trading_mode": account.trading_mode,
    }


def normalize_massive_ticker_snapshot(item: dict[str, Any]) -> dict[str, Any]:
    day = item.get("day") or {}
    trade = item.get("lastTrade") or item.get("last_trade") or {}
    quote = item.get("lastQuote") or item.get("last_quote") or {}
    symbol = str(item.get("ticker") or item.get("T") or "").upper()
    last_price = float_value(trade.get("p") or item.get("lastTradePrice") or day.get("c"))
    day_open = float_value(day.get("o") or last_price)
    bid = float_value(quote.get("p") or quote.get("bp"))
    ask = float_value(quote.get("P") or quote.get("ap"))
    day_volume = float_value(day.get("v"))
    trade_count = float_value(day.get("n"))
    raw_change = item.get("todaysChangePerc")
    day_change_pct = float_value(raw_change) / 100 if raw_change is not None else (last_price / day_open - 1 if day_open > 0 and last_price > 0 else 0)
    spread_bps = ((ask - bid) / last_price * 10_000) if ask >= bid and bid > 0 and last_price > 0 else None
    notional = last_price * day_volume if last_price > 0 else 0
    return {
        "symbol": symbol,
        "last_price": last_price,
        "bid": bid or None,
        "ask": ask or None,
        "spread_bps": spread_bps,
        "day_change_pct": day_change_pct,
        "day_volume": day_volume,
        "trade_count": trade_count,
        "day_notional": notional,
        "live_priority": day_change_pct * 100 + min(notional / 1_000_000, 100) + min(trade_count / 1_000, 50),
        "provider": "massive",
    }


def lookup_ibkr_stock_conid(symbol: str) -> int:
    payload = ibkr_post_json("/iserver/secdef/search", {"symbol": symbol, "secType": "STK", "name": False}, timeout=8)
    candidates = payload if isinstance(payload, list) else payload.get("results", []) if isinstance(payload, dict) else []
    for candidate in candidates:
        if isinstance(candidate, dict):
            candidate_symbol = str(candidate.get("symbol") or candidate.get("ticker") or "").upper()
            if candidate_symbol and candidate_symbol != symbol:
                continue
            try:
                return int(candidate.get("conid") or candidate.get("con_id"))
            except (TypeError, ValueError):
                continue
    raise RuntimeError(f"Could not resolve IBKR stock conid for {symbol}.")


def response_requires_reply(response: Any) -> bool:
    return isinstance(response, list) and any(isinstance(item, dict) and item.get("id") and item.get("message") for item in response)


def broker_id_from_response(response: Any) -> str:
    if isinstance(response, list):
        for item in response:
            if isinstance(item, dict) and (item.get("order_id") or item.get("orderId") or item.get("id")):
                return str(item.get("order_id") or item.get("orderId") or item.get("id"))
    if isinstance(response, dict):
        return str(response.get("order_id") or response.get("orderId") or response.get("id") or "")
    return ""


def massive_api_key() -> str:
    load_real_live_env()
    for name in ("MASSIVE_API_KEY", "MASSIVE_STOCK_API_KEY", "POLYGON_API_KEY"):
        value = os.environ.get(name, "").strip()
        if value:
            return value
    return ""


def massive_base_url() -> str:
    load_real_live_env()
    return os.environ.get("MASSIVE_BASE_URL", DEFAULT_MASSIVE_BASE_URL).rstrip("/")


def ibkr_base_url() -> str:
    load_real_live_env()
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
    return json.loads(text)


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


def float_value(value: Any) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return 0.0
    return number if number == number else 0.0


def mask_account_id(account_id: str) -> str:
    if not account_id:
        return ""
    if len(account_id) <= 4:
        return "*" * len(account_id)
    return f"{account_id[:2]}***{account_id[-3:]}"
