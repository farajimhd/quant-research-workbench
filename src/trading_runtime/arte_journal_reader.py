"""Bounded, verified reads of normalized live/Backtest journal events."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from hashlib import sha256
import os
import re
from typing import Any
from uuid import UUID

from src.trading_runtime.arte_journal_writer import (
    ACKNOWLEDGEMENT, CANCEL, REPRICE, RISK_ACTION_TABLES,
    PROTECTION_CHANGE_TABLES, PROTECTION_RECONCILIATION_TABLES,
    V4_ALLOCATION, CommittedPrefix, V2CommittedPrefix, VerifiedPrefix, _CONTRACTS,
    _EVENT_DETAILS, _canonical_typed_content, _committed_batch_filter,
    _literal, _rows,
)
from src.trading_runtime.journal_contract import canonical_json
from src.trading_runtime.journal_contract import JournalRecord


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


@dataclass(frozen=True, slots=True)
class TypedProtectionPage:
    next_sequence: int
    records: tuple[JournalRecord, ...]


@dataclass(frozen=True, slots=True)
class CompleteProtectionHistory:
    run_id: str
    through_sequence: int
    committed_batch_ids: tuple[str, ...]
    records: tuple[JournalRecord, ...]


def _journal_instant(value: Any) -> datetime:
    source = str(value)
    match = re.fullmatch(r"(\d{4}-\d\d-\d\d) (\d\d:\d\d:\d\d)\.(\d{6})(\d{3})?", source)
    if match is None or match.group(4) not in (None, "000"):
        raise RuntimeError("Typed protection clock cannot fit a causal Python instant")
    return datetime.fromisoformat(
        f"{match.group(1)}T{match.group(2)}.{match.group(3)}+00:00"
    ).astimezone(timezone.utc)


def load_typed_protection_page(
    client: Any, prefix: VerifiedPrefix, *, after_sequence: int = 0,
    limit: int = 500, max_children: int = 50_000,
) -> TypedProtectionPage:
    """Cold-read one V4 event page and all normalized protection children.

    The cursor advances across non-protection events too. The caller must page
    through the entire committed prefix before treating absence of a target
    amendment as proven. This reader never grants live order admission.
    """
    from src.backend.backtest_protection_change_v3 import (
        recover_protection_change_payload,
    )
    from src.trading_runtime.arte_journal_commit_v4 import V4CommittedPrefix

    if (not isinstance(prefix, V4CommittedPrefix)
            or not prefix.batch_ids or type(prefix.last_sequence) is not int
            or prefix.last_sequence < 1
            or type(max_children) is not int or max_children < 0):
        raise ValueError("Typed protection page needs V4 authority and a child bound")
    page = load_typed_event_page(
        client, prefix, after_sequence=after_sequence, limit=limit)
    selected = [item for item in page
                if (item.event["category"], item.event["entity_type"])
                == ("protection", "protection_change")]
    expected = sum(int(item.detail["entry_order_count"]) for item in selected)
    if expected > max_children:
        raise RuntimeError("Typed protection page exceeds its child bound")
    children: dict[str, list[dict[str, Any]]] = {}
    if selected:
        name = PROTECTION_CHANGE_TABLES[1].name
        columns = ",".join(column for column, _ in _CONTRACTS[name].columns)
        identities = {str(UUID(str(item.event["record_id"]))) for item in selected}
        ids = ",".join(f"toUUID({_literal(value)})" for value in sorted(identities))
        rows = _rows(client,
            f"SELECT {columns} FROM arte.{name} "
            f"WHERE run_id={_literal(prefix.run_id)} AND record_id IN ({ids}) "
            f"{_committed_batch_filter(prefix)}"
            f"LIMIT {expected + 1} FORMAT JSONEachRow")
        if len(rows) != expected:
            raise RuntimeError("Typed protection page has missing or excess children")
        for row in rows:
            identity = str(UUID(str(row["record_id"])))
            if identity not in identities:
                raise RuntimeError("Typed protection child has a foreign parent")
            children.setdefault(identity, []).append(row)
    result = []
    for item in selected:
        event, detail = item.event, item.detail
        if detail is None:
            raise RuntimeError("Typed protection detail is missing")
        identity = str(UUID(str(event["record_id"])))
        ordered = sorted(children.get(identity, ()), key=lambda row: int(row["ordinal"]))
        payload = recover_protection_change_payload(event, detail, ordered)
        result.append(JournalRecord(
            identity, prefix.run_id, int(event["sequence"]),
            _journal_instant(event["event_time"]),
            _journal_instant(event["recorded_at"]),
            "protection", "protection_change", str(event["entity_id"]),
            str(event["account_id"]), payload,
        ))
    return TypedProtectionPage(
        int(page[-1].event["sequence"]) if page else after_sequence,
        tuple(result),
    )


def load_complete_typed_protection_history(
    client: Any, prefix: VerifiedPrefix, *, page_size: int = 500,
    max_events: int = 100_000, max_children_per_page: int = 50_000,
) -> CompleteProtectionHistory:
    """Prove the complete V4 protection history through one committed head.

    No absence claim is valid after only one page. An over-budget live run must
    use a separately certified recovery anchor rather than silently truncating
    this scan. This is evidence for later OMS recovery, not execution authority.
    """
    from src.trading_runtime.arte_journal_commit_v4 import V4CommittedPrefix

    if (not isinstance(prefix, V4CommittedPrefix)
            or not prefix.batch_ids or type(prefix.last_sequence) is not int
            or prefix.last_sequence < 1
            or type(page_size) is not int or not 1 <= page_size <= 1000
            or type(max_events) is not int or max_events < 1
            or type(max_children_per_page) is not int
            or max_children_per_page < 0):
        raise ValueError("Complete protection history needs bounded V4 authority")
    if prefix.last_sequence > max_events:
        raise RuntimeError("Committed protection history exceeds its event bound")
    cursor = 0
    records: list[JournalRecord] = []
    while cursor < prefix.last_sequence:
        page = load_typed_protection_page(
            client, prefix, after_sequence=cursor, limit=page_size,
            max_children=max_children_per_page)
        if not cursor < page.next_sequence <= prefix.last_sequence:
            raise RuntimeError("Committed protection history did not advance")
        if any(not cursor < row.sequence <= page.next_sequence
               for row in page.records):
            raise RuntimeError("Committed protection record is outside its page")
        records.extend(page.records)
        cursor = page.next_sequence
    return CompleteProtectionHistory(
        prefix.run_id, prefix.last_sequence, tuple(prefix.batch_ids),
        tuple(records))


# V4 supplements use the same event parent but replace or extend the V1
# detail family. The writer seals these named tables in its V4 commit; review
# must follow that exact contract rather than the legacy-only V1 map.
_V4_EVENT_DETAILS = {
    ("broker", "order_acknowledgement"): ACKNOWLEDGEMENT.name,
    ("broker", "order_cancel_requested"): CANCEL.name,
    ("command", "order_cancel"): CANCEL.name,
    ("broker", "order_repriced"): REPRICE.name,
    ("broker", "order_reprice_error"): REPRICE.name,
    ("portfolio_management", "portfolio_allocation"): V4_ALLOCATION.name,
    ("protection", "protection_change"): PROTECTION_CHANGE_TABLES[0].name,
    ("order_management", "protection_reconciliation"):
        PROTECTION_RECONCILIATION_TABLES[0].name,
    ("risk", "kill_entry_order"): RISK_ACTION_TABLES[0].name,
    ("risk", "emergency_flatten"): RISK_ACTION_TABLES[0].name,
    ("snapshot", "portfolio"): "trading_backtest_account_snapshot_v2",
    ("snapshot", "position"): "trading_backtest_position_snapshot_v2",
}


def _detail_family(prefix: VerifiedPrefix, kind: tuple[str, str]) -> str | None:
    from src.trading_runtime.arte_journal_commit_v4 import V4CommittedPrefix

    if isinstance(prefix, V4CommittedPrefix) and kind in _V4_EVENT_DETAILS:
        return _V4_EVENT_DETAILS[kind]
    if kind in _EVENT_DETAILS:
        return _EVENT_DETAILS[kind]
    raise RuntimeError("Typed event page contains an unknown detail contract")


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
        family = _detail_family(prefix, kind)
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
                or ("account_id" in detail
                    and detail["account_id"] != event["account_id"])):
            raise RuntimeError("Typed event detail differs from its parent")
        result.append(TypedJournalEvent(event, family, detail))
    return tuple(result)
