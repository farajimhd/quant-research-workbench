"""Normalized Strategy 1 broker match-state rows at a completed 100 ms boundary.

This is only the simulator state that cannot be rebuilt from the committed V4
OMS/fill journal and pinned ARTE liquidity bars. It deliberately does not store
JSON, market quotes, executions, or expired same-bucket liquidity consumption.
Projection is pure; a separate fenced writer must publish these rows before
they may participate in cold recovery. No resume gate uses this module yet.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from math import isfinite
from typing import Any, Mapping
from uuid import NAMESPACE_URL, uuid5

from src.backend.backtest_market_data import market_day_boundary
from src.trading_runtime.arte_journal_schema import TableContract
from src.trading_runtime.ibkr_schema import OPEN_ORDER_STATUSES, OrderStatus
from src.trading_runtime.strategy_one_protection_snapshot import _digest


ROOT = TableContract(
    "trading_strategy_one_broker_match_snapshot_v1",
    (("snapshot_id", "UUID"), ("run_id", "String"),
     ("snapshot_month", "Date"), ("session_date", "Date"),
     ("checkpoint_sequence", "UInt64"), ("boundary_ms", "UInt32"),
     ("initial_time", "DateTime64(6, 'UTC')"),
     ("next_order_id", "UInt64"), ("next_execution_id", "UInt64"),
     ("performance_complete", "UInt8"),
     ("performance_as_of", "Nullable(DateTime64(6, 'UTC'))"),
     ("unrealized", "Float64"), ("market_value", "Float64"),
     ("peak_unrealized", "Float64"), ("worst_unrealized", "Float64"),
     ("equity_peak", "Float64"), ("maximum_drawdown", "Float64"),
     ("account_count", "UInt32"), ("account_hash", "FixedString(64)"),
     ("position_count", "UInt32"), ("position_hash", "FixedString(64)"),
     ("open_order_count", "UInt32"), ("open_order_hash", "FixedString(64)"),
     ("ticker_count", "UInt32"), ("ticker_hash", "FixedString(64)"),
     ("mark_count", "UInt32"), ("mark_hash", "FixedString(64)"),
     ("content_hash", "FixedString(64)")),
    "toYYYYMM(snapshot_month)", "run_id, checkpoint_sequence, snapshot_id",
)
ACCOUNT = TableContract(
    "trading_strategy_one_broker_match_account_v1",
    (("snapshot_id", "UUID"), ("run_id", "String"),
     ("snapshot_month", "Date"), ("checkpoint_sequence", "UInt64"),
     ("account_id", "String"), ("cash", "Float64"),
     ("realized_pnl", "Float64"), ("content_hash", "FixedString(64)")),
    "toYYYYMM(snapshot_month)",
    "run_id, checkpoint_sequence, account_id",
)
POSITION = TableContract(
    "trading_strategy_one_broker_match_position_v1",
    (("snapshot_id", "UUID"), ("run_id", "String"),
     ("snapshot_month", "Date"), ("checkpoint_sequence", "UInt64"),
     ("account_id", "String"), ("conid", "UInt64"),
     ("ticker", "LowCardinality(String)"),
     ("quantity", "Float64"), ("avg_cost", "Float64"),
     ("realized_pnl", "Float64"), ("content_hash", "FixedString(64)")),
    "toYYYYMM(snapshot_month)",
    "run_id, checkpoint_sequence, account_id, conid",
)
OPEN_ORDER = TableContract(
    "trading_strategy_one_broker_match_open_order_v1",
    (("snapshot_id", "UUID"), ("run_id", "String"),
     ("snapshot_month", "Date"), ("checkpoint_sequence", "UInt64"),
     ("broker_order_id", "String"), ("account_id", "String"),
     ("client_order_id", "String"), ("conid", "UInt64"),
     ("ticker", "LowCardinality(String)"), ("status", "String"),
     ("submitted_at", "DateTime64(6, 'UTC')"),
     ("oca_group", "String"), ("filled", "Float64"),
     ("avg_price", "Float64"), ("commission_paid", "Float64"),
     ("stop_triggered", "UInt8"), ("trailing_reference", "Float64"),
     ("status_description", "String"),
     ("content_hash", "FixedString(64)")),
    "toYYYYMM(snapshot_month)",
    "run_id, checkpoint_sequence, broker_order_id",
)
TICKER = TableContract(
    "trading_strategy_one_broker_match_ticker_v1",
    (("snapshot_id", "UUID"), ("run_id", "String"),
     ("snapshot_month", "Date"), ("checkpoint_sequence", "UInt64"),
     ("ticker", "LowCardinality(String)"), ("last_boundary_ms", "UInt32"),
     ("has_mark", "UInt8"), ("mark", "Float64"),
     ("has_quote", "UInt8"), ("quote_timestamp_us", "UInt64"),
     ("content_hash", "FixedString(64)")),
    "toYYYYMM(snapshot_month)",
    "run_id, checkpoint_sequence, ticker",
)
MARK = TableContract(
    "trading_strategy_one_broker_match_performance_mark_v1",
    (("snapshot_id", "UUID"), ("run_id", "String"),
     ("snapshot_month", "Date"), ("checkpoint_sequence", "UInt64"),
     ("conid", "UInt64"), ("mark", "Float64"),
     ("unrealized", "Float64"), ("market_value", "Float64"),
     ("content_hash", "FixedString(64)")),
    "toYYYYMM(snapshot_month)",
    "run_id, checkpoint_sequence, conid",
)
TABLES = (ROOT, ACCOUNT, POSITION, OPEN_ORDER, TICKER, MARK)


@dataclass(frozen=True, slots=True)
class BrokerMatchSnapshotRows:
    snapshot: dict[str, Any]
    accounts: tuple[dict[str, Any], ...]
    positions: tuple[dict[str, Any], ...]
    open_orders: tuple[dict[str, Any], ...]
    tickers: tuple[dict[str, Any], ...]
    marks: tuple[dict[str, Any], ...]


def _float(value: Any, label: str) -> float:
    if type(value) not in (int, float) or not isfinite(value):
        raise ValueError(f"Broker match snapshot has invalid {label}")
    return float(value)


def _time(value: Any, label: str) -> datetime:
    parsed = value if isinstance(value, datetime) else datetime.fromisoformat(str(value))
    if parsed.tzinfo is None:
        raise ValueError(f"Broker match snapshot has naive {label}")
    return parsed


def _sealed(common: dict[str, Any], **fields: Any) -> dict[str, Any]:
    row = {**common, **fields}
    return {**row, "content_hash": _digest(row)}


def _family(rows: tuple[dict[str, Any], ...]) -> tuple[int, str]:
    return len(rows), _digest([row["content_hash"] for row in rows])


def project_broker_match_snapshot(
    *, run_id: str, session_date: date, checkpoint_sequence: int,
    boundary_ms: int, state: Mapping[str, Any],
) -> BrokerMatchSnapshotRows:
    """Project only non-replayable broker matching state, never a JSON payload.

    The caller must be at a fully completed global boundary. A future cold
    loader must independently prove that via the V4 cursor and Keeper head,
    then join pinned OMS/fill revisions and reload the last liquidity row for
    each ticker before admitting the next market boundary.
    """
    if (type(run_id) is not str or not run_id or not isinstance(session_date, date)
            or type(checkpoint_sequence) is not int or checkpoint_sequence < 1
            or type(boundary_ms) is not int or not 0 < boundary_ms <= 57_600_000
            or boundary_ms % 100 or not isinstance(state, Mapping)
            or state.get("schema_version") != 4 or state.get("bar_mode") is not True):
        raise ValueError("Broker match snapshot needs completed Strategy 1 bar state")
    origin = market_day_boundary(session_date, 0)
    global_at = origin.timestamp() + boundary_ms / 1000
    initial = _time(state.get("initial_time"), "initial time")
    if initial.timestamp() > global_at:
        raise ValueError("Broker initial time is after checkpoint")
    snapshot_id = str(uuid5(NAMESPACE_URL,
                            f"strategy-one-broker-match-v1:{run_id}:{checkpoint_sequence}"))
    common = dict(snapshot_id=snapshot_id, run_id=run_id,
                  snapshot_month=session_date.replace(day=1).isoformat(),
                  checkpoint_sequence=checkpoint_sequence)
    account_ids = tuple(state.get("account_ids") or ())
    cash = dict(state.get("cash") or {})
    realized = dict(state.get("realized_pnl") or {})
    source_positions = dict(state.get("positions") or {})
    if (not account_ids or tuple(sorted(set(account_ids))) != tuple(sorted(account_ids))
            or set(account_ids) != set(cash) or set(cash) != set(realized)
            or set(cash) != set(source_positions)):
        raise ValueError("Broker match snapshot account identities differ")
    accounts = tuple(_sealed(common, account_id=account,
                             cash=_float(cash[account], "cash"),
                             realized_pnl=_float(realized[account], "realized P&L"))
                     for account in sorted(account_ids))
    positions = []
    for account in sorted(account_ids):
        seen = set()
        for position in source_positions[account]:
            conid = int(position["conid"])
            if conid < 1 or conid in seen or not position["ticker"]:
                raise ValueError("Broker match snapshot position identity differs")
            seen.add(conid)
            positions.append(_sealed(
                common, account_id=account, conid=conid,
                ticker=str(position["ticker"]),
                quantity=_float(position["quantity"], "position quantity"),
                avg_cost=_float(position["avg_cost"], "average cost"),
                realized_pnl=_float(position["realized_pnl"], "position P&L")))
    positions.sort(key=lambda row: (row["account_id"], row["conid"]))
    open_orders = []
    seen_orders = set()
    for order in state.get("orders") or ():
        status = OrderStatus(str(order["status"]))
        if status not in OPEN_ORDER_STATUSES:
            continue
        request = dict(order["request"])
        order_id = str(order["order_id"])
        if (not order_id or order_id in seen_orders
                or request.get("acctId") not in cash
                or not request.get("cOID") or int(request.get("conid") or 0) < 1
                or not request.get("ticker")):
            raise ValueError("Broker match snapshot open order identity differs")
        seen_orders.add(order_id)
        submitted = _time(order["submitted_at"], "order submission")
        if submitted.timestamp() > global_at:
            raise ValueError("Broker open order starts after checkpoint")
        open_orders.append(_sealed(
            common, broker_order_id=order_id,
            account_id=str(request["acctId"]),
            client_order_id=str(request["cOID"]),
            conid=int(request["conid"]), ticker=str(request["ticker"]),
            status=status.value, submitted_at=submitted.isoformat(),
            oca_group=str(order.get("oca_group") or ""),
            filled=_float(order["filled"], "filled quantity"),
            avg_price=_float(order["avg_price"], "fill average"),
            commission_paid=_float(order["commission_paid"], "commission"),
            stop_triggered=int(bool(order["stop_triggered"])),
            trailing_reference=_float(order["trailing_reference"], "trailing reference"),
            status_description=str(order.get("status_description") or "")))
    open_orders.sort(key=lambda row: row["broker_order_id"])
    boundaries = dict(state.get("bar_boundaries") or {})
    bar_marks = dict(state.get("bar_marks_by_ticker") or {})
    quotes = dict(state.get("quotes_by_ticker") or {})
    if set(bar_marks) - set(boundaries) or set(quotes) - set(boundaries):
        raise ValueError("Broker quote or mark lacks a completed ticker boundary")
    tickers = []
    for ticker, value in sorted(boundaries.items()):
        at = _time(value, "ticker boundary")
        elapsed_ms = round((at - origin).total_seconds() * 1000)
        if (not ticker or not 0 < elapsed_ms <= boundary_ms
                or elapsed_ms % 100):
            raise ValueError("Broker ticker boundary is outside checkpoint")
        quote = quotes.get(ticker)
        quote_us = 0
        if quote is not None:
            quote_us = round(_time(quote["ts"], "quote time").timestamp() * 1_000_000)
            if quote_us > round(at.timestamp() * 1_000_000):
                raise ValueError("Broker ticker quote is from the future")
        tickers.append(_sealed(
            common, ticker=ticker, last_boundary_ms=elapsed_ms,
            has_mark=int(ticker in bar_marks),
            mark=_float(bar_marks.get(ticker, 0), "bar mark"),
            has_quote=int(quote is not None), quote_timestamp_us=quote_us))
    performance_marks = dict(state.get("performance_marks") or {})
    conid_marks = dict(state.get("marks") or {})
    if set(performance_marks) - set(conid_marks):
        raise ValueError("Broker performance path lacks a conid mark")
    marks = []
    for conid, value in sorted(conid_marks.items(), key=lambda pair: int(pair[0])):
        path = performance_marks.get(conid, (0, 0))
        if len(path) != 2 or int(conid) < 1:
            raise ValueError("Broker performance path is invalid")
        marks.append(_sealed(common, conid=int(conid),
                             mark=_float(value, "mark"),
                             unrealized=_float(path[0], "unrealized mark"),
                             market_value=_float(path[1], "market value mark")))
    performance = dict(state.get("performance_extrema") or {})
    as_of = performance.get("as_of") or None
    if as_of is not None and _time(as_of, "performance time").timestamp() > global_at:
        raise ValueError("Broker performance path is from the future")
    root = dict(**common, session_date=session_date.isoformat(),
                boundary_ms=boundary_ms, initial_time=initial.isoformat(),
                next_order_id=int(state["next_order_id"]),
                next_execution_id=int(state["next_execution_id"]),
                performance_complete=int(bool(performance["complete"])),
                performance_as_of=as_of)
    if root["next_order_id"] < 1 or root["next_execution_id"] < 1:
        raise ValueError("Broker counters are invalid")
    for name in ("unrealized", "market_value", "peak_unrealized",
                 "worst_unrealized", "equity_peak", "maximum_drawdown"):
        root[name] = _float(performance[name], name)
    families = (tuple(accounts), tuple(positions), tuple(open_orders),
                tuple(tickers), tuple(marks))
    for name, rows in zip(("account", "position", "open_order", "ticker", "mark"),
                          families, strict=True):
        root[f"{name}_count"], root[f"{name}_hash"] = _family(rows)
    return BrokerMatchSnapshotRows(
        {**root, "content_hash": _digest(root)}, *families)
