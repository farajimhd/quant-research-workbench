from __future__ import annotations

from dataclasses import replace
from decimal import Decimal
from typing import Any
from zoneinfo import ZoneInfo

from src.backend.bounded_cache import BoundedTtlCache
from src.backend.lifecycle_contract import lifecycle_projection
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
from src.trading_runtime.performance import (
    build_performance_report,
    derive_position_lifecycles,
    derive_trade_episodes,
    episodes_from_round_trips,
)


_CACHE_SECONDS = 2.0
_CACHE = BoundedTtlCache[tuple[str, str], dict[str, Any]](
    max_entries=32,
    ttl_seconds=_CACHE_SECONDS,
    contract_revision="canonical-live-state.v1",
)
_NEW_YORK = ZoneInfo("America/New_York")


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
    cached = None if refresh else _CACHE.get(cache_key)
    if cached is not None:
        return cached
    snapshot = canonical_live_snapshot(account_type, account_keys)
    payload = trading_state_payload(snapshot)
    payload["lifecycle"] = lifecycle_projection(
        resource_type="trading_session_projection",
        resource_id=(
            f"{snapshot.mode.value}:" + ",".join(sorted(snapshot.account_ids))
        ),
        status="running" if snapshot.complete and not snapshot.stale else "blocked",
        authority="canonical_live_state",
        checkpoint={
            "status": "available" if snapshot.complete else "incomplete",
            "as_of": snapshot.as_of.isoformat(),
            "resume_supported": False,
        },
        error=snapshot.stale_reason if snapshot.stale else "",
        created_at=snapshot.as_of.isoformat(),
        updated_at=snapshot.as_of.isoformat(),
        mode=snapshot.mode.value,
    )
    _CACHE.set(cache_key, payload)
    return payload


def canonical_live_snapshot(
    account_type: str = "paper",
    account_keys: str = "",
) -> TradingStateSnapshot:
    """Return the typed, fresh canonical authority used for admission checks."""

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
    return projector.snapshot()


def trading_state_payload(
    snapshot: TradingStateSnapshot,
    *,
    include_strategy_activity: bool = True,
) -> dict[str, Any]:
    if (
        snapshot.mode in {TradingMode.BACKTEST, TradingMode.BACKTEST_DEBUG}
        and snapshot.executions
        and not snapshot.closed_trades
    ):
        snapshot = replace(
            snapshot,
            closed_trades=tuple(derive_round_trip_trades(list(snapshot.executions))),
        )
    payload = snapshot.to_dict()
    if snapshot.mode in {TradingMode.BACKTEST, TradingMode.BACKTEST_DEBUG}:
        _compact_historical_broker_projection(payload)
    metrics = portfolio_metrics(payload.get("account_values", []), payload.get("ledger", []), payload.get("positions", []))
    payload["portfolio"] = {
        "metrics": metrics,
        "exposure": portfolio_exposure(payload.get("positions", [])),
        "position_count": len(payload.get("positions", [])),
        "working_order_count": sum(1 for row in payload.get("orders", []) if not row.get("terminal")),
        "pending_commission_count": sum(1 for row in payload.get("executions", []) if row.get("commission_status") != "final"),
    }
    position_lifecycles = derive_position_lifecycles(
        snapshot.executions,
        snapshot.orders,
        snapshot.positions,
    )
    payload["position_lifecycles"] = position_lifecycles
    if snapshot.mode in {TradingMode.BACKTEST, TradingMode.BACKTEST_DEBUG}:
        # A completed historical trade is one flat-to-flat position lifecycle.
        # Partial fills remain available in executions; exposing FIFO matches
        # here makes one economic position appear as hundreds of trades.
        payload["closed_trades"] = [
            {
                **row,
                "trade_id": str(
                    row.get("episode_id") or row.get("lifecycle_id") or ""
                ),
            }
            for row in position_lifecycles
            if row.get("status") == "closed"
        ]
        payload["closed_trades_note"] = (
            "Completed flat-to-flat position lifecycles; individual partial fills "
            "remain available in execution detail."
        )
    else:
        payload["closed_trades_note"] = "Derived FIFO round trips for strategy analytics; not IBKR tax lots or IBKR trade confirmations."
    run_ids = sorted(
        {
            str(row.run_id).strip()
            for row in snapshot.executions
            if str(row.run_id).strip()
        }
    )
    payload["strategy_activity"] = []
    if include_strategy_activity and len(run_ids) == 1:
        # A historical canonical state is scoped to one immutable run. Include
        # the durable strategy decisions so Position Manager can present the
        # complete request -> order -> fill -> management -> exit lifecycle.
        from src.backend.trading_runtime_service import strategy_activity_payload

        payload["strategy_activity"] = strategy_activity_payload(
            as_of=snapshot.as_of,
            run_id=run_ids[0],
            limit=50_000,
        )["rows"]
    episodes = (
        derive_trade_episodes(snapshot.executions)
        if snapshot.executions
        else episodes_from_round_trips(snapshot.closed_trades)
    )
    payload["performance_snapshot"] = performance_snapshot(snapshot, metrics, episodes)
    payload["performance_journal"] = build_performance_report(episodes, snapshot.executions, snapshot.orders)
    return payload


def _compact_historical_broker_projection(payload: dict[str, Any]) -> None:
    """Keep immutable broker evidence durable while bounding browser payloads.

    Simulated partial fills repeat the complete strategy-intent metadata in
    every broker-native raw record. That raw evidence remains authoritative in
    the historical journal and restart checkpoint, but retransmitting it for
    thousands of fill parts made a single completed-run Canvas approach one
    gigabyte. The UI consumes typed canonical fields plus only the order timing
    and role hints retained below.
    """

    for order in payload.get("orders") or []:
        raw = dict(order.get("raw") or {})
        metadata = dict(raw.get("canonical_metadata") or {})
        order["raw"] = {
            key: raw[key]
            for key in ("submitted_at", "cancelled_at", "replaced_at")
            if raw.get(key) is not None
        }
        compact_metadata = {
            key: metadata[key]
            for key in ("execution_role", "fill_role", "action", "reason", "reason_code")
            if metadata.get(key) is not None
        }
        if compact_metadata:
            order["raw"]["canonical_metadata"] = compact_metadata
    for execution in payload.get("executions") or []:
        execution["raw"] = {}
    for activity in payload.get("activity") or []:
        activity["payload"] = {}


def performance_snapshot(snapshot: TradingStateSnapshot, metrics: dict[str, Any], episodes: list[Any]) -> dict[str, Any]:
    """Build the compact, point-in-time performance contract used across UIs.

    Daily realized P&L is derived from completed flat-to-flat episodes whose
    close falls on the snapshot's New York market date. Current unrealized P&L
    comes from the position snapshot. This keeps live, replay, and backtest
    consumers on the same causal definition instead of mixing account-lifetime
    realized values with a daily headline.
    """

    session_date = snapshot.as_of.astimezone(_NEW_YORK).date()
    realized_today = sum(
        (row.net_pnl for row in episodes if row.closed_at.astimezone(_NEW_YORK).date() == session_date),
        Decimal("0"),
    )
    unrealized = Decimal(str(metrics.get("unrealized_pnl") or 0))
    has_available_funds = _has_metric(snapshot, "availablefunds")
    available_cash = metrics.get("available_funds") if has_available_funds else metrics.get("total_cash")
    open_positions = sum(1 for row in snapshot.positions if row.quantity != 0)
    return json_safe(
        {
            "as_of": snapshot.as_of,
            "session_date": session_date.isoformat(),
            "net_pnl_today": realized_today + unrealized,
            "open_position_count": open_positions,
            "unrealized_pnl": unrealized,
            "realized_pnl_today": realized_today,
            "available_cash": Decimal(str(available_cash or 0)),
            "available_cash_basis": "available_funds" if has_available_funds else "total_cash",
        }
    )


def _has_metric(snapshot: TradingStateSnapshot, key: str) -> bool:
    wanted = key.lower()
    if any(row.key.lower() == wanted and row.segment == "base" for row in snapshot.account_values):
        return True
    return any(any(source_key.lower() == wanted for source_key in row.values) for row in snapshot.ledger if row.is_base)


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
    realized = amount("realizedpnl")
    if not any(
        row.get("is_base")
        and any(str(key).lower() == "realizedpnl" for key in (row.get("values") or {}))
        for row in ledger
    ):
        # Some broker/simulator payloads expose only one currency ledger and do
        # not label it BASE.  That ledger is still a better realized-P&L
        # authority than summing only currently open positions, which resets to
        # zero as soon as the account is flat.
        candidates = [
            Decimal(str(value or 0))
            for row in ledger
            for key, value in (row.get("values") or {}).items()
            if str(key).lower() == "realizedpnl"
        ]
        if len(candidates) == 1:
            realized = candidates[0]
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
