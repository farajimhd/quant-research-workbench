from __future__ import annotations

import json
import ssl
import urllib.parse
import urllib.request
from uuid import uuid4
from dataclasses import dataclass
from typing import Any

from src.trading_runtime.domain import BrokerEventEnvelope, OrderIntent, TradingMode, TradingStateSnapshot, json_safe
from src.trading_runtime.journal import JournalRecord, TradingJournal
from src.trading_runtime.performance import derive_trade_episodes, episodes_from_round_trips


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
    """CREATE TABLE IF NOT EXISTS q_live.tr_broker_account_v2 (
        provider LowCardinality(String), account_id String, base_currency LowCardinality(String), account_type String,
        alias String, title String, parent_account_id String, can_view UInt8, can_trade UInt8,
        valid_at DateTime64(6, 'UTC'), recorded_at DateTime64(6, 'UTC'), raw_json String
    ) ENGINE = ReplacingMergeTree(recorded_at) ORDER BY (provider, account_id, valid_at)""",
    """CREATE TABLE IF NOT EXISTS q_live.tr_broker_event_v2 (
        event_id UUID, schema_version UInt16, event_type LowCardinality(String), provider LowCardinality(String),
        mode LowCardinality(String), account_id String, run_id String, broker_session_id String, broker_event_id String,
        command_id String, correlation_id String, causation_id String, broker_order_id String, client_order_id String,
        execution_id String, source_event_time DateTime64(9, 'UTC'), received_at DateTime64(6, 'UTC'),
        recorded_at DateTime64(6, 'UTC'), source_sequence UInt64, payload_hash FixedString(64), payload_json String
    ) ENGINE = MergeTree PARTITION BY toYYYYMM(source_event_time)
      ORDER BY (provider, account_id, source_event_time, event_id)""",
    """CREATE TABLE IF NOT EXISTS q_live.tr_order_command_v2 (
        command_id UUID, run_id String, account_id String, client_order_id String, conid UInt64, symbol LowCardinality(String),
        side LowCardinality(String), order_type LowCardinality(String), time_in_force LowCardinality(String),
        quantity Nullable(Decimal(38, 10)), cash_quantity Nullable(Decimal(38, 10)),
        limit_price Nullable(Decimal(38, 10)), stop_price Nullable(Decimal(38, 10)), outside_rth UInt8,
        parent_command_id String, oca_group String, strategy_id String, strategy_revision UInt32,
        created_at DateTime64(9, 'UTC'), metadata_json String
    ) ENGINE = MergeTree PARTITION BY toYYYYMM(created_at) ORDER BY (run_id, account_id, created_at, command_id)""",
    """CREATE TABLE IF NOT EXISTS q_live.tr_order_event_v2 (
        event_id UUID, account_id String, broker_order_id String, client_order_id String, command_id String,
        conid UInt64, symbol LowCardinality(String), lifecycle_state LowCardinality(String), broker_status_raw String,
        total_quantity Decimal(38, 10), filled_quantity Decimal(38, 10), remaining_quantity Decimal(38, 10),
        average_fill_price Decimal(38, 10), can_modify UInt8, can_cancel UInt8, terminal UInt8,
        rejection_code String, rejection_reason String, source_event_time DateTime64(9, 'UTC'), received_at DateTime64(6, 'UTC'), raw_json String
    ) ENGINE = MergeTree PARTITION BY toYYYYMM(source_event_time)
      ORDER BY (account_id, broker_order_id, source_event_time, event_id)""",
    """CREATE TABLE IF NOT EXISTS q_live.tr_execution_event_v2 (
        event_id UUID, execution_id String, account_id String, broker_order_id String, client_order_id String,
        conid UInt64, symbol LowCardinality(String), side LowCardinality(String), quantity Decimal(38, 10),
        price Decimal(38, 10), exchange LowCardinality(String), commission Nullable(Decimal(38, 10)),
        commission_currency LowCardinality(String), commission_status LowCardinality(String),
        net_amount Nullable(Decimal(38, 10)), liquidity String, source_event_time DateTime64(9, 'UTC'),
        received_at DateTime64(6, 'UTC'), raw_json String
    ) ENGINE = MergeTree PARTITION BY toYYYYMM(source_event_time)
      ORDER BY (account_id, execution_id, source_event_time, event_id)""",
    """CREATE TABLE IF NOT EXISTS q_live.tr_commission_event_v2 (
        event_id UUID, execution_id String, account_id String, commission Decimal(38, 10), currency LowCardinality(String),
        realized_pnl Nullable(Decimal(38, 10)), source_event_time DateTime64(9, 'UTC'), received_at DateTime64(6, 'UTC'), raw_json String
    ) ENGINE = MergeTree PARTITION BY toYYYYMM(source_event_time)
      ORDER BY (account_id, execution_id, source_event_time, event_id)""",
    """CREATE TABLE IF NOT EXISTS q_live.tr_account_value_event_v2 (
        event_id UUID, account_id String, key LowCardinality(String), segment LowCardinality(String), value String,
        monetary_value Nullable(Decimal(38, 10)), currency LowCardinality(String), is_null UInt8, severity Int16,
        source_event_time DateTime64(9, 'UTC'), received_at DateTime64(6, 'UTC'), raw_json String
    ) ENGINE = MergeTree PARTITION BY toYYYYMM(source_event_time)
      ORDER BY (account_id, key, segment, currency, source_event_time, event_id)""",
    """CREATE TABLE IF NOT EXISTS q_live.tr_ledger_event_v2 (
        event_id UUID, account_id String, currency LowCardinality(String), is_base UInt8, values_json String,
        source_event_time DateTime64(9, 'UTC'), received_at DateTime64(6, 'UTC'), raw_json String
    ) ENGINE = MergeTree PARTITION BY toYYYYMM(source_event_time)
      ORDER BY (account_id, currency, source_event_time, event_id)""",
    """CREATE TABLE IF NOT EXISTS q_live.tr_snapshot_manifest_v2 (
        snapshot_id UUID, entity_kind LowCardinality(String), account_id String, provider LowCardinality(String),
        started_at DateTime64(6, 'UTC'), completed_at Nullable(DateTime64(6, 'UTC')), complete UInt8,
        expected_pages Nullable(UInt32), received_pages UInt32, item_count UInt32, source_watermark String, error String
    ) ENGINE = MergeTree PARTITION BY toYYYYMM(started_at) ORDER BY (account_id, entity_kind, started_at, snapshot_id)""",
    """CREATE TABLE IF NOT EXISTS q_live.tr_position_snapshot_item_v2 (
        snapshot_id UUID, account_id String, conid UInt64, model String, symbol LowCardinality(String),
        security_type LowCardinality(String), currency LowCardinality(String), quantity Decimal(38, 10),
        market_price Decimal(38, 10), market_value Decimal(38, 10), average_cost Decimal(38, 10),
        average_price Decimal(38, 10), realized_pnl Decimal(38, 10), unrealized_pnl Decimal(38, 10),
        source_event_time DateTime64(9, 'UTC'), received_at DateTime64(6, 'UTC'), raw_json String
    ) ENGINE = MergeTree PARTITION BY toYYYYMM(source_event_time)
      ORDER BY (account_id, snapshot_id, conid, model)""",
    """CREATE TABLE IF NOT EXISTS q_live.tr_reconciliation_event_v2 (
        reconcile_id UUID, account_id String, provider LowCardinality(String), status LowCardinality(String),
        source_event_time DateTime64(9, 'UTC'), differences_json String
    ) ENGINE = MergeTree PARTITION BY toYYYYMM(source_event_time)
      ORDER BY (account_id, source_event_time, reconcile_id)""",
    """CREATE TABLE IF NOT EXISTS q_live.tr_round_trip_trade_v2 (
        trade_id UUID, account_id String, conid UInt64, symbol LowCardinality(String), side LowCardinality(String),
        opened_at DateTime64(9, 'UTC'), closed_at DateTime64(9, 'UTC'), quantity Decimal(38, 10),
        entry_price Decimal(38, 10), exit_price Decimal(38, 10), gross_pnl Decimal(38, 10),
        fees Decimal(38, 10), net_pnl Decimal(38, 10), strategy_id String, exit_reason String, execution_ids Array(String)
    ) ENGINE = MergeTree PARTITION BY toYYYYMM(closed_at) ORDER BY (account_id, closed_at, trade_id)""",
    """CREATE TABLE IF NOT EXISTS q_live.tr_trade_episode_v1 (
        episode_id String, account_id String, conid UInt64, symbol LowCardinality(String), side LowCardinality(String),
        opened_at DateTime64(9, 'UTC'), closed_at DateTime64(9, 'UTC'), quantity Decimal(38, 10),
        entry_price Decimal(38, 10), exit_price Decimal(38, 10), gross_pnl Decimal(38, 10),
        fees Decimal(38, 10), net_pnl Decimal(38, 10), strategy_id String, strategy_revision UInt32,
        run_id String, setup String, exit_reason String, mae Nullable(Decimal(38, 10)),
        mfe Nullable(Decimal(38, 10)), planned_risk Nullable(Decimal(38, 10)),
        execution_ids Array(String), order_ids Array(String)
    ) ENGINE = ReplacingMergeTree(closed_at) PARTITION BY toYYYYMM(closed_at)
      ORDER BY (account_id, closed_at, episode_id)""",
    """CREATE VIEW IF NOT EXISTS q_live.tr_order_current_v2 AS
        SELECT account_id, broker_order_id,
               argMax(client_order_id, source_event_time) AS client_order_id,
               argMax(conid, source_event_time) AS conid,
               argMax(symbol, source_event_time) AS symbol,
               argMax(lifecycle_state, source_event_time) AS lifecycle_state,
               argMax(broker_status_raw, source_event_time) AS broker_status_raw,
               argMax(total_quantity, source_event_time) AS total_quantity,
               argMax(filled_quantity, source_event_time) AS filled_quantity,
               argMax(remaining_quantity, source_event_time) AS remaining_quantity,
               argMax(average_fill_price, source_event_time) AS average_fill_price,
               argMax(can_modify, source_event_time) AS can_modify,
               argMax(can_cancel, source_event_time) AS can_cancel,
               argMax(terminal, source_event_time) AS terminal,
               max(source_event_time) AS source_event_time
        FROM q_live.tr_order_event_v2 GROUP BY account_id, broker_order_id""",
    """CREATE VIEW IF NOT EXISTS q_live.tr_account_value_current_v2 AS
        SELECT account_id, key, segment, currency,
               argMax(value, source_event_time) AS value,
               argMax(monetary_value, source_event_time) AS monetary_value,
               argMax(is_null, source_event_time) AS is_null,
               max(source_event_time) AS source_event_time
        FROM q_live.tr_account_value_event_v2 GROUP BY account_id, key, segment, currency""",
    """CREATE VIEW IF NOT EXISTS q_live.tr_ledger_current_v2 AS
        SELECT account_id, currency,
               argMax(is_base, source_event_time) AS is_base,
               argMax(values_json, source_event_time) AS values_json,
               max(source_event_time) AS source_event_time
        FROM q_live.tr_ledger_event_v2 GROUP BY account_id, currency""",
    """CREATE VIEW IF NOT EXISTS q_live.tr_position_current_v2 AS
        SELECT item.* FROM q_live.tr_position_snapshot_item_v2 AS item
        INNER JOIN (
            SELECT account_id, argMax(snapshot_id, completed_at) AS snapshot_id
            FROM q_live.tr_snapshot_manifest_v2
            WHERE entity_kind = 'position' AND complete = 1 AND completed_at IS NOT NULL
            GROUP BY account_id
        ) AS latest USING (account_id, snapshot_id)""",
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

    def persist_canonical_events(self, events: list[BrokerEventEnvelope]) -> int:
        rows = []
        for event in events:
            rows.append(
                {
                    "event_id": event.event_id,
                    "schema_version": event.schema_version,
                    "event_type": event.event_type.value,
                    "provider": event.provider.value,
                    "mode": event.mode.value,
                    "account_id": event.account_id,
                    "run_id": event.run_id,
                    "broker_session_id": event.broker_session_id,
                    "broker_event_id": event.broker_event_id,
                    "command_id": event.command_id,
                    "correlation_id": event.correlation_id,
                    "causation_id": event.causation_id,
                    "broker_order_id": event.broker_order_id,
                    "client_order_id": event.client_order_id,
                    "execution_id": event.execution_id,
                    "source_event_time": event.source_event_time.isoformat(),
                    "received_at": event.received_at.isoformat(),
                    "recorded_at": event.recorded_at.isoformat(),
                    "source_sequence": event.source_sequence,
                    "payload_hash": event.payload_hash,
                    "payload_json": json.dumps(json_safe(event.payload), separators=(",", ":"), sort_keys=True),
                }
            )
        self._insert_rows("tr_broker_event_v2", rows)
        return len(rows)

    def persist_order_intents(self, intents: list[OrderIntent]) -> int:
        self._insert_rows(
            "tr_order_command_v2",
            [
                {
                    "command_id": row.command_id, "run_id": row.run_id, "account_id": row.account_id,
                    "client_order_id": row.client_order_id, "conid": row.instrument.conid,
                    "symbol": row.instrument.symbol, "side": row.side, "order_type": row.order_type,
                    "time_in_force": row.time_in_force, "quantity": row.quantity, "cash_quantity": row.cash_quantity,
                    "limit_price": row.limit_price, "stop_price": row.stop_price, "outside_rth": int(row.outside_rth),
                    "parent_command_id": row.parent_command_id, "oca_group": row.oca_group,
                    "strategy_id": row.strategy_id, "strategy_revision": row.strategy_revision,
                    "created_at": row.created_at.isoformat(),
                    "metadata_json": json.dumps(json_safe(row.metadata), separators=(",", ":"), sort_keys=True),
                }
                for row in intents
            ],
        )
        return len(intents)

    def persist_canonical_snapshot(self, snapshot: TradingStateSnapshot) -> None:
        recorded_at = snapshot.as_of.isoformat()
        self._insert_rows(
            "tr_broker_account_v2",
            [
                {
                    "provider": row.provider.value,
                    "account_id": row.account_id,
                    "base_currency": row.base_currency,
                    "account_type": row.account_type,
                    "alias": row.alias,
                    "title": row.title,
                    "parent_account_id": row.parent_account_id,
                    "can_view": int(row.can_view),
                    "can_trade": int(row.can_trade),
                    "valid_at": row.valid_at.isoformat(),
                    "recorded_at": recorded_at,
                    "raw_json": json.dumps(json_safe(row.raw), separators=(",", ":"), sort_keys=True),
                }
                for row in snapshot.accounts
            ],
        )
        self._insert_rows(
            "tr_account_value_event_v2",
            [
                {
                    "event_id": str(uuid4()), "account_id": row.account_id, "key": row.key, "segment": row.segment,
                    "value": row.value, "monetary_value": row.monetary_value, "currency": row.currency,
                    "is_null": int(row.is_null), "severity": row.severity,
                    "source_event_time": row.source_event_time.isoformat(), "received_at": row.received_at.isoformat(),
                    "raw_json": json.dumps(json_safe(row.raw), separators=(",", ":"), sort_keys=True),
                }
                for row in snapshot.account_values
            ],
        )
        self._insert_rows(
            "tr_ledger_event_v2",
            [
                {
                    "event_id": str(uuid4()), "account_id": row.account_id, "currency": row.currency,
                    "is_base": int(row.is_base),
                    "values_json": json.dumps(json_safe(row.values), separators=(",", ":"), sort_keys=True),
                    "source_event_time": row.source_event_time.isoformat(), "received_at": row.received_at.isoformat(),
                    "raw_json": json.dumps(json_safe(row.raw), separators=(",", ":"), sort_keys=True),
                }
                for row in snapshot.ledger
            ],
        )
        self._insert_rows(
            "tr_order_event_v2",
            [
                {
                    "event_id": str(uuid4()), "account_id": row.account_id, "broker_order_id": row.broker_order_id,
                    "client_order_id": row.client_order_id, "command_id": row.command_id,
                    "conid": row.instrument.conid, "symbol": row.instrument.symbol,
                    "lifecycle_state": row.lifecycle_state.value, "broker_status_raw": row.broker_status_raw,
                    "total_quantity": row.total_quantity, "filled_quantity": row.filled_quantity,
                    "remaining_quantity": row.remaining_quantity, "average_fill_price": row.average_fill_price,
                    "can_modify": int(row.can_modify), "can_cancel": int(row.can_cancel), "terminal": int(row.terminal),
                    "rejection_code": row.rejection_code, "rejection_reason": row.rejection_reason,
                    "source_event_time": row.source_event_time.isoformat(), "received_at": row.received_at.isoformat(),
                    "raw_json": json.dumps(json_safe(row.raw), separators=(",", ":"), sort_keys=True),
                }
                for row in snapshot.orders
            ],
        )
        self._insert_rows(
            "tr_execution_event_v2",
            [
                {
                    "event_id": str(uuid4()), "execution_id": row.execution_id, "account_id": row.account_id,
                    "broker_order_id": row.broker_order_id, "client_order_id": row.client_order_id,
                    "conid": row.instrument.conid, "symbol": row.instrument.symbol, "side": row.side,
                    "quantity": row.quantity, "price": row.price, "exchange": row.exchange,
                    "commission": row.commission, "commission_currency": row.commission_currency,
                    "commission_status": row.commission_status, "net_amount": row.net_amount, "liquidity": row.liquidity,
                    "source_event_time": row.source_event_time.isoformat(), "received_at": row.received_at.isoformat(),
                    "raw_json": json.dumps(json_safe(row.raw), separators=(",", ":"), sort_keys=True),
                }
                for row in snapshot.executions
            ],
        )
        positions_by_snapshot: dict[tuple[str, str], list[Any]] = {}
        for row in snapshot.positions:
            positions_by_snapshot.setdefault((row.account_id, row.snapshot_id), []).append(row)
        for account_id in snapshot.account_ids:
            if not any(key[0] == account_id for key in positions_by_snapshot):
                positions_by_snapshot[(account_id, str(uuid4()))] = []
        self._insert_rows(
            "tr_snapshot_manifest_v2",
            [
                {
                    "snapshot_id": snapshot_id, "entity_kind": "position", "account_id": account_id,
                    "provider": snapshot.provider.value,
                    "started_at": (min(row.received_at for row in rows) if rows else snapshot.as_of).isoformat(),
                    "completed_at": (max(row.received_at for row in rows) if rows else snapshot.as_of).isoformat(), "complete": 1,
                    "expected_pages": 1, "received_pages": 1, "item_count": len(rows),
                    "source_watermark": (max(row.source_event_time for row in rows) if rows else snapshot.as_of).isoformat(), "error": "",
                }
                for (account_id, snapshot_id), rows in positions_by_snapshot.items()
            ],
        )
        self._insert_rows(
            "tr_position_snapshot_item_v2",
            [
                {
                    "snapshot_id": row.snapshot_id, "account_id": row.account_id, "conid": row.instrument.conid,
                    "model": row.model, "symbol": row.instrument.symbol, "security_type": row.instrument.security_type,
                    "currency": row.instrument.currency, "quantity": row.quantity, "market_price": row.market_price,
                    "market_value": row.market_value, "average_cost": row.average_cost, "average_price": row.average_price,
                    "realized_pnl": row.realized_pnl, "unrealized_pnl": row.unrealized_pnl,
                    "source_event_time": row.source_event_time.isoformat(), "received_at": row.received_at.isoformat(),
                    "raw_json": json.dumps(json_safe(row.raw), separators=(",", ":"), sort_keys=True),
                }
                for row in snapshot.positions
            ],
        )
        self._insert_rows(
            "tr_round_trip_trade_v2",
            [
                {
                    "trade_id": row.trade_id,
                    "account_id": row.account_id,
                    "conid": row.instrument.conid,
                    "symbol": row.instrument.symbol,
                    "side": row.side,
                    "opened_at": row.opened_at.isoformat(),
                    "closed_at": row.closed_at.isoformat(),
                    "quantity": row.quantity,
                    "entry_price": row.entry_price,
                    "exit_price": row.exit_price,
                    "gross_pnl": row.gross_pnl,
                    "fees": row.fees,
                    "net_pnl": row.net_pnl,
                    "strategy_id": row.strategy_id,
                    "exit_reason": row.exit_reason,
                    "execution_ids": list(row.execution_ids),
                }
                for row in snapshot.closed_trades
            ],
        )
        episodes = (
            episodes_from_round_trips(snapshot.closed_trades)
            if snapshot.mode in {TradingMode.BACKTEST, TradingMode.BACKTEST_DEBUG} and snapshot.closed_trades
            else derive_trade_episodes(snapshot.executions)
        )
        self._insert_rows(
            "tr_trade_episode_v1",
            [
                {
                    "episode_id": row.episode_id, "account_id": row.account_id,
                    "conid": row.instrument.conid, "symbol": row.instrument.symbol, "side": row.side,
                    "opened_at": row.opened_at.isoformat(), "closed_at": row.closed_at.isoformat(),
                    "quantity": row.quantity, "entry_price": row.entry_price, "exit_price": row.exit_price,
                    "gross_pnl": row.gross_pnl, "fees": row.fees, "net_pnl": row.net_pnl,
                    "strategy_id": row.strategy_id, "strategy_revision": row.strategy_revision,
                    "run_id": row.run_id, "setup": row.setup, "exit_reason": row.exit_reason,
                    "mae": row.mae, "mfe": row.mfe, "planned_risk": row.planned_risk,
                    "execution_ids": list(row.execution_ids), "order_ids": list(row.order_ids),
                }
                for row in episodes
            ],
        )

    def _insert_rows(self, table: str, rows: list[dict[str, Any]]) -> None:
        if not rows:
            return
        body = "\n".join(json.dumps(json_safe(row), separators=(",", ":"), default=str) for row in rows)
        self._execute(f"INSERT INTO q_live.{table} FORMAT JSONEachRow", body)

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
