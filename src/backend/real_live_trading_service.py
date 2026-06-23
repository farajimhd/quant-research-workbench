from __future__ import annotations

import json
import os
import re
import ssl
import urllib.error
import urllib.parse
import urllib.request
import uuid
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from dotenv import load_dotenv

from src.backend.qmd_gateway_client import qmd_scanner_snapshot
from src.backend.real_live_market_data.clickhouse import ClickHouseHttpClient, quote_identifier
from src.backend.real_live_market_data.config import market_gateway_config
from src.market_engine.broker import AccountSnapshot, ExecutionFill, OrderSnapshot, PortfolioPosition


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
    try:
        payload = qmd_scanner_snapshot(row_limit=row_limit)
        if payload.get("row_count", 0) > 0:
            return apply_tradable_filter_to_scanner_payload(payload)
    except Exception as exc:
        qmd_error = str(exc)
    else:
        qmd_error = "QMD gateway returned no scanner rows."
    payload = massive_get_json("/v2/snapshot/locale/us/markets/stocks/tickers", {}, timeout=20)
    tickers = payload.get("tickers") or payload.get("results") or []
    rows = [normalize_massive_ticker_snapshot(item) for item in tickers]
    rows = [row for row in rows if row["symbol"] and row["last_price"] > 0]
    rows.sort(key=lambda row: (row["live_priority"], row["day_volume"]), reverse=True)
    rows = rows[: max(1, min(int(row_limit or 250), 1000))]
    rows, tradable_filter = filter_tradable_rows(rows)
    now = datetime.now(NEW_YORK)
    return {
        "provider": "massive",
        "session_date": now.date().isoformat(),
        "market_time": now.strftime("%H:%M"),
        "rows": rows,
        "row_count": len(rows),
        "qmd_gateway_error": qmd_error,
        "tradable_filter": tradable_filter,
    }


def real_live_portfolio(account_type: str, account_keys: str | list[str] | None = None) -> dict[str, Any]:
    selected_accounts = resolve_real_live_accounts(account_keys, account_type)
    now = datetime.now(NEW_YORK).isoformat()
    selected_account_ids = {account.account_id for account in selected_accounts if account.account_id}
    selected_by_id = {account.account_id: account for account in selected_accounts if account.account_id}
    selected_by_key = {account.account_key: account for account in selected_accounts}
    errors: list[dict[str, Any]] = []
    portfolio_accounts, portfolio_accounts_error = ibkr_get_optional("/portfolio/accounts", timeout=8)
    iserver_accounts, iserver_accounts_error = ibkr_get_optional("/iserver/accounts", timeout=8)
    raw_orders, orders_error = ibkr_get_optional("/iserver/account/orders", timeout=8)
    raw_trades, trades_error = ibkr_get_optional("/iserver/account/trades?days=7", timeout=8)
    raw_pnl, pnl_error = ibkr_get_optional("/iserver/account/pnl/partitioned", timeout=8)

    for scope, error in (
        ("portfolio_accounts", portfolio_accounts_error),
        ("iserver_accounts", iserver_accounts_error),
        ("orders", orders_error),
        ("trades", trades_error),
        ("pnl", pnl_error),
    ):
        if error:
            errors.append({"account_key": "", "scope": scope, "message": error})

    portfolios = [real_live_portfolio_for_account(account, now=now, errors=errors) for account in selected_accounts]
    orders = normalize_ibkr_orders(raw_orders, selected_by_id=selected_by_id, fallback_account=selected_accounts[0] if len(selected_accounts) == 1 else None)
    orders = [order for order in orders if account_row_matches_selection(order, selected_by_key, selected_account_ids)]
    executions = normalize_ibkr_trades(raw_trades, selected_by_id=selected_by_id, fallback_account=selected_accounts[0] if len(selected_accounts) == 1 else None)
    executions = [execution for execution in executions if account_row_matches_selection(execution, selected_by_key, selected_account_ids)]
    pnl_rows = normalize_pnl_rows(raw_pnl, selected_accounts)
    return {
        "as_of": now,
        "account_type": selected_accounts[0].account_key,
        "account_id": ", ".join(portfolio["account_id"] for portfolio in portfolios if portfolio.get("account_id")),
        "accounts": [public_account(account) for account in selected_accounts],
        "balances": [portfolio.get("balances", {}) for portfolio in portfolios],
        "connection": {
            "portfolio": "blocked" if portfolio_accounts_error else "ready",
            "iserver": "blocked" if iserver_accounts_error else "ready",
        },
        "errors": errors,
        "executions": executions,
        "ledger": {portfolio["account_key"]: portfolio.get("ledger", {}) for portfolio in portfolios},
        "orders": orders,
        "pnl": pnl_rows,
        "portfolios": portfolios,
        "selected_account_keys": [account.account_key for account in selected_accounts],
        "summary": {portfolio["account_key"]: portfolio.get("summary", {}) for portfolio in portfolios},
        "positions": [position for portfolio in portfolios for position in portfolio.get("positions", [])],
        "source": "ibkr",
        "raw_refs": {
            "iserver_accounts": mask_account_payload(iserver_accounts),
            "portfolio_accounts": mask_account_payload(portfolio_accounts),
        },
    }


def real_live_portfolio_for_account(account: RealLiveAccount, *, now: str | None = None, errors: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    if not account.account_id:
        raise RuntimeError(f"Missing configured IBKR {account.label} account id.")
    account_path = urllib.parse.quote(account.account_id, safe="")
    snapshot_time = now or datetime.now(NEW_YORK).isoformat()
    account_errors: list[dict[str, Any]] = []
    raw_positions, positions_error = ibkr_get_optional(f"/portfolio2/{account_path}/positions", timeout=8)
    if positions_error:
        raw_positions, positions_error = ibkr_get_optional(f"/portfolio/{account_path}/positions/0", timeout=8)
    raw_summary, summary_error = ibkr_get_optional(f"/portfolio/{account_path}/summary", timeout=8)
    raw_ledger, ledger_error = ibkr_get_optional(f"/portfolio/{account_path}/ledger", timeout=8)
    for scope, error in (("positions", positions_error), ("summary", summary_error), ("ledger", ledger_error)):
        if error:
            item = {"account_key": account.account_key, "account_id": mask_account_id(account.account_id), "scope": scope, "message": error}
            account_errors.append(item)
            if errors is not None:
                errors.append(item)
    summary = normalize_account_summary(raw_summary)
    ledger = normalize_ledger(raw_ledger)
    return {
        "as_of": snapshot_time,
        "account_key": account.account_key,
        "account_type": account.account_key,
        "account_class": account.account_class,
        "account_id": mask_account_id(account.account_id),
        "label": account.label,
        "trading_mode": account.trading_mode,
        "balances": broker_balances(summary, ledger, account),
        "broker_account_snapshot": broker_account_snapshot(summary, ledger, account, raw_positions, raw_summary, raw_ledger, snapshot_time),
        "errors": account_errors,
        "ledger": ledger,
        "summary": summary,
        "positions": normalize_positions(position_rows(raw_positions), account, as_of=snapshot_time),
    }


def submit_real_live_order(account_type: str, order: dict[str, Any], *, preview: bool = False, account_keys: str | list[str] | None = None) -> dict[str, Any]:
    selected_accounts = resolve_real_live_accounts(account_keys, account_type)
    normalized_order = normalize_live_order_intent(order)
    tradability = require_tradable_symbol(normalized_order["symbol"])
    conid = int(tradability["ibkr_conid"])
    requested_conid = int(float(normalized_order.get("conid") or 0))
    if requested_conid and requested_conid != conid:
        raise ValueError(f"Order conid {requested_conid} does not match q_live tradable universe conid {conid} for {normalized_order['symbol']}.")
    normalized_order["conid"] = conid
    normalized_order["tradable_universe"] = tradability
    results = [submit_real_live_order_for_account(account, normalized_order, preview=preview) for account in selected_accounts]
    return {
        "account_type": selected_accounts[0].account_key,
        "account_id": ", ".join(result["account_id"] for result in results if result.get("account_id")),
        "accounts": [public_account(account) for account in selected_accounts],
        "preview": preview,
        "results": results,
        "submitted_orders": [result["submitted_order"] for result in results],
        "client_order_id": normalized_order["client_order_id"],
        "tradable_universe": tradability,
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
        "cOID": str(order.get("client_order_id") or ""),
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


def normalize_live_order_intent(order: dict[str, Any]) -> dict[str, Any]:
    normalized = dict(order)
    symbol = str(normalized.get("symbol") or "").strip().upper()
    if not symbol:
        raise ValueError("Order symbol is required.")
    side = str(normalized.get("side") or "BUY").strip().upper()
    if side not in {"BUY", "SELL"}:
        raise ValueError("Order side must be BUY or SELL.")
    quantity = int(float(normalized.get("quantity") or 0))
    if quantity <= 0:
        raise ValueError("Order quantity must be positive.")
    order_type = normalize_order_type(str(normalized.get("order_type") or "LMT"))
    client_order_id = str(normalized.get("client_order_id") or "").strip()
    if not client_order_id:
        client_order_id = f"qrw-{datetime.utcnow().strftime('%Y%m%dT%H%M%S%f')}-{uuid.uuid4().hex[:10]}"
    normalized.update(
        {
            "client_order_id": client_order_id,
            "order_type": order_type,
            "quantity": quantity,
            "side": side,
            "symbol": symbol,
            "time_in_force": str(normalized.get("time_in_force") or "DAY").upper(),
        }
    )
    if order_type == "LMT":
        limit_price = float(normalized.get("limit_price") or 0)
        if limit_price <= 0:
            raise ValueError("Limit orders require limit_price.")
        normalized["limit_price"] = round(limit_price, 4)
    return normalized


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
    row = {
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
    row["broker_order_snapshot"] = order_snapshot_from_row(row, account, raw=response)
    return row


def normalize_ibkr_orders(
    payload: Any,
    account: RealLiveAccount | None = None,
    *,
    selected_by_id: dict[str, RealLiveAccount] | None = None,
    fallback_account: RealLiveAccount | None = None,
) -> list[dict[str, Any]]:
    raw_orders = payload.get("orders", []) if isinstance(payload, dict) else payload if isinstance(payload, list) else []
    return [normalize_ibkr_order(item, account or account_from_broker_row(item, selected_by_id, fallback_account)) for item in raw_orders if isinstance(item, dict)]


def normalize_ibkr_order(item: dict[str, Any], account: RealLiveAccount | None = None) -> dict[str, Any]:
    quantity = float_value(item.get("totalSize") or item.get("quantity") or item.get("size"))
    filled = float_value(item.get("filledQuantity") or item.get("filled") or 0)
    avg_price = float_value(item.get("avgPrice") or item.get("avg_fill_price") or 0)
    account_id = broker_account_id(item)
    broker_order_id = str(item.get("orderId") or item.get("order_id") or item.get("id") or "")
    row = {
        "account_key": account.account_key if account else "",
        "account_label": account.label if account else "",
        "account_class": account.account_class if account else "",
        "account_id": mask_account_id(account_id or account.account_id if account else account_id),
        "broker_order_id": broker_order_id,
        "client_order_id": str(item.get("cOID") or item.get("client_order_id") or item.get("order_ref") or ""),
        "conid": str(item.get("conid") or item.get("con_id") or ""),
        "symbol": str(item.get("ticker") or item.get("symbol") or "").upper(),
        "side": str(item.get("side") or "").upper(),
        "order_type": str(item.get("orderType") or item.get("order_type") or ""),
        "quantity": quantity,
        "filled_quantity": filled,
        "remaining_quantity": max(0.0, quantity - filled),
        "avg_fill_price": avg_price or None,
        "last_fill_price": float_value(item.get("lastExecutionPrice")) or None,
        "status": str(item.get("status") or "UNKNOWN").upper(),
        "submitted_at": item.get("lastExecutionTime") or item.get("submitted_at") or item.get("orderTime") or "",
        "updated_at": item.get("lastExecutionTime") or item.get("modifiedTime") or "",
        "raw_broker_order": item,
    }
    row["broker_order_snapshot"] = order_snapshot_from_row(row, account, raw=item)
    return row


def normalize_positions(rows: list[dict[str, Any]], account: RealLiveAccount | None = None, *, as_of: str = "") -> list[dict[str, Any]]:
    return [normalize_position(row, account, as_of=as_of) for row in rows if isinstance(row, dict)]


def normalize_position(row: dict[str, Any], account: RealLiveAccount | None = None, *, as_of: str = "") -> dict[str, Any]:
    symbol = str(row.get("ticker") or row.get("symbol") or row.get("contractDesc") or "").split(" ")[0].upper()
    quantity = float_value(row.get("position") or row.get("quantity"))
    avg_price = float_value(row.get("avgCost") or row.get("averageCost"))
    mark = float_value(row.get("mktPrice") or row.get("marketPrice"))
    market_value = float_value(row.get("mktValue") or row.get("marketValue"))
    normalized = {
        "account_key": account.account_key if account else "",
        "account_label": account.label if account else "",
        "account_class": account.account_class if account else "",
        "account_id": mask_account_id(account.account_id) if account else "",
        "asset_class": str(row.get("assetClass") or row.get("asset_class") or row.get("secType") or "STK"),
        "conid": str(row.get("conid") or row.get("con_id") or row.get("contractId") or ""),
        "currency": str(row.get("currency") or row.get("listingExchange") or ""),
        "symbol": symbol,
        "quantity": quantity,
        "avg_cost": avg_price,
        "avg_price": avg_price,
        "mark_price": mark,
        "market_value": market_value,
        "unrealized_pnl": float_value(row.get("unrealizedPnl")),
        "realized_pnl": float_value(row.get("realizedPnl")),
        "as_of": as_of,
        "raw_broker_position": row,
    }
    normalized["broker_position_snapshot"] = portfolio_position_from_row(normalized, account)
    return normalized


def normalize_account_summary(summary: Any) -> dict[str, Any]:
    if not isinstance(summary, dict):
        return {}
    return {str(key): value for key, value in summary.items() if any(token in str(key).lower() for token in ("netliquidation", "availablefunds", "buyingpower", "cashbalance", "totalcashvalue"))}


def normalize_ledger(ledger: Any) -> dict[str, Any]:
    if not isinstance(ledger, dict):
        return {}
    normalized: dict[str, Any] = {}
    for key, value in ledger.items():
        if isinstance(value, dict):
            normalized[str(key)] = {str(item_key): item_value for item_key, item_value in value.items()}
        else:
            normalized[str(key)] = value
    return normalized


def broker_balances(summary: dict[str, Any], ledger: dict[str, Any], account: RealLiveAccount) -> dict[str, Any]:
    cash = first_summary_amount(summary, ("totalcashvalue", "cashbalance", "availablefunds"))
    available_funds = first_summary_amount(summary, ("availablefunds", "cashbalance", "totalcashvalue"))
    buying_power = first_summary_amount(summary, ("buyingpower", "availablefunds"))
    net_liquidation = first_summary_amount(summary, ("netliquidation", "netliquidationvalue"))
    currency = first_summary_currency(summary) or first_ledger_currency(ledger) or "USD"
    return {
        "account_key": account.account_key,
        "account_label": account.label,
        "account_class": account.account_class,
        "account_id": mask_account_id(account.account_id),
        "available_funds": available_funds,
        "buying_power": buying_power,
        "cash": cash,
        "currency": currency,
        "net_liquidation": net_liquidation,
        "source": "ibkr",
    }


def normalize_ibkr_trades(
    payload: Any,
    *,
    selected_by_id: dict[str, RealLiveAccount] | None = None,
    fallback_account: RealLiveAccount | None = None,
) -> list[dict[str, Any]]:
    raw_trades = payload.get("trades", []) if isinstance(payload, dict) else payload if isinstance(payload, list) else []
    rows: list[dict[str, Any]] = []
    for item in raw_trades:
        if not isinstance(item, dict):
            continue
        account = account_from_broker_row(item, selected_by_id, fallback_account)
        account_id = broker_account_id(item)
        price = float_value(item.get("price") or item.get("tradePrice") or item.get("executionPrice"))
        quantity = float_value(item.get("quantity") or item.get("size") or item.get("shares"))
        side = str(item.get("side") or item.get("buySell") or "").upper()
        row = {
            "account_key": account.account_key if account else "",
            "account_label": account.label if account else "",
            "account_class": account.account_class if account else "",
            "account_id": mask_account_id(account_id or account.account_id if account else account_id),
            "broker_order_id": str(item.get("orderId") or item.get("order_id") or ""),
            "commission": optional_float(item.get("commission")),
            "conid": str(item.get("conid") or item.get("con_id") or ""),
            "execution_id": str(item.get("execution_id") or item.get("execId") or item.get("tradeId") or item.get("id") or ""),
            "fill_price": price,
            "filled_quantity": quantity,
            "gross_amount": price * quantity if price and quantity else 0,
            "side": side,
            "symbol": str(item.get("ticker") or item.get("symbol") or "").upper(),
            "timestamp": item.get("time") or item.get("trade_time") or item.get("executionTime") or "",
            "raw_broker_execution": item,
        }
        row["broker_execution_fill"] = execution_fill_from_row(row, account, item)
        rows.append(row)
    return rows


def normalize_pnl_rows(payload: Any, accounts: list[RealLiveAccount]) -> list[dict[str, Any]]:
    if not isinstance(payload, dict):
        return []
    rows: list[dict[str, Any]] = []
    by_id = {account.account_id: account for account in accounts if account.account_id}
    raw_upnl = payload.get("upnl") if isinstance(payload.get("upnl"), dict) else {}
    raw_dpl = payload.get("dpl") if isinstance(payload.get("dpl"), dict) else {}
    raw_nlv = payload.get("nl") if isinstance(payload.get("nl"), dict) else payload.get("nlv") if isinstance(payload.get("nlv"), dict) else {}
    for account_id, account in by_id.items():
        rows.append(
            {
                "account_key": account.account_key,
                "account_label": account.label,
                "account_class": account.account_class,
                "account_id": mask_account_id(account_id),
                "daily_pnl": float_value(raw_dpl.get(account_id)),
                "net_liquidation": float_value(raw_nlv.get(account_id)),
                "unrealized_pnl": float_value(raw_upnl.get(account_id)),
                "source": "ibkr",
            }
        )
    return rows


def broker_account_snapshot(
    summary: dict[str, Any],
    ledger: dict[str, Any],
    account: RealLiveAccount,
    raw_positions: Any,
    raw_summary: Any,
    raw_ledger: Any,
    as_of: str,
) -> dict[str, Any]:
    balances = broker_balances(summary, ledger, account)
    positions = tuple(
        PortfolioPosition(
            account_id=mask_account_id(account.account_id),
            avg_cost=float_value(position.get("avgCost") or position.get("averageCost")),
            conid=int_value(position.get("conid") or position.get("con_id") or position.get("contractId")),
            currency=str(position.get("currency") or "USD"),
            market_price=float_value(position.get("mktPrice") or position.get("marketPrice")),
            market_value=float_value(position.get("mktValue") or position.get("marketValue")),
            quantity=float_value(position.get("position") or position.get("quantity")),
            realized_pnl=float_value(position.get("realizedPnl")),
            ticker=str(position.get("ticker") or position.get("symbol") or position.get("contractDesc") or "").split(" ")[0].upper(),
            unrealized_pnl=float_value(position.get("unrealizedPnl")),
        )
        for position in position_rows(raw_positions)
    )
    snapshot = AccountSnapshot(
        account_id=mask_account_id(account.account_id),
        account_type=account.account_class,
        buying_power=float_value(balances.get("buying_power")),
        cash=float_value(balances.get("cash")),
        currency=str(balances.get("currency") or "USD"),
        equity=float_value(balances.get("net_liquidation")),
        excess_liquidity=first_summary_amount(summary, ("excessliquidity", "availablefunds")),
        gross_position_value=first_summary_amount(summary, ("grosspositionvalue",)),
        net_liquidation=float_value(balances.get("net_liquidation")),
        positions=positions,
        raw={"as_of": as_of, "summary": raw_summary, "ledger": raw_ledger},
    )
    return asdict(snapshot)


def order_snapshot_from_row(row: dict[str, Any], account: RealLiveAccount | None, *, raw: Any) -> dict[str, Any]:
    quantity = float_value(row.get("quantity"))
    filled_quantity = float_value(row.get("filled_quantity"))
    remaining_quantity = float_value(row.get("remaining_quantity"))
    snapshot = OrderSnapshot(
        account_id=str(row.get("account_id") or (mask_account_id(account.account_id) if account else "")),
        avg_filled_price=float_value(row.get("avg_fill_price")),
        conid=int_value(row.get("conid")),
        currency=str(row.get("currency") or "USD"),
        filled_quantity=filled_quantity,
        limit_price=optional_float(row.get("limit_price")),
        order_id=str(row.get("broker_order_id") or ""),
        order_type=str(row.get("order_type") or ""),
        remaining_quantity=remaining_quantity if remaining_quantity else max(0.0, quantity - filled_quantity),
        side=str(row.get("side") or "").upper(),
        status=broker_order_status(str(row.get("status") or "")),
        submitted_at=parse_broker_datetime(str(row.get("submitted_at") or row.get("updated_at") or "")),
        ticker=str(row.get("symbol") or "").upper(),
        total_quantity=quantity,
        raw=raw if isinstance(raw, dict) else {"response": raw},
    )
    return asdict(snapshot)


def portfolio_position_from_row(row: dict[str, Any], account: RealLiveAccount | None) -> dict[str, Any]:
    snapshot = PortfolioPosition(
        account_id=str(row.get("account_id") or (mask_account_id(account.account_id) if account else "")),
        avg_cost=float_value(row.get("avg_cost") or row.get("avg_price")),
        conid=int_value(row.get("conid")),
        currency=str(row.get("currency") or "USD"),
        market_price=float_value(row.get("mark_price") or row.get("mark")),
        market_value=float_value(row.get("market_value")),
        quantity=float_value(row.get("quantity")),
        realized_pnl=float_value(row.get("realized_pnl")),
        ticker=str(row.get("symbol") or "").upper(),
        unrealized_pnl=float_value(row.get("unrealized_pnl")),
    )
    return asdict(snapshot)


def execution_fill_from_row(row: dict[str, Any], account: RealLiveAccount | None, raw: dict[str, Any]) -> dict[str, Any]:
    fill_price = float_value(row.get("fill_price"))
    fill_quantity = float_value(row.get("filled_quantity"))
    snapshot = ExecutionFill(
        account_id=str(row.get("account_id") or (mask_account_id(account.account_id) if account else "")),
        avg_price_after_fill=float_value(raw.get("avgPrice") or fill_price),
        commission=float_value(row.get("commission")),
        conid=int_value(row.get("conid")),
        currency=str(raw.get("currency") or "USD"),
        execution_id=str(row.get("execution_id") or ""),
        fill_price=fill_price,
        fill_quantity=fill_quantity,
        order_id=str(row.get("broker_order_id") or ""),
        remaining_quantity=float_value(raw.get("remainingQuantity") or raw.get("remaining")),
        side=str(row.get("side") or "").upper(),
        ticker=str(row.get("symbol") or "").upper(),
        ts=parse_broker_datetime(str(row.get("timestamp") or "")),
    )
    return asdict(snapshot)


def position_rows(payload: Any) -> list[dict[str, Any]]:
    if isinstance(payload, list):
        return [item for item in payload if isinstance(item, dict)]
    if isinstance(payload, dict):
        raw_rows = payload.get("positions") or payload.get("data") or payload.get("results") or []
        if isinstance(raw_rows, list):
            return [item for item in raw_rows if isinstance(item, dict)]
    return []


def account_from_broker_row(item: dict[str, Any], selected_by_id: dict[str, RealLiveAccount] | None, fallback_account: RealLiveAccount | None) -> RealLiveAccount | None:
    account_id = broker_account_id(item)
    if account_id and selected_by_id and account_id in selected_by_id:
        return selected_by_id[account_id]
    return fallback_account


def account_row_matches_selection(row: dict[str, Any], selected_by_key: dict[str, RealLiveAccount], selected_account_ids: set[str]) -> bool:
    account_key = str(row.get("account_key") or "")
    if account_key and account_key in selected_by_key:
        return True
    raw_account_id = str(row.get("account_id") or "")
    return bool(raw_account_id and raw_account_id in {mask_account_id(account_id) for account_id in selected_account_ids})


def broker_account_id(item: dict[str, Any]) -> str:
    return str(item.get("acct") or item.get("acctId") or item.get("account") or item.get("accountId") or item.get("account_id") or "")


def first_summary_amount(summary: dict[str, Any], key_tokens: tuple[str, ...]) -> float:
    for key, value in summary.items():
        lower_key = key.lower()
        if any(token in lower_key for token in key_tokens):
            amount = amount_from_summary_value(value)
            if amount is not None:
                return amount
    return 0.0


def amount_from_summary_value(value: Any) -> float | None:
    if isinstance(value, dict):
        for key in ("amount", "value", "val"):
            if key in value:
                return float_value(value.get(key))
    if isinstance(value, (int, float, str)):
        return float_value(value)
    return None


def first_summary_currency(summary: dict[str, Any]) -> str:
    for value in summary.values():
        if isinstance(value, dict) and value.get("currency"):
            return str(value.get("currency"))
    return ""


def first_ledger_currency(ledger: dict[str, Any]) -> str:
    for key, value in ledger.items():
        if isinstance(value, dict):
            if value.get("currency"):
                return str(value.get("currency"))
            if key and len(key) == 3:
                return str(key)
    return ""


def optional_float(value: Any) -> float | None:
    if value is None or value == "":
        return None
    return float_value(value)


def int_value(value: Any) -> int:
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return 0


def parse_broker_datetime(value: str) -> datetime:
    text = (value or "").strip()
    if text:
        for candidate in (text, text.replace("Z", "+00:00")):
            try:
                parsed = datetime.fromisoformat(candidate)
                return parsed if parsed.tzinfo else parsed.replace(tzinfo=NEW_YORK)
            except ValueError:
                pass
    return datetime.now(NEW_YORK)


def broker_order_status(value: str):
    normalized = value.strip().lower().replace(" ", "_")
    if normalized in {"submitted", "presubmitted", "pending_submit", "inactive", "needs_reply"}:
        return "submitted"
    if normalized in {"filled", "complete"}:
        return "filled"
    if normalized in {"partially_filled", "partial", "partfilled"}:
        return "partially_filled"
    if normalized in {"cancelled", "canceled"}:
        return "cancelled"
    if normalized in {"rejected", "inactive_rejected"}:
        return "rejected"
    return "pending_submit"


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


def apply_tradable_filter_to_scanner_payload(payload: dict[str, Any]) -> dict[str, Any]:
    rows = payload.get("rows", []) if isinstance(payload, dict) else []
    if not isinstance(rows, list) or not rows:
        return payload
    filtered_rows, metadata = filter_tradable_rows([row for row in rows if isinstance(row, dict)])
    filtered = dict(payload)
    filtered["rows"] = filtered_rows
    filtered["row_count"] = len(filtered_rows)
    filtered["market_rows"] = filtered_rows
    filtered["market_row_count"] = len(filtered_rows)
    filtered["tradable_filter"] = metadata
    status = dict(filtered.get("status") or {})
    status["tradable_filter"] = metadata
    filtered["status"] = status
    return filtered


def filter_tradable_rows(rows: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    symbols = sorted({scanner_row_symbol(row) for row in rows if scanner_row_symbol(row)})
    if not symbols:
        return [], {"enabled": True, "checked": 0, "allowed": 0, "blocked": len(rows), "message": "No symbols to validate."}
    tradable = tradable_symbol_map(symbols)
    filtered: list[dict[str, Any]] = []
    blocked = 0
    for row in rows:
        symbol = scanner_row_symbol(row)
        match = tradable.get(symbol)
        if not match or not match.get("is_tradable"):
            blocked += 1
            continue
        enriched = dict(row)
        enriched["is_tradable"] = True
        enriched["ibkr_conid"] = int(match["ibkr_conid"])
        enriched["conid"] = int(match["ibkr_conid"])
        enriched["tradable_universe_date"] = match["universe_date"]
        enriched["exclusion_reason"] = match.get("exclusion_reason") or ""
        filtered.append(enriched)
    return filtered, {
        "enabled": True,
        "checked": len(symbols),
        "allowed": len(filtered),
        "blocked": blocked,
        "source": "q_live.feature_tradable_universe_v1",
    }


def require_tradable_symbol(symbol: str) -> dict[str, Any]:
    normalized = str(symbol or "").strip().upper()
    if not normalized:
        raise ValueError("Order symbol is required.")
    row = tradable_symbol_map([normalized]).get(normalized)
    if row and row.get("is_tradable"):
        return row
    reason = row.get("exclusion_reason") if row else "missing_from_latest_tradable_universe"
    raise RuntimeError(f"{normalized} is not tradable in the latest q_live tradable universe: {reason}.")


def tradable_symbol_map(symbols: list[str]) -> dict[str, dict[str, Any]]:
    normalized = sorted({str(symbol or "").strip().upper() for symbol in symbols if str(symbol or "").strip()})
    if not normalized:
        return {}
    config = market_gateway_config()
    client = ClickHouseHttpClient(config.read_clickhouse)
    feature_database = os.environ.get("REAL_LIVE_TRADABLE_UNIVERSE_DATABASE", "").strip() or config.write_clickhouse.database or config.read_clickhouse.database or "q_live"
    database = quote_identifier(feature_database)
    symbol_list = ", ".join(sql_literal(symbol) for symbol in normalized)
    rows = client.query_json(
        f"""
        WITH latest AS
        (
            SELECT max(universe_date) AS universe_date
            FROM {database}.feature_tradable_universe_v1 FINAL
        )
        SELECT
            toString(universe_date) AS universe_date_text,
            upper(ticker) AS ticker,
            toUInt8(is_tradable) AS is_tradable,
            ifNull(exclusion_reason, '') AS exclusion_reason,
            toUInt64OrZero(ifNull(ibkr_conid, '')) AS ibkr_conid
        FROM {database}.feature_tradable_universe_v1 FINAL
        WHERE universe_date = (SELECT universe_date FROM latest)
          AND upper(ticker) IN ({symbol_list})
        """,
        timeout=10,
    )
    return {
        str(row.get("ticker") or "").upper(): {
            "universe_date": str(row.get("universe_date_text") or ""),
            "ticker": str(row.get("ticker") or "").upper(),
            "is_tradable": bool(int(row.get("is_tradable") or 0)),
            "exclusion_reason": str(row.get("exclusion_reason") or ""),
            "ibkr_conid": int(row.get("ibkr_conid") or 0),
        }
        for row in rows
        if str(row.get("ticker") or "").strip()
    }


def scanner_row_symbol(row: dict[str, Any]) -> str:
    return str(row.get("ticker") or row.get("symbol") or "").strip().upper()


def sql_literal(value: str) -> str:
    return "'" + value.replace("\\", "\\\\").replace("'", "\\'") + "'"


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


def ibkr_get_optional(path: str, *, timeout: int) -> tuple[Any, str]:
    try:
        return ibkr_get_json(path, timeout=timeout), ""
    except Exception as exc:
        return None, str(exc)


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


def mask_account_payload(payload: Any) -> Any:
    if isinstance(payload, dict):
        return {key: mask_account_payload_value(key, value) for key, value in payload.items()}
    if isinstance(payload, list):
        return [mask_account_payload(item) for item in payload]
    return payload


def mask_account_payload_value(key: Any, value: Any) -> Any:
    key_text = str(key).lower()
    if key_text in {"account", "accountid", "account_id", "acct", "acctid", "selectedaccount"}:
        if isinstance(value, str):
            return mask_account_id(value)
        if isinstance(value, list):
            return [mask_account_id(str(item)) if not isinstance(item, dict) else mask_account_payload(item) for item in value]
    return mask_account_payload(value)


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
