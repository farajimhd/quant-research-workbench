from __future__ import annotations

import json
import socket
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from research.mlops.clickhouse import ClickHouseHttpClient, default_clickhouse_password, default_clickhouse_url, default_clickhouse_user, quote_ident
from services.ibkr_gateway_supervisor.config import IbkrGatewayConfig


class SupervisorEventLog:
    def __init__(self, config: IbkrGatewayConfig) -> None:
        self.config = config
        self.run_id = datetime.now(UTC).strftime("ibkr_gateway_%Y%m%d_%H%M%S_") + uuid.uuid4().hex[:8]
        self.event_log_path = config.log_root / self.run_id / "ibkr_gateway_supervisor_events.jsonl"
        self._clickhouse_client: ClickHouseHttpClient | None = None
        self._clickhouse_ready = False
        self._clickhouse_disabled_reason = ""
        if config.event_log_jsonl_enabled:
            self.event_log_path.parent.mkdir(parents=True, exist_ok=True)

    @property
    def clickhouse_status(self) -> str:
        if not self.config.clickhouse_log_enabled:
            return "disabled"
        if self._clickhouse_disabled_reason:
            return "failed"
        if self._clickhouse_ready:
            return "ready"
        return "not_started"

    @property
    def clickhouse_error(self) -> str:
        return self._clickhouse_disabled_reason

    def write(self, event: dict[str, Any]) -> None:
        row = dict(event)
        row.setdefault("run_id", self.run_id)
        row.setdefault("account_key", self.config.account_key)
        row.setdefault("host", socket.gethostname())
        if self.config.event_log_jsonl_enabled:
            self.write_jsonl(row)
        if self.config.clickhouse_log_enabled:
            self.write_clickhouse(row)

    def write_jsonl(self, row: dict[str, Any]) -> None:
        self.event_log_path.parent.mkdir(parents=True, exist_ok=True)
        with self.event_log_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(row, sort_keys=True, ensure_ascii=False, default=str) + "\n")

    def write_clickhouse(self, row: dict[str, Any]) -> None:
        if self._clickhouse_disabled_reason:
            return
        try:
            client = self.clickhouse_client()
            compact = clickhouse_row(row, self.config)
            body = json.dumps(compact, separators=(",", ":"), ensure_ascii=False, default=str)
            target = f"{quote_ident(self.config.clickhouse_database)}.{quote_ident(self.config.clickhouse_table)}"
            client.execute(f"INSERT INTO {target} FORMAT JSONEachRow\n{body}")
        except Exception as exc:  # noqa: BLE001
            self._clickhouse_disabled_reason = f"{type(exc).__name__}: {exc}"

    def clickhouse_client(self) -> ClickHouseHttpClient:
        if self._clickhouse_client is None:
            self._clickhouse_client = ClickHouseHttpClient(default_clickhouse_url(), default_clickhouse_user(), default_clickhouse_password())
            ensure_clickhouse_table(self._clickhouse_client, self.config.clickhouse_database, self.config.clickhouse_table)
            self._clickhouse_ready = True
        return self._clickhouse_client


def ensure_clickhouse_table(client: ClickHouseHttpClient, database: str, table: str) -> None:
    client.execute(f"CREATE DATABASE IF NOT EXISTS {quote_ident(database)}")
    target = f"{quote_ident(database)}.{quote_ident(table)}"
    client.execute(
        f"""
CREATE TABLE IF NOT EXISTS {target}
(
    ts_utc DateTime64(3, 'UTC'),
    run_id String,
    account_key LowCardinality(String),
    event LowCardinality(String),
    severity LowCardinality(String),
    status_code Int32,
    authenticated UInt8,
    gateway_reachable UInt8,
    message String,
    payload_json String,
    host LowCardinality(String)
)
ENGINE = MergeTree
PARTITION BY toYYYYMM(ts_utc)
ORDER BY (account_key, event, ts_utc, run_id)
TTL toDateTime(ts_utc) + INTERVAL 30 DAY
SETTINGS index_granularity = 8192
""".strip()
    )


def clickhouse_row(row: dict[str, Any], config: IbkrGatewayConfig) -> dict[str, Any]:
    payload = {key: value for key, value in row.items() if key not in {"ts_utc", "run_id", "account_key", "host"}}
    result = payload.get("result") if isinstance(payload.get("result"), dict) else {}
    status = payload.get("status") if isinstance(payload.get("status"), dict) else {}
    status_code = int(result.get("status_code") or status.get("status_code") or 0)
    event_name = str(row.get("event") or "")
    return {
        "ts_utc": parse_ts(row.get("ts_utc")),
        "run_id": str(row.get("run_id") or ""),
        "account_key": str(row.get("account_key") or config.account_key),
        "event": event_name,
        "severity": severity_for_event(event_name, payload),
        "status_code": status_code,
        "authenticated": 1 if bool(payload.get("authenticated")) else 0,
        "gateway_reachable": 1 if event_name in {"gateway_reachable", "auth_status", "tickle", "accounts"} else 0,
        "message": event_message(event_name, payload),
        "payload_json": json.dumps(payload, sort_keys=True, ensure_ascii=False, default=str),
        "host": str(row.get("host") or socket.gethostname()),
    }


def parse_ts(value: Any) -> str:
    text = str(value or "").strip()
    if not text:
        return clickhouse_ts(datetime.now(UTC))
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return text
    return clickhouse_ts(parsed.astimezone(UTC))


def clickhouse_ts(value: datetime) -> str:
    return value.astimezone(UTC).strftime("%Y-%m-%d %H:%M:%S.%f")[:23]


def severity_for_event(event: str, payload: dict[str, Any]) -> str:
    if "failed" in event or "error" in event:
        return "error"
    if "required" in event or "waiting" in event or "skipped" in event:
        return "warning"
    result = payload.get("result") if isinstance(payload.get("result"), dict) else {}
    if result and not result.get("ok", False):
        return "warning"
    return "info"


def event_message(event: str, payload: dict[str, Any]) -> str:
    for key in ("message", "error", "reason"):
        if payload.get(key):
            return str(payload[key])[:500]
    result = payload.get("result") if isinstance(payload.get("result"), dict) else {}
    if result.get("error"):
        return str(result["error"])[:500]
    return event
