from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any
from uuid import NAMESPACE_URL, uuid5

import polars as pl

from src.backtest.results import read_run_metadata
from src.trading_runtime.domain import (
    AccountValue,
    BrokerAccount,
    BrokerEventEnvelope,
    BrokerEventType,
    BrokerProvider,
    Execution,
    InstrumentContract,
    OrderLifecycleState,
    OrderState,
    PositionState,
    RoundTripTrade,
    TradingMode,
)
from src.trading_runtime.projector import TradingStateProjector
from src.backend.canonical_trading_service import trading_state_payload


def canonical_backtest_state(run_dir: Path) -> dict[str, Any]:
    """Adapt completed v1 run artifacts to the same v2 query model used by live trading."""
    metadata = read_run_metadata(run_dir) or {}
    run_id = str(metadata.get("run_id") or run_dir.name)
    strategy_id = str(metadata.get("strategy_id") or metadata.get("strategy_name") or "")
    strategy_revision = _integer(metadata.get("strategy_revision") or metadata.get("strategy_version"))
    account_id = f"backtest:{run_id}"
    projector = TradingStateProjector(TradingMode.BACKTEST, BrokerProvider.SIMULATED)
    projector.set_accounts([
        BrokerAccount(
            provider=BrokerProvider.SIMULATED,
            account_id=account_id,
            base_currency=str((metadata.get("config") or {}).get("base_currency") or "USD"),
            account_type="BACKTEST",
            alias=run_id,
            title=str(metadata.get("run_name") or run_id),
            can_view=True,
            can_trade=False,
            valid_at=_time(metadata.get("created_at")),
            raw={"run_id": run_id, "strategy_name": metadata.get("strategy_name"), "strategy_version": metadata.get("strategy_version")},
        )
    ])
    portfolio_rows = _read(run_dir / "portfolio.parquet")
    if portfolio_rows:
        latest = portfolio_rows[-1]
        observed = _time(latest.get("timestamp"))
        projector.set_account_values([
            _account_value(account_id, "netliquidation", latest.get("equity"), observed),
            _account_value(account_id, "totalcashvalue", latest.get("cash"), observed),
            _account_value(account_id, "grosspositionvalue", latest.get("gross_exposure"), observed),
            _account_value(account_id, "realizedpnl", latest.get("realized_pnl"), observed),
            _account_value(account_id, "unrealizedpnl", latest.get("open_unrealized_pnl"), observed),
        ])
    position_rows = _latest_snapshot(_read(run_dir / "positions.parquet"), "timestamp")
    snapshot_id = f"backtest:{run_id}:final"
    positions = [
        PositionState(
            snapshot_id=snapshot_id,
            account_id=account_id,
            instrument=_instrument(row.get("symbol")),
            quantity=_decimal(row.get("quantity")),
            market_price=_decimal(row.get("mark_price")),
            market_value=_decimal(row.get("market_value")),
            average_cost=_decimal(row.get("entry_price")),
            average_price=_decimal(row.get("entry_price")),
            realized_pnl=Decimal("0"),
            unrealized_pnl=_decimal(row.get("unrealized_pnl")),
            source_event_time=_time(row.get("timestamp")),
            raw=row,
        )
        for row in position_rows
    ]
    projector.apply_position_snapshot(account_id, snapshot_id, True, positions)
    orders = [_order(account_id, row) for row in _latest_by(_read(run_dir / "orders.parquet"), "order_id")]
    executions = [_execution(account_id, run_id, strategy_id, strategy_revision, row) for row in _read(run_dir / "fills.parquet")]
    projector.set_orders(orders)
    projector.set_executions(executions)
    projector.closed_trades = {trade.trade_id: trade for trade in (_trade(account_id, run_id, strategy_id, strategy_revision, index, row) for index, row in enumerate(_read(run_dir / "trades.parquet")))}
    for order in orders:
        projector.record_activity(_activity(BrokerEventType.ORDER_STATUS_CHANGED, account_id, run_id, order.source_event_time, order.raw, broker_order_id=order.broker_order_id))
    for execution in executions:
        projector.record_activity(_activity(BrokerEventType.EXECUTION_REPORTED, account_id, run_id, execution.source_event_time, execution.raw, execution_id=execution.execution_id, broker_order_id=execution.broker_order_id))
    projector.complete = str(metadata.get("status") or "").lower() == "completed"
    projector.stale = False
    projector.stale_reason = ""
    return trading_state_payload(projector.snapshot())


def _read(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    frame = pl.read_parquet(path)
    if frame.is_empty():
        return []
    time_columns = [name for name in ("timestamp", "filled_at", "created_at", "exit_time") if name in frame.columns]
    if time_columns:
        frame = frame.sort(time_columns[0])
    return frame.to_dicts()


def _latest_snapshot(rows: list[dict[str, Any]], key: str) -> list[dict[str, Any]]:
    if not rows:
        return []
    latest = max(_time(row.get(key)) for row in rows)
    return [row for row in rows if _time(row.get(key)) == latest]


def _latest_by(rows: list[dict[str, Any]], key: str) -> list[dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    for row in rows:
        result[str(row.get(key) or "")] = row
    return list(result.values())


def _instrument(symbol: Any) -> InstrumentContract:
    ticker = str(symbol or "").upper()
    return InstrumentContract(instrument_id=f"symbol:{ticker}:STK:USD", conid=0, symbol=ticker, security_type="STK", currency="USD")


def _order(account_id: str, row: dict[str, Any]) -> OrderState:
    raw_status = str(row.get("status") or "UNKNOWN")
    status_key = raw_status.upper()
    lifecycle = OrderLifecycleState.FILLED if "FILLED" in status_key else OrderLifecycleState.CANCELLED if "CANCEL" in status_key else OrderLifecycleState.REJECTED if "REJECT" in status_key else OrderLifecycleState.WORKING if status_key == "OPEN" else OrderLifecycleState.UNKNOWN
    quantity = _decimal(row.get("quantity"))
    filled = quantity if lifecycle in {OrderLifecycleState.FILLED, OrderLifecycleState.PARTIALLY_FILLED} else Decimal("0")
    return OrderState(
        account_id=account_id,
        instrument=_instrument(row.get("symbol")),
        lifecycle_state=lifecycle,
        broker_status_raw=raw_status,
        broker_order_id=str(row.get("order_id") or ""),
        client_order_id=f"backtest-{row.get('order_id')}",
        side=str(row.get("side") or "").upper(),
        order_type=str(row.get("order_type") or ""),
        time_in_force="BAR" if row.get("expire_on_bar_close") else "RUN",
        total_quantity=quantity,
        filled_quantity=filled,
        remaining_quantity=max(Decimal("0"), quantity - filled),
        average_fill_price=_decimal(row.get("fill_price")),
        limit_price=_optional_decimal(row.get("limit_price")),
        stop_price=_optional_decimal(row.get("stop_price")),
        terminal=lifecycle in {OrderLifecycleState.FILLED, OrderLifecycleState.CANCELLED, OrderLifecycleState.REJECTED},
        rejection_reason=str(row.get("reason") or "") if lifecycle == OrderLifecycleState.REJECTED else "",
        source_event_time=_time(row.get("filled_at") or row.get("created_at")),
        raw=row,
    )


def _execution(account_id: str, run_id: str, strategy_id: str, strategy_revision: int, row: dict[str, Any]) -> Execution:
    return Execution(
        execution_id=f"backtest-fill-{row.get('fill_id')}",
        account_id=account_id,
        instrument=_instrument(row.get("symbol")),
        side=str(row.get("side") or "").upper(),
        quantity=_decimal(row.get("quantity")),
        price=_decimal(row.get("fill_price")),
        source_event_time=_time(row.get("filled_at")),
        broker_order_id=str(row.get("order_id") or ""),
        commission=_decimal(row.get("total_fee")),
        commission_currency="USD",
        commission_status="final",
        strategy_id=strategy_id,
        strategy_revision=strategy_revision,
        run_id=run_id,
        setup=str(row.get("setup") or row.get("tag") or ""),
        exit_reason=str(row.get("exit_reason") or row.get("reason") or ""),
        signal_price=_optional_decimal(row.get("signal_price")),
        arrival_midpoint=_optional_decimal(row.get("arrival_midpoint")),
        planned_risk=_optional_decimal(row.get("planned_risk")),
        raw=row,
    )


def _trade(account_id: str, run_id: str, strategy_id: str, strategy_revision: int, index: int, row: dict[str, Any]) -> RoundTripTrade:
    trade_id = str(uuid5(NAMESPACE_URL, f"{run_id}:{index}:{row.get('symbol')}:{row.get('entry_time')}:{row.get('exit_time')}"))
    return RoundTripTrade(
        trade_id=trade_id,
        account_id=account_id,
        instrument=_instrument(row.get("symbol")),
        opened_at=_time(row.get("entry_time")),
        closed_at=_time(row.get("exit_time")),
        quantity=_decimal(row.get("quantity")),
        entry_price=_decimal(row.get("entry_price")),
        exit_price=_decimal(row.get("exit_price")),
        gross_pnl=_decimal(row.get("gross_pnl")),
        fees=_decimal(row.get("fees")),
        net_pnl=_decimal(row.get("pnl")),
        side="LONG",
        strategy_id=strategy_id,
        strategy_revision=strategy_revision,
        run_id=run_id,
        setup=str(row.get("setup") or row.get("tag") or ""),
        exit_reason=str(row.get("exit_reason") or ""),
        mae=_optional_decimal(row.get("mae")),
        mfe=_optional_decimal(row.get("mfe")),
        planned_risk=_optional_decimal(row.get("planned_risk")),
    )


def _account_value(account_id: str, key: str, value: Any, observed: datetime) -> AccountValue:
    return AccountValue(account_id=account_id, key=key, value=str(value or 0), monetary_value=_decimal(value), currency="USD", source_event_time=observed)


def _activity(event_type: BrokerEventType, account_id: str, run_id: str, observed: datetime, payload: dict[str, Any], **identity: Any) -> BrokerEventEnvelope:
    stable = str(uuid5(NAMESPACE_URL, f"{run_id}:{event_type.value}:{identity}:{observed.isoformat()}"))
    return BrokerEventEnvelope.create(event_type=event_type, provider=BrokerProvider.SIMULATED, mode=TradingMode.BACKTEST, account_id=account_id, payload=payload, source_event_time=observed, event_id=stable, run_id=run_id, **identity)


def _time(value: Any) -> datetime:
    if isinstance(value, datetime):
        return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)
    text = str(value or "").strip()
    if text:
        try:
            parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
            return parsed.replace(tzinfo=UTC) if parsed.tzinfo is None else parsed.astimezone(UTC)
        except ValueError:
            pass
    return datetime.fromtimestamp(0, UTC)


def _decimal(value: Any) -> Decimal:
    return Decimal(str(value or 0))


def _optional_decimal(value: Any) -> Decimal | None:
    return None if value in (None, "") else _decimal(value)


def _integer(value: Any) -> int:
    text = str(value or "").strip().lower().lstrip("v")
    try:
        return int(text)
    except ValueError:
        return 0
