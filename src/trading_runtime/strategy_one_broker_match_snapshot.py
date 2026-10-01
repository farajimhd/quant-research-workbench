"""Normalized Strategy 1 broker match-state rows at a completed 100 ms boundary.

This is only the simulator state that cannot be rebuilt from the committed V4
OMS/fill journal and pinned ARTE liquidity bars. It deliberately does not store
JSON, market quotes, executions, or expired same-bucket liquidity consumption.
Projection is pure; a separate fenced writer publishes these rows before the
cold-resume gate may use them. V5 stores simulator Float64 bit patterns as
named UInt64 scalars: ClickHouse 26.3 JSON Float64 parsing can change one ULP,
which would corrupt an exact resume even though the table is normalized.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timezone
from hashlib import sha256
import json
from math import isfinite
from struct import pack, unpack
from typing import Any, Mapping, Protocol
from uuid import NAMESPACE_URL, UUID, uuid5

from src.backend.backtest_market_data import market_day_boundary
from src.trading_runtime.arte_journal_schema import TableContract
from src.trading_runtime.ibkr_schema import OPEN_ORDER_STATUSES, OrderStatus
from src.trading_runtime.keeper_session import ManagedKeeperSession
from src.trading_runtime.strategy_one_protection_snapshot import _digest


ROOT = TableContract(
    "trading_strategy_one_broker_match_snapshot_v5",
    (("snapshot_id", "UUID"), ("run_id", "String"),
     ("snapshot_month", "Date"), ("session_date", "Date"),
     ("checkpoint_sequence", "UInt64"), ("boundary_ms", "UInt32"),
     ("initial_time", "DateTime64(6, 'UTC')"),
     ("next_order_id", "UInt64"), ("next_execution_id", "UInt64"),
     ("performance_complete", "UInt8"),
     ("performance_as_of", "Nullable(DateTime64(6, 'UTC'))"),
     # Checkpoint internals must be bit-exact, not financially rounded.
     ("unrealized_f64_bits", "UInt64"), ("market_value_f64_bits", "UInt64"),
     ("peak_unrealized_f64_bits", "UInt64"),
     ("worst_unrealized_f64_bits", "UInt64"),
     ("equity_peak_f64_bits", "UInt64"),
     ("maximum_drawdown_f64_bits", "UInt64"),
     ("account_count", "UInt32"), ("account_hash", "FixedString(64)"),
     ("position_count", "UInt32"), ("position_hash", "FixedString(64)"),
     ("open_order_count", "UInt32"), ("open_order_hash", "FixedString(64)"),
     ("ticker_count", "UInt32"), ("ticker_hash", "FixedString(64)"),
     ("mark_count", "UInt32"), ("mark_hash", "FixedString(64)"),
     ("content_hash", "FixedString(64)")),
    "toYYYYMM(snapshot_month)", "run_id, checkpoint_sequence, snapshot_id",
)
ACCOUNT = TableContract(
    "trading_strategy_one_broker_match_account_v5",
    (("snapshot_id", "UUID"), ("run_id", "String"),
     ("snapshot_month", "Date"), ("checkpoint_sequence", "UInt64"),
     ("account_id", "String"), ("cash_f64_bits", "UInt64"),
     ("realized_pnl_f64_bits", "UInt64"), ("content_hash", "FixedString(64)")),
    "toYYYYMM(snapshot_month)",
    "run_id, checkpoint_sequence, account_id",
)
POSITION = TableContract(
    "trading_strategy_one_broker_match_position_v5",
    (("snapshot_id", "UUID"), ("run_id", "String"),
     ("snapshot_month", "Date"), ("checkpoint_sequence", "UInt64"),
     ("account_id", "String"), ("conid", "UInt64"),
     ("ticker", "LowCardinality(String)"),
     ("quantity_f64_bits", "UInt64"), ("avg_cost_f64_bits", "UInt64"),
     ("realized_pnl_f64_bits", "UInt64"), ("content_hash", "FixedString(64)")),
    "toYYYYMM(snapshot_month)",
    "run_id, checkpoint_sequence, account_id, conid",
)
OPEN_ORDER = TableContract(
    "trading_strategy_one_broker_match_open_order_v5",
    (("snapshot_id", "UUID"), ("run_id", "String"),
     ("snapshot_month", "Date"), ("checkpoint_sequence", "UInt64"),
     ("broker_order_id", "String"), ("account_id", "String"),
     ("client_order_id", "String"), ("conid", "UInt64"),
     ("ticker", "LowCardinality(String)"), ("status", "String"),
     ("submitted_at", "DateTime64(6, 'UTC')"),
     ("oca_group", "String"), ("filled_f64_bits", "UInt64"),
     ("effective_quantity_f64_bits", "Nullable(UInt64)"),
     ("effective_cash_quantity_f64_bits", "Nullable(UInt64)"),
     ("effective_request_hash", "FixedString(64)"),
     ("avg_price_f64_bits", "UInt64"), ("commission_paid_f64_bits", "UInt64"),
     ("stop_triggered", "UInt8"), ("trailing_reference_f64_bits", "UInt64"),
     ("status_description", "String"),
     ("content_hash", "FixedString(64)")),
    "toYYYYMM(snapshot_month)",
    "run_id, checkpoint_sequence, broker_order_id",
)
TICKER = TableContract(
    "trading_strategy_one_broker_match_ticker_v5",
    (("snapshot_id", "UUID"), ("run_id", "String"),
     ("snapshot_month", "Date"), ("checkpoint_sequence", "UInt64"),
     ("ticker", "LowCardinality(String)"), ("last_boundary_ms", "UInt32"),
     ("has_mark", "UInt8"), ("mark_f64_bits", "UInt64"),
     ("has_quote", "UInt8"), ("quote_timestamp_us", "UInt64"),
     ("content_hash", "FixedString(64)")),
    "toYYYYMM(snapshot_month)",
    "run_id, checkpoint_sequence, ticker",
)
MARK = TableContract(
    "trading_strategy_one_broker_match_performance_mark_v5",
    (("snapshot_id", "UUID"), ("run_id", "String"),
     ("snapshot_month", "Date"), ("checkpoint_sequence", "UInt64"),
     ("conid", "UInt64"), ("mark_f64_bits", "UInt64"),
     ("has_performance_path", "UInt8"),
     ("unrealized_f64_bits", "UInt64"), ("market_value_f64_bits", "UInt64"),
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


@dataclass(frozen=True, slots=True)
class BrokerMatchHead:
    run_id: str
    checkpoint_sequence: int
    journal_batch_id: str
    snapshot_hash: str
    keeper_version: int


class BrokerMatchHeadReader(Protocol):
    def read_head(self, *, run_id: str) -> BrokerMatchHead: ...


class ManagedBrokerMatchHeadReader:
    """Read only the Keeper-selected root, never adopt orphan rows."""

    def __init__(self, session: ManagedKeeperSession) -> None:
        if not isinstance(session, ManagedKeeperSession):
            raise TypeError("Broker match head needs managed Keeper")
        self._session = session

    @staticmethod
    def path(run_id: str) -> str:
        if (type(run_id) is not str or not run_id
                or any(char in run_id for char in "\r\n\x00")):
            raise ValueError("Broker match head run is invalid")
        return ("/trading/strategy-one-broker-match/v5/"
                + sha256(run_id.encode()).hexdigest() + "/head")

    def read_head(self, *, run_id: str) -> BrokerMatchHead:
        session, client = self._session, self._session.client
        if not session.writable or client.client_id is None:
            raise RuntimeError("Broker match Keeper session is unavailable")
        generation, client_id = session._generation, client.client_id
        try:
            raw, stat = client.get(self.path(run_id))
            fields = raw.decode("utf-8").split("\n")
            if (len(fields) != 5 or fields[:2] != ["1", run_id]
                    or str(int(fields[2])) != fields[2] or int(fields[2]) < 1
                    or str(UUID(fields[3])) != fields[3]
                    or len(fields[4]) != 64
                    or any(char not in "0123456789abcdef" for char in fields[4])
                    or type(stat.version) is not int or stat.version < 0):
                raise ValueError
            head = BrokerMatchHead(
                run_id, int(fields[2]), fields[3], fields[4], stat.version)
        except Exception as exc:
            raise ValueError("Broker match Keeper head missing or corrupt") from exc
        if (not session.writable or session._generation != generation
                or client.client_id != client_id):
            raise RuntimeError("Broker match Keeper session changed during read")
        return head


def _float(value: Any, label: str) -> float:
    if type(value) not in (int, float) or not isfinite(value):
        raise ValueError(f"Broker match snapshot has invalid {label}")
    # ClickHouse's Float64 JSON renderer need not preserve a negative zero
    # spelling; canonicalize it before sealing on both write and cold read.
    return 0.0 if value == 0 else float(value)


def float64_bits(value: Any, label: str) -> int:
    """Encode one finite simulator float without a text-parser round trip."""
    return int.from_bytes(pack(">d", _float(value, label)), "big")


def float64_from_bits(bits: Any, label: str) -> float:
    if type(bits) is not int or not 0 <= bits < 1 << 64:
        raise ValueError(f"Broker match {label} bits are invalid")
    value = unpack(">d", bits.to_bytes(8, "big"))[0]
    if not isfinite(value) or bits == (1 << 63):
        raise ValueError(f"Broker match {label} float is invalid")
    return value


def _time(value: Any, label: str) -> datetime:
    parsed = value if isinstance(value, datetime) else datetime.fromisoformat(str(value))
    if parsed.tzinfo is None:
        raise ValueError(f"Broker match snapshot has naive {label}")
    return parsed


def _utc_text(value: Any, label: str, *, from_clickhouse: bool = False) -> str:
    """Use one UTC microsecond representation on both sides of ClickHouse."""
    parsed = value if isinstance(value, datetime) else datetime.fromisoformat(
        str(value).replace(" ", "T"))
    if parsed.tzinfo is None:
        if not from_clickhouse:
            raise ValueError(f"Broker match snapshot has naive {label}")
        # DateTime64(6, 'UTC') is a typed UTC column. JSONEachRow renders it
        # without a timezone suffix; this branch is readback-only.
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc).isoformat(timespec="microseconds")


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
    if state.get("trades") or state.get("trades_by_ticker"):
        raise ValueError("Broker match snapshot cannot omit event-mode trade state")
    origin = market_day_boundary(session_date, 0)
    global_at = origin.timestamp() + boundary_ms / 1000
    initial = _time(state.get("initial_time"), "initial time")
    if initial.timestamp() > global_at:
        raise ValueError("Broker initial time is after checkpoint")
    snapshot_id = str(uuid5(NAMESPACE_URL,
                            f"strategy-one-broker-match-v5:{run_id}:{checkpoint_sequence}"))
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
                             cash_f64_bits=float64_bits(cash[account], "cash"),
                             realized_pnl_f64_bits=float64_bits(
                                 realized[account], "realized P&L"))
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
                 quantity_f64_bits=float64_bits(
                     position["quantity"], "position quantity"),
                 avg_cost_f64_bits=float64_bits(
                     position["avg_cost"], "average cost"),
                 realized_pnl_f64_bits=float64_bits(
                     position["realized_pnl"], "position P&L")))
    positions.sort(key=lambda row: (row["account_id"], row["conid"]))
    open_orders = []
    seen_orders = set()
    for order in state.get("orders") or ():
        status = OrderStatus(str(order["status"]))
        if status not in OPEN_ORDER_STATUSES:
            continue
        request = dict(order["request"])
        # Normalize negative zero before sealing the effective request hash.
        for key in ("quantity", "cashQty"):
            if request.get(key) is not None:
                request[key] = _float(request[key], key)
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
            status=status.value,
            submitted_at=_utc_text(submitted, "order submission"),
            oca_group=str(order.get("oca_group") or ""),
             filled_f64_bits=float64_bits(order["filled"], "filled quantity"),
             effective_quantity_f64_bits=(float64_bits(
                 request["quantity"], "effective quantity")
                 if request.get("quantity") is not None else None),
             effective_cash_quantity_f64_bits=(float64_bits(
                 request["cashQty"], "effective cash quantity")
                 if request.get("cashQty") is not None else None),
             effective_request_hash=_digest(request),
             avg_price_f64_bits=float64_bits(order["avg_price"], "fill average"),
             commission_paid_f64_bits=float64_bits(
                 order["commission_paid"], "commission"),
             stop_triggered=int(bool(order["stop_triggered"])),
             trailing_reference_f64_bits=float64_bits(
                 order["trailing_reference"], "trailing reference"),
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
             mark_f64_bits=float64_bits(bar_marks.get(ticker, 0), "bar mark"),
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
                             mark_f64_bits=float64_bits(value, "mark"),
                             has_performance_path=int(conid in performance_marks),
                             unrealized_f64_bits=float64_bits(
                                 path[0], "unrealized mark"),
                             market_value_f64_bits=float64_bits(
                                 path[1], "market value mark")))
    performance = dict(state.get("performance_extrema") or {})
    as_of = performance.get("as_of") or None
    if as_of is not None and _time(as_of, "performance time").timestamp() > global_at:
        raise ValueError("Broker performance path is from the future")
    root = dict(**common, session_date=session_date.isoformat(),
                boundary_ms=boundary_ms,
                initial_time=_utc_text(initial, "initial time"),
                next_order_id=int(state["next_order_id"]),
                next_execution_id=int(state["next_execution_id"]),
                performance_complete=int(bool(performance["complete"])),
                performance_as_of=(_utc_text(as_of, "performance time")
                                   if as_of is not None else None))
    if root["next_order_id"] < 1 or root["next_execution_id"] < 1:
        raise ValueError("Broker counters are invalid")
    for name in ("unrealized", "market_value", "peak_unrealized",
                 "worst_unrealized", "equity_peak", "maximum_drawdown"):
        root[f"{name}_f64_bits"] = float64_bits(performance[name], name)
    families = (tuple(accounts), tuple(positions), tuple(open_orders),
                tuple(tickers), tuple(marks))
    for name, rows in zip(("account", "position", "open_order", "ticker", "mark"),
                          families, strict=True):
        root[f"{name}_count"], root[f"{name}_hash"] = _family(rows)
    return BrokerMatchSnapshotRows(
        {**root, "content_hash": _digest(root)}, *families)


def _canonical_row(contract: TableContract, value: Mapping[str, Any]) -> dict[str, Any]:
    kinds = dict(contract.columns)
    if not isinstance(value, Mapping) or set(value) != set(kinds):
        raise ValueError("Broker match row has missing or extra columns")
    row: dict[str, Any] = {}
    for name, kind in contract.columns:
        item = value[name]
        if item is None:
            if not kind.startswith("Nullable("):
                raise ValueError("Broker match row has unexpected null")
            row[name] = None
        elif "DateTime64(" in kind:
            row[name] = _utc_text(item, name, from_clickhouse=True)
        elif kind.startswith("UInt") or kind == "Nullable(UInt64)":
            if type(item) is not int or item < 0 or name.endswith("_f64_bits") \
                    and item >= 1 << 64:
                raise ValueError("Broker match integer is invalid")
            if name.endswith("_f64_bits"):
                float64_from_bits(item, name)
            row[name] = item
        elif kind == "UUID":
            row[name] = str(UUID(str(item)))
        elif kind == "Date":
            row[name] = date.fromisoformat(str(item)).isoformat()
        else:
            if type(item) is not str:
                raise ValueError("Broker match string is invalid")
            row[name] = item
    return row


def verify_broker_match_snapshot(rows: BrokerMatchSnapshotRows) -> BrokerMatchSnapshotRows:
    """Reject missing, duplicated, foreign, or altered child rows.

    This verifies the ClickHouse row contract, not Keeper or the V4 journal
    cursor. Read order is canonicalized; both separate authorities are
    mandatory before executable restore.
    """
    if not isinstance(rows, BrokerMatchSnapshotRows):
        raise ValueError("Broker match recovery needs typed rows")
    root = _canonical_row(ROOT, rows.snapshot)
    families = []
    keys = (
        ("account_id",), ("account_id", "conid"),
        ("broker_order_id",), ("ticker",), ("conid",),
    )
    for contract, source, identity in zip(
            TABLES[1:], (rows.accounts, rows.positions, rows.open_orders,
                         rows.tickers, rows.marks), keys, strict=True):
        normalized = tuple(_canonical_row(contract, row) for row in source)
        normalized = tuple(sorted(normalized, key=lambda row: tuple(
            row[name] for name in identity)))
        identities = [tuple(row[name] for name in identity) for row in normalized]
        if len(set(identities)) != len(identities):
            raise ValueError("Broker match child identity is duplicated")
        for row in normalized:
            if any(row[name] != root[name] for name in
                   ("snapshot_id", "run_id", "snapshot_month",
                    "checkpoint_sequence")):
                raise ValueError(f"Broker match {contract.name} child identity differs")
            if row["content_hash"] != _digest({
                    name: item for name, item in row.items()
                    if name != "content_hash"}):
                raise ValueError(f"Broker match {contract.name} child hash differs")
        families.append(normalized)
    if (not root["run_id"] or root["checkpoint_sequence"] < 1
            or not 0 < root["boundary_ms"] <= 57_600_000
            or root["boundary_ms"] % 100
            or root["snapshot_id"] != str(uuid5(
                NAMESPACE_URL, f"strategy-one-broker-match-v5:"
                f"{root['run_id']}:{root['checkpoint_sequence']}"))
            or root["snapshot_month"] != root["session_date"][:7] + "-01"
            or root["next_order_id"] < 1 or root["next_execution_id"] < 1
            or root["performance_complete"] not in (0, 1)):
        raise ValueError("Broker match snapshot root identity is invalid")
    cutoff = market_day_boundary(date.fromisoformat(root["session_date"]),
                                 root["boundary_ms"])
    if (_time(root["initial_time"], "initial time") > cutoff
            or root["performance_as_of"] is not None
            and _time(root["performance_as_of"], "performance time") > cutoff):
        raise ValueError("Broker match snapshot time is after cutoff")
    for name, group in zip(("account", "position", "open_order", "ticker", "mark"),
                           families, strict=True):
        count, digest = _family(group)
        if root[f"{name}_count"] != count or root[f"{name}_hash"] != digest:
            raise ValueError("Broker match child family seal differs")
    accounts, positions, orders, tickers, marks = families
    account_ids = {row["account_id"] for row in accounts}
    if (not account_ids or any(row["account_id"] not in account_ids
                               for row in (*positions, *orders))):
        raise ValueError("Broker match child account differs")
    for row in orders:
        if (OrderStatus(row["status"]) not in OPEN_ORDER_STATUSES
                or not row["client_order_id"] or not row["ticker"]
                or row["conid"] < 1
                or float64_from_bits(row["filled_f64_bits"], "filled") < 0
                or float64_from_bits(
                    row["commission_paid_f64_bits"], "commission") < 0
                or _time(row["submitted_at"], "order time") > cutoff):
            raise ValueError("Broker match open order is invalid")
        effective_qty = (float64_from_bits(row["effective_quantity_f64_bits"],
                           "effective quantity")
                         if row["effective_quantity_f64_bits"] is not None else None)
        effective_cash = (float64_from_bits(row["effective_cash_quantity_f64_bits"],
                            "effective cash quantity")
                          if row["effective_cash_quantity_f64_bits"] is not None else None)
        request_hash = row["effective_request_hash"]
        if ((effective_qty is None) == (effective_cash is None)
                or effective_qty is not None and effective_qty <= 0
                or effective_cash is not None and effective_cash <= 0
                or effective_qty is not None and effective_qty <
                   float64_from_bits(row["filled_f64_bits"], "filled")
                or len(request_hash) != 64
                or any(char not in "0123456789abcdef" for char in request_hash)):
            raise ValueError("Broker match effective request is invalid")
    for row in tickers:
        if (not row["ticker"] or not 0 < row["last_boundary_ms"] <= root["boundary_ms"]
                or row["last_boundary_ms"] % 100
                or row["has_mark"] not in (0, 1)
                or row["has_quote"] not in (0, 1)
                or not row["has_mark"] and row["mark_f64_bits"] != 0
                or bool(row["quote_timestamp_us"]) != bool(row["has_quote"])):
            raise ValueError("Broker match ticker boundary is invalid")
        at = market_day_boundary(date.fromisoformat(root["session_date"]),
                                 row["last_boundary_ms"])
        if row["quote_timestamp_us"] > round(at.timestamp() * 1_000_000):
            raise ValueError("Broker match quote is from the future")
    if (any(row["conid"] < 1 for row in (*positions, *marks))
            or any(row["has_performance_path"] not in (0, 1)
                   or not row["has_performance_path"]
                   and (row["unrealized_f64_bits"] != 0
                        or row["market_value_f64_bits"] != 0)
                   for row in marks)):
        raise ValueError("Broker match conid is invalid")
    if root["content_hash"] != _digest({
            name: value for name, value in root.items() if name != "content_hash"}):
        raise ValueError("Broker match root hash differs")
    return BrokerMatchSnapshotRows(root, *families)


def load_unattested_broker_match_snapshot(
    client: Any, *, run_id: str, checkpoint_sequence: int,
) -> BrokerMatchSnapshotRows:
    """SELECT exact rows; caller must still verify the V4/Keeper cursor.

    The root is coverage-last. LIMIT count+1 bounds a corrupt child family
    before Python decodes an unbounded failed-attempt accumulation.
    """
    from src.backend.backtest_market_data import _literal, assert_select_only

    if (type(run_id) is not str or not run_id or "\x00" in run_id
            or type(checkpoint_sequence) is not int or checkpoint_sequence < 1
            or not callable(getattr(client, "execute", None))):
        raise ValueError("Broker match cold read needs an exact run and cursor")

    def read(table: TableContract, *, limit: int,
             snapshot_id: str | None = None) -> tuple[dict[str, Any], ...]:
        where = (f"run_id={_literal(run_id)} "
                 f"AND checkpoint_sequence={checkpoint_sequence}")
        if snapshot_id is not None:
            where += f" AND snapshot_id=toUUID({_literal(snapshot_id)})"
        sql = assert_select_only(
            f"SELECT * FROM arte.{table.name} WHERE {where} "
            f"LIMIT {limit} FORMAT JSONEachRow")
        return tuple(json.loads(line) for line in client.execute(sql).splitlines()
                     if line.strip())

    roots = read(ROOT, limit=2)
    if len(roots) != 1:
        raise ValueError("Broker match root is missing or duplicate")
    root = _canonical_row(ROOT, roots[0])
    if (root["run_id"] != run_id
            or root["checkpoint_sequence"] != checkpoint_sequence):
        raise ValueError("Broker match root differs from requested cursor")
    children = []
    for table, name in zip(TABLES[1:],
                           ("account", "position", "open_order", "ticker", "mark"),
                           strict=True):
        expected = root[f"{name}_count"]
        if expected > 1_000_000:
            raise ValueError("Broker match family exceeds bounded cold read")
        rows = read(table, limit=expected + 1,
                    snapshot_id=root["snapshot_id"])
        if len(rows) != expected:
            raise ValueError("Broker match family is missing or duplicate")
        children.append(rows)
    return verify_broker_match_snapshot(
        BrokerMatchSnapshotRows(root, *children))


def load_attested_broker_match_snapshot(
    client: Any, keeper: BrokerMatchHeadReader, *,
    run_id: str, checkpoint_sequence: int,
    first_price_source=None,
) -> BrokerMatchSnapshotRows:
    """Require exact Keeper, V4, and market-cursor agreement on cold read.

    A verified broker root alone is not a complete executable checkpoint:
    manager, OMS, portfolio, and controller recovery must independently join
    this same cursor before the resume gate may open.
    """
    from src.trading_runtime.arte_journal_commit_v4 import load_verified_v4_prefix
    from src.trading_runtime.arte_journal_projection import load_latest_backtest_cursor

    if (type(run_id) is not str or not run_id
            or type(checkpoint_sequence) is not int or checkpoint_sequence < 1
            or not callable(getattr(client, "execute", None))
            or not callable(getattr(keeper, "read_head", None))):
        raise ValueError("Broker match cold read lacks exact authorities")
    prefix = load_verified_v4_prefix(client, run_id, **({} if first_price_source is None else {'first_price_source': first_price_source}))
    if (prefix is None or prefix.status != "running"
            or prefix.last_sequence != checkpoint_sequence
            or not prefix.batch_ids
            or prefix.last_batch_id != prefix.batch_ids[-1]):
        raise RuntimeError("Broker match lacks a running verified V4 cursor")
    first = keeper.read_head(run_id=run_id)
    if (not isinstance(first, BrokerMatchHead)
            or first.run_id != run_id
            or first.checkpoint_sequence != checkpoint_sequence
            or first.journal_batch_id != prefix.last_batch_id
            or type(first.keeper_version) is not int or first.keeper_version < 0
            or len(first.snapshot_hash) != 64
            or any(char not in "0123456789abcdef"
                   for char in first.snapshot_hash)):
        raise RuntimeError("Broker match Keeper head differs from V4 cursor")
    cursor = load_latest_backtest_cursor(client, prefix)
    if (not isinstance(cursor, dict) or cursor.get("run_id") != run_id
            or cursor.get("event_sequence") != checkpoint_sequence
            or cursor.get("batch_id") != prefix.last_batch_id):
        raise RuntimeError("Broker match lacks a committed market cursor")
    rows = load_unattested_broker_match_snapshot(
        client, run_id=run_id, checkpoint_sequence=checkpoint_sequence)
    if (rows.snapshot["content_hash"] != first.snapshot_hash
            or rows.snapshot["boundary_ms"] != cursor.get("boundary_ms")
            or rows.snapshot["session_date"] != cursor.get("session_date")):
        raise RuntimeError("Broker match seal differs from selected cursor")
    if keeper.read_head(run_id=run_id) != first:
        raise RuntimeError("Broker match Keeper head changed during cold read")
    return rows


def publish_broker_match_snapshot(
    client: Any, session: ManagedKeeperSession,
    rows: BrokerMatchSnapshotRows, *, journal_batch_id: str,
    first_price_source=None,
) -> BrokerMatchHead:
    """Journal-worker-only children-first publication, then Keeper CAS.

    An uncertain INSERT never selects a head. The execution thread may only
    submit this work through its bounded journal queue and receives a future.
    """
    from src.trading_runtime.arte_journal_commit_v4 import load_writer_v4_snapshot_prefix
    from src.trading_runtime.arte_journal_projection import load_latest_backtest_cursor
    from src.trading_runtime.arte_journal_writer import _insert
    from src.trading_runtime.arte_typed_insert_dispatch import TypedInsertDispatch

    if (not isinstance(session, ManagedKeeperSession) or not session.writable
            or getattr(client, "typed_insert_strict", False) is not True
            or not isinstance(getattr(client, "typed_insert_dispatch", None),
                              TypedInsertDispatch)
            or client.typed_insert_dispatch.keeper is not session.client):
        raise RuntimeError("Broker match publication lacks fenced writer")
    expected = verify_broker_match_snapshot(rows)
    seal = expected.snapshot
    run_id, sequence = seal["run_id"], seal["checkpoint_sequence"]
    try:
        if str(UUID(journal_batch_id)) != journal_batch_id:
            raise ValueError
    except (TypeError, ValueError) as exc:
        raise ValueError("Broker match batch ID is invalid") from exc
    prefix = load_writer_v4_snapshot_prefix(client, run_id, **({} if first_price_source is None else {'first_price_source': first_price_source}))
    if (prefix is None or prefix.status != "running"
            or prefix.last_sequence != sequence
            or prefix.last_batch_id != journal_batch_id):
        raise RuntimeError("Broker match lacks exact running V4 cursor")
    cursor = load_latest_backtest_cursor(client, prefix)
    if (not isinstance(cursor, dict) or cursor.get("run_id") != run_id
            or cursor.get("event_sequence") != sequence
            or cursor.get("batch_id") != journal_batch_id
            or cursor.get("boundary_ms") != seal["boundary_ms"]
            or cursor.get("session_date") != seal["session_date"]):
        raise RuntimeError("Broker match cursor differs from captured state")
    reader = ManagedBrokerMatchHeadReader(session)
    path = reader.path(run_id)
    if session.client.exists(path) is None:
        previous = None
    else:
        previous = reader.read_head(run_id=run_id)
        if previous.checkpoint_sequence == sequence:
            if (previous.journal_batch_id != journal_batch_id
                    or previous.snapshot_hash != seal["content_hash"]
                    or load_attested_broker_match_snapshot(
                        client, reader, run_id=run_id,
                        checkpoint_sequence=sequence,
                        **({} if first_price_source is None else {'first_price_source': first_price_source})) != expected):
                raise RuntimeError("Broker match repeat differs from selected state")
            return previous
        if previous.checkpoint_sequence > sequence:
            raise RuntimeError("Broker match would rewind Keeper head")

    families = ((ACCOUNT, expected.accounts), (POSITION, expected.positions),
                (OPEN_ORDER, expected.open_orders), (TICKER, expected.tickers),
                (MARK, expected.marks), (ROOT, (seal,)))
    operations: list[tuple[str, str]] = []
    for contract, family in families:
        if not family:
            continue
        token = (f"broker-match:{run_id}:{sequence}:"
                 f"{seal['content_hash']}:{contract.name}")
        _insert(client, contract.name, family, token,
                dispatch_sequence=sequence,
                dispatch_batch_id=journal_batch_id,
                dispatch_broker_snapshot_hash=seal["content_hash"])
        operations.append((contract.name, token))
    observed = load_unattested_broker_match_snapshot(
        client, run_id=run_id, checkpoint_sequence=sequence)
    if observed != expected:
        raise RuntimeError("Broker match readback differs from captured state")
    for table, token in operations:
        client.typed_insert_dispatch.seal_verified_operation(
            run_id=run_id, table=table, token=token,
            batch_id=journal_batch_id, batch_last_sequence=sequence,
            broker_snapshot=True)
    client.typed_insert_dispatch.compact_verified_broker_match_snapshot(
        run_id=run_id, batch_id=journal_batch_id,
        last_sequence=sequence, snapshot_hash=seal["content_hash"],
        operations=tuple(operations), previous=previous)
    selected = reader.read_head(run_id=run_id)
    if (selected.checkpoint_sequence != sequence
            or selected.journal_batch_id != journal_batch_id
            or selected.snapshot_hash != seal["content_hash"]):
        raise RuntimeError("Broker match Keeper readback differs")
    return selected
