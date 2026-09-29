"""Pure V4 broker restoration from verified normalized rows.

This builds only an in-memory simulator payload. It does not replay synthetic
market events, access disk, or grant checkpoint resume authority.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from dataclasses import replace
from typing import Mapping

from src.backend.backtest_market_data import market_day_boundary
from src.backend.backtest_v4_broker_quote_restore import CompletedBrokerQuote
from src.trading_runtime.ibkr_schema import OrderRequest
from src.trading_runtime.strategy_one_broker_match_snapshot import (
    BrokerMatchSnapshotRows, verify_broker_match_snapshot,
)
from src.trading_runtime.strategy_one_protection_snapshot import _digest


def reconstruct_broker_match_state(
    broker: BrokerMatchSnapshotRows, *,
    requests_by_broker_id: Mapping[str, OrderRequest],
    quotes: Mapping[str, CompletedBrokerQuote],
) -> dict:
    """Reconstruct a simulator schema-4 payload at one completed boundary.

    OMS supplies the canonical request identity and metadata. The broker's
    effective quantity is separately persisted because OCA partial fills can
    resize a resting sibling without amending the OMS-requested quantity.
    The caller must separately verify V4 prefix, OMS lineage, and pinned ARTE
    liquidity rows before using this result. Historical executions are not
    reconstructed here, so this function alone cannot authorize resume.
    """
    verified = verify_broker_match_snapshot(broker)
    root = verified.snapshot
    day = date.fromisoformat(root["session_date"])
    origin = market_day_boundary(day, 0)
    account_ids = [row["account_id"] for row in verified.accounts]
    if (set(requests_by_broker_id) !=
            {row["broker_order_id"] for row in verified.open_orders}):
        raise RuntimeError("Broker restoration requires exact OMS order bindings")
    expected_quotes = {row["ticker"] for row in verified.tickers if row["has_quote"]}
    if set(quotes) != expected_quotes:
        raise RuntimeError("Broker restoration requires exact quote identities")
    boundaries = {}
    bar_marks = {}
    quote_states = {}
    for row in verified.tickers:
        ticker = row["ticker"]
        at = origin + timedelta(milliseconds=int(row["last_boundary_ms"]))
        boundaries[ticker] = at.astimezone(timezone.utc).isoformat()
        if row["has_mark"]:
            bar_marks[ticker] = float(row["mark"])
        if row["has_quote"]:
            quote = quotes[ticker]
            if (quote.ticker != ticker
                    or quote.boundary_ms != row["last_boundary_ms"]
                    or quote.quote_timestamp_us != row["quote_timestamp_us"]):
                raise RuntimeError("Broker restoration quote boundary differs")
            observed = datetime.fromtimestamp(
                quote.quote_timestamp_us / 1_000_000, tz=timezone.utc)
            quote_states[ticker] = dict(
                kind="quote", ticker=ticker, ts=observed.isoformat(),
                ingest_ts=at.astimezone(timezone.utc).isoformat(),
                source="arte.liquidity_100ms_v1", raw={
                    "liquidity_bar_snapshot": True,
                    "quote_timestamp_us": quote.quote_timestamp_us,
                }, conditions=(), indicators=(), ask_exchange=0,
                ask_price=quote.ask, ask_size=quote.ask_size,
                bid_exchange=0, bid_price=quote.bid, bid_size=quote.bid_size,
            )
    orders = []
    for row in verified.open_orders:
        request = requests_by_broker_id[row["broker_order_id"]]
        if (request.cOID != row["client_order_id"]
                or request.acctId != row["account_id"]
                or request.conid != row["conid"]
                or request.ticker != row["ticker"]):
            raise RuntimeError("Broker restoration OMS request differs")
        request = replace(
            request,
            quantity=(float(row["effective_quantity"])
                      if row["effective_quantity"] is not None else None),
            cashQty=(float(row["effective_cash_quantity"])
                     if row["effective_cash_quantity"] is not None else None),
        )
        request_payload = request.to_cpapi()
        request_payload.update({key: value for key, value in request.raw.items()
                                if key.startswith("canonical_")})
        if _digest(request_payload) != row["effective_request_hash"]:
            raise RuntimeError("Broker restoration effective request differs")
        orders.append(dict(
            request=request_payload, order_id=row["broker_order_id"],
            status=row["status"], submitted_at=row["submitted_at"],
            oca_group=row["oca_group"], filled=float(row["filled"]),
            avg_price=float(row["avg_price"]),
            commission_paid=float(row["commission_paid"]),
            stop_triggered=bool(row["stop_triggered"]),
            trailing_reference=float(row["trailing_reference"]),
            status_description=row["status_description"],
        ))
    performance = {
        "complete": bool(root["performance_complete"]),
        "as_of": root["performance_as_of"] or "",
    }
    for name in ("unrealized", "market_value", "peak_unrealized",
                 "worst_unrealized", "equity_peak", "maximum_drawdown"):
        performance[name] = float(root[name])
    return dict(
        schema_version=4, initial_time=root["initial_time"],
        account_ids=account_ids,
        cash={row["account_id"]: float(row["cash"]) for row in verified.accounts},
        realized_pnl={row["account_id"]: float(row["realized_pnl"])
                      for row in verified.accounts},
        positions={account: [dict(
            conid=row["conid"], ticker=row["ticker"],
            quantity=float(row["quantity"]), avg_cost=float(row["avg_cost"]),
            realized_pnl=float(row["realized_pnl"]))
            for row in verified.positions if row["account_id"] == account]
            for account in account_ids},
        orders=orders, liquidity_consumed={}, bar_mode=True,
        bar_boundaries=boundaries, bar_marks_by_ticker=bar_marks,
        quotes_by_ticker=quote_states,
        marks={str(row["conid"]): float(row["mark"]) for row in verified.marks},
        performance_marks={str(row["conid"]): [float(row["unrealized"]),
                          float(row["market_value"])] for row in verified.marks
                          if row["has_performance_path"]},
        performance_extrema=performance,
        next_order_id=root["next_order_id"],
        next_execution_id=root["next_execution_id"],
    )
