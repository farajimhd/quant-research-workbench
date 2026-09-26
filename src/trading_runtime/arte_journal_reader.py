"""Bounded, verified reads of normalized live/Backtest journal events."""
from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
import os
from typing import Any
from uuid import UUID

from src.trading_runtime.arte_journal_writer import (
    CommittedPrefix, V2CommittedPrefix, VerifiedPrefix, _CONTRACTS,
    _EVENT_DETAILS, _canonical_typed_content, _committed_batch_filter,
    _literal, _rows,
)
from src.trading_runtime.journal_contract import canonical_json


def readonly_typed_journal_client():
    """Open journal credentials with server-enforced read-only query settings."""
    from research.mlops.clickhouse import ClickHouseHttpClient

    url = os.environ.get("TRADING_JOURNAL_CLICKHOUSE_URL", "").strip()
    user = os.environ.get("TRADING_JOURNAL_CLICKHOUSE_USER", "").strip()
    password = os.environ.get("TRADING_JOURNAL_CLICKHOUSE_PASSWORD", "")
    market_user = os.environ.get("BACKTEST_CLICKHOUSE_USER", "").strip()
    if not url or not user or not password or user == market_user:
        raise ValueError("Typed journal review requires separate journal credentials")
    return ClickHouseHttpClient(
        url, user, password, timeout_seconds=60, persistent=True,
        default_query_params={"readonly": 1, "max_threads": 2,
                              "max_execution_time": 60},
    )


@dataclass(frozen=True, slots=True)
class TypedJournalEvent:
    event: dict[str, Any]
    detail_family: str | None
    detail: dict[str, Any] | None


def _verified_row(name: str, row: dict[str, Any]) -> dict[str, Any]:
    content = {key: value for key, value in row.items() if key != "content_hash"}
    canonical = _canonical_typed_content(name, content, stored_utc=True)
    digest = sha256(canonical_json(canonical).encode("utf-8")).hexdigest()
    if digest != str(row["content_hash"]):
        raise RuntimeError(f"Typed journal {name} row differs from its hash")
    return canonical


def load_typed_event_page(
    client: Any, prefix: VerifiedPrefix, *, after_sequence: int = 0,
    limit: int = 500,
) -> tuple[TypedJournalEvent, ...]:
    """Read one typed page with one batched detail query per present family."""
    from src.trading_runtime.arte_journal_commit_v4 import V4CommittedPrefix

    if not isinstance(prefix, (CommittedPrefix, V2CommittedPrefix,
                               V4CommittedPrefix)) or not prefix.batch_ids:
        raise ValueError("Typed event page requires a verified committed prefix")
    if after_sequence < 0 or not 1 <= limit <= 1000:
        raise ValueError("Typed event page bounds are invalid")
    event_columns = ",".join(column for column, _ in _CONTRACTS["trading_event_v1"].columns)
    events = _rows(client,
        f"SELECT {event_columns} FROM arte.trading_event_v1 "
        f"WHERE run_id={_literal(prefix.run_id)} "
        f"AND sequence>{after_sequence} AND sequence<={prefix.last_sequence} "
        f"{_committed_batch_filter(prefix)}"
        f"ORDER BY sequence LIMIT {limit} FORMAT JSONEachRow")
    if not events:
        if after_sequence < prefix.last_sequence:
            raise RuntimeError("Typed event page is missing committed rows")
        return ()
    allowed_batches = set(prefix.batch_ids)
    by_family: dict[str, set[str]] = {}
    sealed_events = []
    previous = after_sequence
    for raw in events:
        event = _verified_row("trading_event_v1", raw)
        sequence = int(event["sequence"])
        record_id = str(UUID(str(event["record_id"])))
        if (event["run_id"] != prefix.run_id or sequence != previous + 1
                or sequence > prefix.last_sequence
                or str(UUID(str(event["batch_id"]))) not in allowed_batches):
            raise RuntimeError("Typed event page differs from its committed prefix")
        previous = sequence
        kind = (event["category"], event["entity_type"])
        if kind not in _EVENT_DETAILS:
            raise RuntimeError("Typed event page contains an unknown detail contract")
        family = _EVENT_DETAILS[kind]
        if (family == "trading_strategy_signal_v1"
                and isinstance(prefix, (V2CommittedPrefix, V4CommittedPrefix))):
            family = "trading_strategy_signal_v2"
        if family is not None:
            by_family.setdefault(family, set()).add(record_id)
        sealed_events.append((record_id, event, family))
    if len(events) < limit and previous != prefix.last_sequence:
        raise RuntimeError("Typed event page ends before the committed prefix")
    details: dict[tuple[str, str], dict[str, Any]] = {}
    for family, identities in by_family.items():
        contract = (_CONTRACTS["trading_strategy_signal_v1"]
                    if family == "trading_strategy_signal_v2" else _CONTRACTS[family])
        columns = ",".join(column for column, _ in contract.columns)
        ids = ",".join(f"toUUID({_literal(value)})" for value in sorted(identities))
        rows = _rows(client,
            f"SELECT {columns} FROM arte.{family} "
            f"WHERE run_id={_literal(prefix.run_id)} AND record_id IN ({ids}) "
            f"{_committed_batch_filter(prefix)}"
            f"LIMIT {len(identities) + 1} FORMAT JSONEachRow")
        if len(rows) != len(identities):
            raise RuntimeError(f"Typed event page has missing or duplicate {family} rows")
        for raw in rows:
            hash_contract = ("trading_strategy_signal_v1"
                             if family == "trading_strategy_signal_v2" else family)
            detail = _verified_row(hash_contract, raw)
            record_id = str(UUID(str(detail["record_id"])))
            key = (family, record_id)
            if record_id not in identities or key in details:
                raise RuntimeError(f"Typed event page has an unexpected {family} row")
            details[key] = detail
    result = []
    for record_id, event, family in sealed_events:
        detail = details.get((family, record_id)) if family else None
        if family is not None and (detail is None
                or detail["run_id"] != event["run_id"]
                or detail["event_month"] != event["event_month"]
                or detail["batch_id"] != event["batch_id"]
                or detail["account_id"] != event["account_id"]):
            raise RuntimeError("Typed event detail differs from its parent")
        result.append(TypedJournalEvent(event, family, detail))
    return tuple(result)
