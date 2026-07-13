from __future__ import annotations

import json
import ssl
import urllib.parse
import urllib.request
from dataclasses import dataclass
from typing import Any

from src.trading_runtime.journal import JournalRecord, TradingJournal


TRADING_TABLE_DDL = (
    """CREATE TABLE IF NOT EXISTS q_live.tr_strategy_v1 (
        strategy_id String, revision UInt32, name String, implementation String, automatic UInt8,
        enabled UInt8, config_json String, created_at DateTime64(6, 'UTC')
    ) ENGINE = ReplacingMergeTree(created_at) ORDER BY (strategy_id, revision)""",
    """CREATE TABLE IF NOT EXISTS q_live.tr_run_v1 (
        run_id UUID, mode LowCardinality(String), status LowCardinality(String), strategy_id String,
        strategy_revision UInt32, anchor_date Date, config_json String, started_at DateTime64(6, 'UTC'),
        ended_at Nullable(DateTime64(6, 'UTC'))
    ) ENGINE = ReplacingMergeTree(started_at) ORDER BY run_id""",
    """CREATE TABLE IF NOT EXISTS q_live.tr_run_account_v1 (
        run_id UUID, account_id String, allocation_json String, recorded_at DateTime64(6, 'UTC')
    ) ENGINE = ReplacingMergeTree(recorded_at) ORDER BY (run_id, account_id)""",
    """CREATE TABLE IF NOT EXISTS q_live.tr_journal_v1 (
        record_id UUID, run_id UUID, sequence UInt64, event_time DateTime64(9, 'UTC'),
        recorded_at DateTime64(6, 'UTC'), category LowCardinality(String), entity_type LowCardinality(String),
        entity_id String, account_id String, payload_json String
    ) ENGINE = MergeTree PARTITION BY toYYYYMM(event_time) ORDER BY (run_id, sequence, record_id)""",
    """CREATE TABLE IF NOT EXISTS q_live.tr_signal_v1 (
        signal_id UUID, run_id UUID, strategy_id String, account_id String, ticker LowCardinality(String),
        signal_type LowCardinality(String), event_time DateTime64(9, 'UTC'), payload_json String
    ) ENGINE = MergeTree PARTITION BY toYYYYMM(event_time) ORDER BY (run_id, event_time, signal_id)""",
    """CREATE TABLE IF NOT EXISTS q_live.tr_order_event_v1 (
        record_id UUID, run_id UUID, account_id String, order_id String, client_order_id String,
        status LowCardinality(String), event_time DateTime64(9, 'UTC'), payload_json String
    ) ENGINE = MergeTree PARTITION BY toYYYYMM(event_time) ORDER BY (run_id, account_id, order_id, event_time, record_id)""",
    """CREATE TABLE IF NOT EXISTS q_live.tr_fill_v1 (
        execution_id String, run_id UUID, account_id String, order_id String, conid UInt64,
        ticker LowCardinality(String), side LowCardinality(String), quantity Float64, price Float64,
        commission Float64, event_time DateTime64(9, 'UTC'), payload_json String
    ) ENGINE = ReplacingMergeTree(event_time) PARTITION BY toYYYYMM(event_time)
      ORDER BY (run_id, account_id, execution_id)""",
    """CREATE TABLE IF NOT EXISTS q_live.tr_trade_v1 (
        trade_id UUID, run_id UUID, account_id String, strategy_id String, conid UInt64,
        ticker LowCardinality(String), opened_at DateTime64(9, 'UTC'), closed_at Nullable(DateTime64(9, 'UTC')),
        quantity Float64, realized_pnl Float64, payload_json String
    ) ENGINE = ReplacingMergeTree(opened_at) PARTITION BY toYYYYMM(opened_at) ORDER BY (run_id, account_id, trade_id)""",
    """CREATE TABLE IF NOT EXISTS q_live.tr_portfolio_v1 (
        run_id UUID, account_id String, event_time DateTime64(9, 'UTC'), net_liquidation Float64,
        cash Float64, buying_power Float64, gross_position_value Float64, payload_json String
    ) ENGINE = MergeTree PARTITION BY toYYYYMM(event_time) ORDER BY (run_id, account_id, event_time)""",
    """CREATE TABLE IF NOT EXISTS q_live.tr_position_v1 (
        run_id UUID, account_id String, conid UInt64, ticker LowCardinality(String), event_time DateTime64(9, 'UTC'),
        quantity Float64, average_cost Float64, market_price Float64, unrealized_pnl Float64,
        realized_pnl Float64, payload_json String
    ) ENGINE = MergeTree PARTITION BY toYYYYMM(event_time) ORDER BY (run_id, account_id, conid, event_time)""",
    """CREATE TABLE IF NOT EXISTS q_live.tr_checkpoint_v1 (
        run_id UUID, sequence UInt64, cursor String, event_time DateTime64(9, 'UTC'), state_json String,
        recorded_at DateTime64(6, 'UTC')
    ) ENGINE = ReplacingMergeTree(recorded_at) ORDER BY (run_id, sequence)""",
    """CREATE TABLE IF NOT EXISTS q_live.tr_reconcile_v1 (
        reconcile_id UUID, run_id UUID, account_id String, status LowCardinality(String),
        event_time DateTime64(9, 'UTC'), differences_json String
    ) ENGINE = MergeTree PARTITION BY toYYYYMM(event_time) ORDER BY (run_id, account_id, event_time, reconcile_id)""",
)


@dataclass(frozen=True, slots=True)
class ClickHouseTradingSink:
    endpoint_url: str
    user: str = "default"
    password: str = ""
    timeout: float = 10.0

    def initialize(self) -> None:
        self._execute("CREATE DATABASE IF NOT EXISTS q_live")
        for statement in TRADING_TABLE_DDL:
            self._execute(statement)

    def flush(self, journal: TradingJournal, limit: int = 500) -> int:
        records = journal.pending_outbox(limit)
        if not records:
            return 0
        body = "\n".join(json.dumps(_journal_row(record), separators=(",", ":"), default=str) for record in records)
        try:
            self._execute("INSERT INTO q_live.tr_journal_v1 FORMAT JSONEachRow", body)
            for table, rows in _specialized_rows(records).items():
                if rows:
                    specialized_body = "\n".join(json.dumps(row, separators=(",", ":"), default=str) for row in rows)
                    self._execute(f"INSERT INTO q_live.{table} FORMAT JSONEachRow", specialized_body)
        except Exception as exc:
            journal.mark_failed((record.record_id for record in records), str(exc))
            raise
        journal.mark_delivered(record.record_id for record in records)
        return len(records)

    def persist_strategy(self, strategy: dict[str, Any]) -> None:
        row = {
            "strategy_id": str(strategy["strategy_id"]),
            "revision": int(strategy["revision"]),
            "name": str(strategy["name"]),
            "implementation": str(strategy["implementation"]),
            "automatic": int(bool(strategy["automatic"])),
            "enabled": int(bool(strategy["enabled"])),
            "config_json": json.dumps(strategy.get("config") or {}, separators=(",", ":"), sort_keys=True),
            "created_at": str(strategy["created_at"]),
        }
        self._execute("INSERT INTO q_live.tr_strategy_v1 FORMAT JSONEachRow", json.dumps(row, separators=(",", ":")))

    def _execute(self, query: str, body: str = "") -> str:
        url = f"{self.endpoint_url.rstrip('/')}?{urllib.parse.urlencode({'query': query})}"
        request = urllib.request.Request(url, data=body.encode("utf-8"), method="POST")
        if self.user or self.password:
            request.add_header("X-ClickHouse-User", self.user)
            request.add_header("X-ClickHouse-Key", self.password)
        with urllib.request.urlopen(request, timeout=self.timeout, context=ssl.create_default_context()) as response:
            return response.read().decode("utf-8")


def _journal_row(record: JournalRecord) -> dict[str, Any]:
    return {
        "record_id": record.record_id,
        "run_id": record.run_id,
        "sequence": record.sequence,
        "event_time": record.event_time.isoformat(),
        "recorded_at": record.recorded_at.isoformat(),
        "category": record.category,
        "entity_type": record.entity_type,
        "entity_id": record.entity_id,
        "account_id": record.account_id,
        "payload_json": json.dumps(record.payload, separators=(",", ":"), sort_keys=True, default=str),
    }


def _specialized_rows(records: list[JournalRecord]) -> dict[str, list[dict[str, Any]]]:
    grouped = {"tr_order_event_v1": [], "tr_fill_v1": [], "tr_portfolio_v1": [], "tr_position_v1": [], "tr_signal_v1": []}
    for record in records:
        payload = record.payload
        payload_json = json.dumps(payload, separators=(",", ":"), sort_keys=True, default=str)
        if record.entity_type == "order":
            grouped["tr_order_event_v1"].append(
                {
                    "record_id": record.record_id, "run_id": record.run_id, "account_id": record.account_id,
                    "order_id": str(payload.get("order_id") or payload.get("orderId") or record.entity_id),
                    "client_order_id": str(payload.get("cOID") or payload.get("local_order_id") or ""),
                    "status": str(payload.get("order_status") or payload.get("status") or record.category),
                    "event_time": record.event_time.isoformat(), "payload_json": payload_json,
                }
            )
        elif record.entity_type == "fill":
            grouped["tr_fill_v1"].append(
                {
                    "execution_id": str(payload.get("execution_id") or record.entity_id), "run_id": record.run_id,
                    "account_id": record.account_id, "order_id": str(payload.get("order_id") or ""),
                    "conid": int(payload.get("conid") or 0), "ticker": str(payload.get("symbol") or payload.get("ticker") or ""),
                    "side": str(payload.get("side") or ""), "quantity": float(payload.get("size") or payload.get("quantity") or 0),
                    "price": float(payload.get("price") or 0), "commission": float(payload.get("commission") or 0),
                    "event_time": record.event_time.isoformat(), "payload_json": payload_json,
                }
            )
        elif record.entity_type == "portfolio":
            grouped["tr_portfolio_v1"].append(
                {
                    "run_id": record.run_id, "account_id": record.account_id, "event_time": record.event_time.isoformat(),
                    "net_liquidation": _summary_amount(payload, "netliquidation"), "cash": _summary_amount(payload, "totalcashvalue"),
                    "buying_power": _summary_amount(payload, "buyingpower"), "gross_position_value": _summary_amount(payload, "grosspositionvalue"),
                    "payload_json": payload_json,
                }
            )
        elif record.entity_type == "position":
            grouped["tr_position_v1"].append(
                {
                    "run_id": record.run_id, "account_id": record.account_id, "conid": int(payload.get("conid") or 0),
                    "ticker": str(payload.get("contractDesc") or payload.get("ticker") or ""), "event_time": record.event_time.isoformat(),
                    "quantity": float(payload.get("position") or 0), "average_cost": float(payload.get("avgCost") or 0),
                    "market_price": float(payload.get("mktPrice") or 0), "unrealized_pnl": float(payload.get("unrealizedPnl") or 0),
                    "realized_pnl": float(payload.get("realizedPnl") or 0), "payload_json": payload_json,
                }
            )
        elif record.entity_type == "signal":
            grouped["tr_signal_v1"].append(
                {
                    "signal_id": record.record_id, "run_id": record.run_id, "strategy_id": str(payload.get("strategy_id") or ""),
                    "account_id": record.account_id, "ticker": str(payload.get("ticker") or ""),
                    "signal_type": str(payload.get("signal_type") or record.category), "event_time": record.event_time.isoformat(),
                    "payload_json": payload_json,
                }
            )
    return grouped


def _summary_amount(payload: dict[str, Any], key: str) -> float:
    value = payload.get(key, 0)
    if isinstance(value, dict):
        value = value.get("amount", value.get("monetaryValue", 0))
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0
