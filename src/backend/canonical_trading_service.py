from __future__ import annotations

import threading
import time
from decimal import Decimal
from typing import Any

from src.backend.real_live_trading_service import real_live_portfolio
from src.trading_runtime.domain import (
    BrokerEventEnvelope,
    BrokerEventType,
    BrokerAccount,
    BrokerProvider,
    TradingMode,
    TradingStateSnapshot,
    json_safe,
)
from src.trading_runtime.ibkr_normalizer import (
    normalize_account_values,
    normalize_execution,
    normalize_ledger,
    normalize_order,
    normalize_position_snapshot,
)
from src.trading_runtime.projector import TradingStateProjector
from src.trading_runtime.round_trips import derive_round_trip_trades
from src.trading_runtime.performance import build_performance_report, derive_trade_episodes, episodes_from_round_trips


_CACHE_LOCK = threading.Lock()
_CACHE: dict[tuple[str, str], tuple[float, dict[str, Any]]] = {}
_CACHE_SECONDS = 2.0


def canonical_trading_state(
    *,
    mode: str = "paper",
    account_type: str = "paper",
    account_keys: str = "",
    run_dir: str = "",
    refresh: bool = False,
) -> dict[str, Any]:
    normalized_mode = str(mode or account_type).lower()
    if normalized_mode in {"live", "paper"}:
        return canonical_live_state(account_type if account_type else normalized_mode, account_keys, refresh=refresh)
    if normalized_mode in {"backtest", "backtest_debug"}:
        if not run_dir:
            raise ValueError("run_dir is required for backtest canonical state")
        from pathlib import Path
        from src.backend.canonical_backtest_service import canonical_backtest_state

        return canonical_backtest_state(Path(run_dir))
    raise ValueError(f"Unsupported canonical trading mode: {mode}")


def canonical_live_state(account_type: str = "paper", account_keys: str = "", *, refresh: bool = False) -> dict[str, Any]:
    cache_key = (account_type, account_keys)
    now = time.monotonic()
    with _CACHE_LOCK:
        cached = _CACHE.get(cache_key)
        if cached and not refresh and now - cached[0] <= _CACHE_SECONDS:
            return cached[1]
    raw = real_live_portfolio(account_type, account_keys=account_keys)
    mode = TradingMode.PAPER if all(str(row.get("trading_mode") or "").lower() == "paper" for row in raw.get("portfolios", [])) else TradingMode.LIVE
    projector = TradingStateProjector(mode, BrokerProvider.IBKR_CPAPI)
    accounts: list[BrokerAccount] = []
    for portfolio in raw.get("portfolios", []):
        account_id = str(portfolio.get("account_id") or portfolio.get("account_key") or "")
        snapshot_raw = portfolio.get("broker_account_snapshot", {}).get("raw", {})
        summary_raw = snapshot_raw.get("summary") or portfolio.get("summary") or {}
        ledger_raw = snapshot_raw.get("ledger") or portfolio.get("ledger") or {}
        accounts.append(
            BrokerAccount(
                provider=BrokerProvider.IBKR_CPAPI,
                account_id=account_id,
                base_currency=str(portfolio.get("balances", {}).get("currency") or "USD"),
                account_type=str(portfolio.get("account_class") or ""),
                alias=str(portfolio.get("account_key") or ""),
                title=str(portfolio.get("label") or ""),
                can_view=True,
                can_trade=True,
                raw={"account_key": portfolio.get("account_key"), "trading_mode": portfolio.get("trading_mode")},
            )
        )
        projector.set_account_values(normalize_account_values(summary_raw, account_id))
        projector.merge_ledger(normalize_ledger(ledger_raw, account_id))
        position_payload = [row.get("raw_broker_position") or row for row in portfolio.get("positions", [])]
        manifest, positions = normalize_position_snapshot(position_payload, account_id)
        projector.apply_position_snapshot(account_id, manifest.snapshot_id, manifest.complete, positions)
    projector.set_accounts(accounts)
    orders = [normalize_order(row.get("raw_broker_order") or row) for row in raw.get("orders", [])]
    executions = [normalize_execution(row.get("raw_broker_execution") or row) for row in raw.get("executions", [])]
    projector.set_orders(orders)
    projector.set_executions(executions)
    for row in orders:
        projector.record_activity(
            BrokerEventEnvelope.create(
                event_type=BrokerEventType.ORDER_STATUS_CHANGED,
                provider=BrokerProvider.IBKR_CPAPI,
                mode=mode,
                account_id=row.account_id,
                payload=json_safe(row.raw),
                source_event_time=row.source_event_time,
                broker_order_id=row.broker_order_id,
                client_order_id=row.client_order_id,
            )
        )
    for row in executions:
        projector.record_activity(
            BrokerEventEnvelope.create(
                event_type=BrokerEventType.EXECUTION_REPORTED,
                provider=BrokerProvider.IBKR_CPAPI,
                mode=mode,
                account_id=row.account_id,
                payload=json_safe(row.raw),
                source_event_time=row.source_event_time,
                broker_order_id=row.broker_order_id,
                client_order_id=row.client_order_id,
                execution_id=row.execution_id,
            )
        )
    projector.closed_trades = {row.trade_id: row for row in derive_round_trip_trades(list(projector.executions.values()))}
    errors = raw.get("errors") or []
    projector.complete = not errors and all(account.account_id in projector.last_complete_position_snapshot for account in accounts)
    projector.stale = bool(errors)
    projector.stale_reason = "; ".join(str(item.get("message") or item) for item in errors) if errors else ""
    payload = trading_state_payload(projector.snapshot())
    with _CACHE_LOCK:
        _CACHE[cache_key] = (now, payload)
    return payload


def trading_state_payload(snapshot: TradingStateSnapshot) -> dict[str, Any]:
    payload = snapshot.to_dict()
    metrics = portfolio_metrics(payload.get("account_values", []), payload.get("ledger", []), payload.get("positions", []))
    payload["portfolio"] = {
        "metrics": metrics,
        "exposure": portfolio_exposure(payload.get("positions", [])),
        "position_count": len(payload.get("positions", [])),
        "working_order_count": sum(1 for row in payload.get("orders", []) if not row.get("terminal")),
        "pending_commission_count": sum(1 for row in payload.get("executions", []) if row.get("commission_status") != "final"),
    }
    payload["closed_trades_note"] = "Derived FIFO round trips for strategy analytics; not IBKR tax lots or IBKR trade confirmations."
    episodes = (
        episodes_from_round_trips(snapshot.closed_trades)
        if snapshot.mode in {TradingMode.BACKTEST, TradingMode.BACKTEST_DEBUG} and snapshot.closed_trades
        else derive_trade_episodes(snapshot.executions)
    )
    payload["performance_journal"] = build_performance_report(episodes, snapshot.executions, snapshot.orders)
    return payload


def portfolio_metrics(account_values: list[dict[str, Any]], ledger: list[dict[str, Any]], positions: list[dict[str, Any]]) -> dict[str, Any]:
    def amount(*keys: str) -> Decimal:
        wanted = {key.lower() for key in keys}
        candidates = [row for row in account_values if str(row.get("key") or "").lower() in wanted and row.get("segment") == "base"]
        if candidates:
            return Decimal(str(candidates[-1].get("monetary_value") or candidates[-1].get("value") or 0))
        for ledger_row in ledger:
            if not ledger_row.get("is_base"):
                continue
            values = ledger_row.get("values") or {}
            for key in keys:
                for source_key, value in values.items():
                    if source_key.lower() == key.lower():
                        return Decimal(str(value or 0))
        return Decimal("0")

    unrealized = sum(Decimal(str(row.get("unrealized_pnl") or 0)) for row in positions)
    realized = sum(Decimal(str(row.get("realized_pnl") or 0)) for row in positions)
    return json_safe(
        {
            "net_liquidation": amount("netliquidation", "netliquidationvalue"),
            "available_funds": amount("availablefunds"),
            "excess_liquidity": amount("excessliquidity"),
            "buying_power": amount("buyingpower"),
            "total_cash": amount("totalcashvalue", "cashbalance"),
            "gross_position_value": amount("grosspositionvalue"),
            "unrealized_pnl": unrealized,
            "realized_pnl": realized,
        }
    )


def portfolio_exposure(positions: list[dict[str, Any]]) -> dict[str, Any]:
    long_value = Decimal("0")
    short_value = Decimal("0")
    by_currency: dict[str, Decimal] = {}
    by_asset_class: dict[str, Decimal] = {}
    for row in positions:
        value = Decimal(str(row.get("market_value") or 0))
        if value >= 0:
            long_value += value
        else:
            short_value += abs(value)
        instrument = row.get("instrument") or {}
        currency = str(instrument.get("currency") or "USD")
        asset_class = str(instrument.get("security_type") or "UNKNOWN")
        by_currency[currency] = by_currency.get(currency, Decimal("0")) + value
        by_asset_class[asset_class] = by_asset_class.get(asset_class, Decimal("0")) + value
    return json_safe(
        {
            "long_value": long_value,
            "short_value": short_value,
            "net_value": long_value - short_value,
            "gross_value": long_value + short_value,
            "by_currency": by_currency,
            "by_asset_class": by_asset_class,
        }
    )
