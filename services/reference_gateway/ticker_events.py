from __future__ import annotations

import hashlib
import json
import time
from dataclasses import asdict, dataclass
from datetime import UTC, date, datetime, timedelta
from typing import Any, Callable, Iterable

from research.mlops.clickhouse import ClickHouseHttpClient, quote_ident, sql_string
from services.reference_gateway.market_publications import mergetree_settings
from services.reference_gateway.providers import MassiveReferenceClient, MassiveTickerEventsResult


TICKER_EVENT_HISTORY_START = date(2003, 9, 10)
TICKER_EVENT_ENTITY_TABLE = "market_ticker_event_entity_v1"
TICKER_EVENT_TABLE = "market_ticker_event_v1"
TICKER_EVENT_COVERAGE_TABLE = "market_ticker_event_entity_coverage_v1"
SYMBOL_INTERVAL_TABLE = "id_symbol_interval_v1"
CLICKHOUSE_KEY_BATCH_SIZE = 1_000


@dataclass(frozen=True, slots=True)
class TickerEventEntity:
    provider_entity_key: str
    provider_identifier_kind: str
    provider_identifier: str
    current_ticker: str
    entity_name: str
    active: bool
    composite_figi: str
    share_class_figi: str
    cik: str
    primary_exchange: str
    currency_name: str
    provider_last_updated_utc: str
    source_payload_json: str
    source_content_sha256: str


@dataclass(frozen=True, slots=True)
class CanonicalBinding:
    status: str
    security_id: str = ""
    listing_id: str = ""
    reason: str = ""


@dataclass(frozen=True, slots=True)
class TickerEventInventoryResult:
    status: str
    active_rows: int
    inactive_rows: int
    entities: int
    rows_written: int
    rows_deleted: int
    pages: int
    saturated: bool
    run_id: str
    wall_seconds: float


@dataclass(frozen=True, slots=True)
class TickerEventSyncResult:
    status: str
    mode: str
    shard_index: int
    shard_count: int
    inventory_entities: int
    selected_entities: int
    completed_entities: int
    covered_empty_entities: int
    failed_entities: int
    unmapped_entities: int
    ambiguous_entities: int
    source_conflict_entities: int
    events_written: int
    event_tombstones_written: int
    intervals_written: int
    interval_tombstones_written: int
    run_id: str
    wall_seconds: float
    oldest_success_at_utc: str

    def public_dict(self) -> dict[str, Any]:
        return asdict(self)


def ensure_ticker_event_schema(
    client: ClickHouseHttpClient,
    *,
    database: str,
    storage_policy: str = "",
) -> None:
    settings = mergetree_settings(storage_policy)
    client.execute(f"CREATE DATABASE IF NOT EXISTS {quote_ident(database)}")
    client.execute(
        f"""
CREATE TABLE IF NOT EXISTS {table(database, TICKER_EVENT_ENTITY_TABLE)}
(
    provider_entity_key String,
    provider_identifier_kind LowCardinality(String),
    provider_identifier String,
    current_ticker String,
    entity_name String,
    active UInt8,
    composite_figi Nullable(String),
    share_class_figi Nullable(String),
    cik Nullable(String),
    primary_exchange Nullable(String),
    currency_name Nullable(String),
    provider_last_updated_utc Nullable(DateTime64(3, 'UTC')),
    source_payload_json String,
    source_content_sha256 String,
    is_deleted UInt8,
    observed_at_utc DateTime64(3, 'UTC'),
    source_run_id String,
    inserted_at DateTime64(3, 'UTC')
)
ENGINE = ReplacingMergeTree(inserted_at)
ORDER BY provider_entity_key
SETTINGS {settings}
""".strip()
    )
    client.execute(
        f"ALTER TABLE {table(database, TICKER_EVENT_ENTITY_TABLE)} "
        "ADD COLUMN IF NOT EXISTS is_deleted UInt8 DEFAULT 0 AFTER source_content_sha256"
    )
    client.execute(
        f"""
CREATE TABLE IF NOT EXISTS {table(database, TICKER_EVENT_TABLE)}
(
    ticker_event_id String,
    provider_entity_key String,
    provider_identifier_kind LowCardinality(String),
    provider_identifier String,
    security_id Nullable(String),
    listing_id Nullable(String),
    entity_name String,
    event_date Date,
    event_type LowCardinality(String),
    ticker Nullable(String),
    event_payload_json String,
    source_request_id Nullable(String),
    source_response_sha256 String,
    source_content_sha256 String,
    source_system LowCardinality(String),
    is_deleted UInt8,
    observed_at_utc DateTime64(3, 'UTC'),
    source_run_id String,
    inserted_at DateTime64(3, 'UTC')
)
ENGINE = ReplacingMergeTree(inserted_at)
PARTITION BY toYYYYMM(event_date)
ORDER BY (provider_entity_key, event_date, event_type, ticker_event_id)
SETTINGS {settings}
""".strip()
    )
    client.execute(
        f"""
CREATE TABLE IF NOT EXISTS {table(database, SYMBOL_INTERVAL_TABLE)}
(
    symbol_interval_id String,
    provider_entity_key String,
    provider_identifier_kind LowCardinality(String),
    provider_identifier String,
    security_id String,
    listing_id String,
    ticker String,
    ticker_normalized String,
    valid_from_date Date,
    valid_to_date_exclusive Nullable(Date),
    is_current UInt8,
    mapping_status LowCardinality(String),
    confidence_score Float64,
    source_event_id String,
    source_system LowCardinality(String),
    is_deleted UInt8,
    observed_at_utc DateTime64(3, 'UTC'),
    source_run_id String,
    source_content_sha256 String,
    inserted_at DateTime64(3, 'UTC')
)
ENGINE = ReplacingMergeTree(inserted_at)
PARTITION BY toYYYYMM(valid_from_date)
ORDER BY (security_id, listing_id, valid_from_date, ticker_normalized, symbol_interval_id)
SETTINGS {settings}
""".strip()
    )
    client.execute(
        f"""
CREATE TABLE IF NOT EXISTS {table(database, TICKER_EVENT_COVERAGE_TABLE)}
(
    provider_entity_key String,
    provider_identifier_kind LowCardinality(String),
    provider_identifier String,
    current_ticker String,
    security_id Nullable(String),
    listing_id Nullable(String),
    source_status LowCardinality(String),
    mapping_status LowCardinality(String),
    mapping_reason Nullable(String),
    event_count UInt32,
    active_event_count UInt32,
    min_event_date Nullable(Date),
    max_event_date Nullable(Date),
    source_response_sha256 Nullable(String),
    provider_last_updated_utc Nullable(DateTime64(3, 'UTC')),
    last_started_at_utc DateTime64(3, 'UTC'),
    last_finished_at_utc DateTime64(3, 'UTC'),
    last_success_at_utc Nullable(DateTime64(3, 'UTC')),
    next_due_at_utc DateTime64(3, 'UTC'),
    rows_written UInt64,
    rows_deleted UInt64,
    error_type Nullable(String),
    error_message Nullable(String),
    source_run_id String,
    inserted_at DateTime64(3, 'UTC')
)
ENGINE = ReplacingMergeTree(inserted_at)
ORDER BY provider_entity_key
SETTINGS {settings}
""".strip()
    )
    client.execute(
        f"ALTER TABLE {table(database, TICKER_EVENT_COVERAGE_TABLE)} "
        "ADD COLUMN IF NOT EXISTS mapping_reason Nullable(String) AFTER mapping_status"
    )


def refresh_ticker_event_inventory(
    client: ClickHouseHttpClient,
    provider: MassiveReferenceClient,
    *,
    database: str,
    execute: bool,
    run_id: str | None = None,
    on_progress: Callable[[str, str, str, int | None], None] | None = None,
) -> TickerEventInventoryResult:
    started = time.perf_counter()
    run_id = run_id or ticker_event_run_id("inventory")
    emit(on_progress, "massive_ticker_event_inventory", "running", "Fetching active US stock entity inventory.", None)
    active = provider.fetch_us_stock_tickers(active=True)
    emit(on_progress, "massive_ticker_event_inventory", "running", f"Fetched {len(active.tickers):,} active ticker rows; fetching inactive rows.", len(active.tickers))
    inactive = provider.fetch_us_stock_tickers(active=False)
    entities = normalize_inventory(active.tickers, inactive.tickers)
    saturated = active.saturated or inactive.saturated
    if saturated:
        emit(
            on_progress,
            "massive_ticker_event_inventory",
            "failed",
            (
                f"Provider pagination saturated after {active.pages + inactive.pages:,} pages; "
                "the existing inventory was preserved and no completeness claim was written."
            ),
            len(entities),
        )
        return TickerEventInventoryResult(
            status="failed",
            active_rows=len(active.tickers),
            inactive_rows=len(inactive.tickers),
            entities=len(entities),
            rows_written=0,
            rows_deleted=0,
            pages=active.pages + inactive.pages,
            saturated=True,
            run_id=run_id,
            wall_seconds=time.perf_counter() - started,
        )
    now = datetime.now(UTC)
    rows = [inventory_db_row(entity, run_id=run_id, now=now) for entity in entities]
    existing = load_raw_inventory_rows(client, database=database) if execute else []
    desired_keys = {entity.provider_entity_key for entity in entities}
    tombstones = tombstone_rows(existing, desired_keys, id_column="provider_entity_key", run_id=run_id)
    written = insert_json_rows(client, database, TICKER_EVENT_ENTITY_TABLE, rows) if execute else 0
    deleted = insert_json_rows(client, database, TICKER_EVENT_ENTITY_TABLE, tombstones) if execute else 0
    status = "completed"
    emit(
        on_progress,
        "massive_ticker_event_inventory",
        status,
        f"Entity inventory has {len(entities):,} stable identities from {len(active.tickers):,} active and {len(inactive.tickers):,} inactive rows; saturated={saturated}.",
        len(entities),
    )
    return TickerEventInventoryResult(
        status=status,
        active_rows=len(active.tickers),
        inactive_rows=len(inactive.tickers),
        entities=len(entities),
        rows_written=written,
        rows_deleted=deleted,
        pages=active.pages + inactive.pages,
        saturated=False,
        run_id=run_id,
        wall_seconds=time.perf_counter() - started,
    )


def sync_ticker_events(
    client: ClickHouseHttpClient,
    provider: MassiveReferenceClient,
    *,
    database: str,
    read_database: str | None = None,
    execute: bool,
    mode: str = "rolling",
    max_entities: int = 1_000,
    stale_after_days: int = 7,
    request_min_interval_seconds: float = 0.12,
    only_identifiers: Iterable[str] = (),
    shard_index: int = 0,
    shard_count: int = 1,
    run_id: str | None = None,
    on_progress: Callable[[str, str, str, int | None], None] | None = None,
) -> TickerEventSyncResult:
    if mode not in {"historical", "delta", "rolling", "reconcile"}:
        raise ValueError(f"unsupported ticker-event sync mode: {mode}")
    started = time.perf_counter()
    run_id = run_id or ticker_event_run_id(mode)
    inventory = load_ticker_event_inventory(client, database=database)
    coverage = load_ticker_event_coverage(client, database=database)
    selected = select_entities(
        inventory,
        coverage,
        mode=mode,
        max_entities=max_entities,
        stale_after_days=stale_after_days,
        only_identifiers=only_identifiers,
        shard_index=shard_index,
        shard_count=shard_count,
    )
    bindings = load_canonical_bindings(client, database=read_database or database, entities=selected)
    existing_events = load_existing_rows(client, database, TICKER_EVENT_TABLE, "ticker_event_id", selected)
    existing_intervals = load_existing_rows(client, database, SYMBOL_INTERVAL_TABLE, "symbol_interval_id", selected)
    completed = covered_empty = failed = unmapped = ambiguous = source_conflicts = 0
    events_written = event_tombstones = intervals_written = interval_tombstones = 0
    emit(
        on_progress,
        "massive_ticker_events",
        "running",
        f"Selected {len(selected):,}/{len(inventory):,} entities for {mode} ticker-event reconciliation.",
        0,
    )
    last_request_at = 0.0
    for index, entity in enumerate(selected, start=1):
        entity_started = datetime.now(UTC)
        binding = bindings.get(entity.provider_entity_key, CanonicalBinding("unmapped", reason="no_canonical_binding"))
        try:
            remaining = request_min_interval_seconds - (time.monotonic() - last_request_at)
            if remaining > 0:
                time.sleep(remaining)
            response = provider.fetch_ticker_events(entity.provider_identifier)
            last_request_at = time.monotonic()
            binding = reconcile_source_binding(entity, response, binding)
            if binding.status == "unmapped":
                unmapped += 1
            elif binding.status == "ambiguous":
                ambiguous += 1
            elif binding.status == "source_conflict":
                source_conflicts += 1
            normalized_events, intervals, response_sha = normalize_ticker_event_response(
                entity,
                response,
                binding,
                run_id=run_id,
                observed_at=datetime.now(UTC),
            )
            desired_event_ids = {str(row["ticker_event_id"]) for row in normalized_events}
            desired_interval_ids = {str(row["symbol_interval_id"]) for row in intervals}
            stale_event_rows = tombstone_rows(
                existing_events.get(entity.provider_entity_key, []),
                desired_event_ids,
                id_column="ticker_event_id",
                run_id=run_id,
            )
            stale_interval_rows = tombstone_rows(
                existing_intervals.get(entity.provider_entity_key, []),
                desired_interval_ids,
                id_column="symbol_interval_id",
                run_id=run_id,
            )
            if execute:
                events_written += insert_json_rows(client, database, TICKER_EVENT_TABLE, normalized_events)
                event_tombstones += insert_json_rows(client, database, TICKER_EVENT_TABLE, stale_event_rows)
                intervals_written += insert_json_rows(client, database, SYMBOL_INTERVAL_TABLE, intervals)
                interval_tombstones += insert_json_rows(client, database, SYMBOL_INTERVAL_TABLE, stale_interval_rows)
            source_status = "completed" if normalized_events else "covered_empty"
            completed += int(source_status == "completed")
            covered_empty += int(source_status == "covered_empty")
            finished = datetime.now(UTC)
            coverage_row = successful_coverage_row(
                entity,
                binding,
                normalized_events,
                source_status=source_status,
                response_sha=response_sha,
                run_id=run_id,
                started_at=entity_started,
                finished_at=finished,
                rows_written=len(normalized_events) + len(intervals),
                rows_deleted=len(stale_event_rows) + len(stale_interval_rows),
                stale_after_days=stale_after_days,
            )
            if execute:
                insert_json_rows(client, database, TICKER_EVENT_COVERAGE_TABLE, [coverage_row])
        except Exception as exc:  # noqa: BLE001
            failed += 1
            finished = datetime.now(UTC)
            previous = coverage.get(entity.provider_entity_key, {})
            failure_row = failed_coverage_row(
                entity,
                binding,
                exc,
                previous=previous,
                run_id=run_id,
                started_at=entity_started,
                finished_at=finished,
            )
            if execute:
                insert_json_rows(client, database, TICKER_EVENT_COVERAGE_TABLE, [failure_row])
        if index == 1 or index == len(selected) or index % 25 == 0:
            emit(
                on_progress,
                "massive_ticker_events",
                "running" if index < len(selected) else ("warning" if failed else "completed"),
                f"Ticker events {index:,}/{len(selected):,}; completed={completed:,} empty={covered_empty:,} failed={failed:,} unmapped={unmapped:,} ambiguous={ambiguous:,} conflicts={source_conflicts:,}.",
                index,
            )
    oldest_success = oldest_ticker_event_success(client, database=database) if execute else ""
    status = "failed" if selected and failed == len(selected) else "warning" if failed or ambiguous or unmapped or source_conflicts else "completed"
    if not selected:
        status = "skipped"
        emit(on_progress, "massive_ticker_events", "skipped", "No ticker-event entities are due.", 0)
    return TickerEventSyncResult(
        status=status,
        mode=mode,
        shard_index=shard_index,
        shard_count=shard_count,
        inventory_entities=len(inventory),
        selected_entities=len(selected),
        completed_entities=completed,
        covered_empty_entities=covered_empty,
        failed_entities=failed,
        unmapped_entities=unmapped,
        ambiguous_entities=ambiguous,
        source_conflict_entities=source_conflicts,
        events_written=events_written,
        event_tombstones_written=event_tombstones,
        intervals_written=intervals_written,
        interval_tombstones_written=interval_tombstones,
        run_id=run_id,
        wall_seconds=time.perf_counter() - started,
        oldest_success_at_utc=oldest_success,
    )


def normalize_inventory(active_rows: list[dict[str, Any]], inactive_rows: list[dict[str, Any]]) -> list[TickerEventEntity]:
    entities: dict[str, TickerEventEntity] = {}
    for active, rows in ((True, active_rows), (False, inactive_rows)):
        for raw in rows:
            if not isinstance(raw, dict):
                continue
            ticker = str(raw.get("ticker") or "").strip().upper()
            composite_figi = str(raw.get("composite_figi") or "").strip().upper()
            if composite_figi:
                identifier_kind = "composite_figi"
                identifier = composite_figi
            elif ticker:
                identifier_kind = "ticker"
                identifier = ticker
            else:
                continue
            entity_key = f"massive:{identifier_kind}:{identifier}"
            payload_json = canonical_json(raw)
            entity = TickerEventEntity(
                provider_entity_key=entity_key,
                provider_identifier_kind=identifier_kind,
                provider_identifier=identifier,
                current_ticker=ticker,
                entity_name=str(raw.get("name") or "").strip(),
                active=bool(raw.get("active", active)),
                composite_figi=composite_figi,
                share_class_figi=str(raw.get("share_class_figi") or "").strip().upper(),
                cik=str(raw.get("cik") or "").strip(),
                primary_exchange=str(raw.get("primary_exchange") or "").strip().upper(),
                currency_name=str(raw.get("currency_name") or raw.get("currency_symbol") or "").strip().upper(),
                provider_last_updated_utc=normalize_provider_datetime(raw.get("last_updated_utc")),
                source_payload_json=payload_json,
                source_content_sha256=sha256_text(payload_json),
            )
            previous = entities.get(entity_key)
            if previous is None or inventory_rank(entity) > inventory_rank(previous):
                entities[entity_key] = entity
    return sorted(entities.values(), key=lambda entity: (entity.current_ticker, entity.provider_entity_key))


def normalize_ticker_event_response(
    entity: TickerEventEntity,
    response: MassiveTickerEventsResult,
    binding: CanonicalBinding,
    *,
    run_id: str,
    observed_at: datetime,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], str]:
    stable_response = {"status": response.status, "name": response.name, "events": response.events}
    response_sha = sha256_text(canonical_json(stable_response))
    rows: list[dict[str, Any]] = []
    changes: list[tuple[date, str, str]] = []
    for raw in response.events:
        event_type = str(raw.get("type") or "").strip().lower()
        event_date = parse_event_date(raw.get("date"))
        if not event_type:
            raise ValueError(f"ticker-event type missing for {entity.provider_entity_key}")
        payload_json = canonical_json(raw)
        payload_sha = sha256_text(payload_json)
        ticker = ""
        if event_type == "ticker_change":
            ticker_change = raw.get("ticker_change")
            if not isinstance(ticker_change, dict):
                raise ValueError(f"ticker_change payload missing for {entity.provider_entity_key} on {event_date}")
            ticker = str(ticker_change.get("ticker") or "").strip().upper()
        event_id = stable_id("ticker_event", f"{entity.provider_entity_key}|{event_date.isoformat()}|{event_type}|{payload_sha}")
        if event_type == "ticker_change":
            changes.append((event_date, ticker, event_id))
        rows.append(
            {
                "ticker_event_id": event_id,
                "provider_entity_key": entity.provider_entity_key,
                "provider_identifier_kind": entity.provider_identifier_kind,
                "provider_identifier": entity.provider_identifier,
                "security_id": binding.security_id or None,
                "listing_id": binding.listing_id or None,
                "entity_name": response.name or entity.entity_name,
                "event_date": event_date.isoformat(),
                "event_type": event_type,
                "ticker": ticker or None,
                "event_payload_json": payload_json,
                "source_request_id": response.request_id or None,
                "source_response_sha256": response_sha,
                "source_content_sha256": payload_sha,
                "source_system": "massive",
                "is_deleted": 0,
                "observed_at_utc": dt64(observed_at),
                "source_run_id": run_id,
                "inserted_at": dt64(observed_at),
            }
        )
    intervals = build_symbol_intervals(entity, binding, changes, run_id=run_id, observed_at=observed_at)
    return rows, intervals, response_sha


def reconcile_source_binding(
    entity: TickerEventEntity,
    response: MassiveTickerEventsResult,
    binding: CanonicalBinding,
) -> CanonicalBinding:
    if binding.status != "mapped" or not response.events:
        return binding
    changes: list[tuple[date, str]] = []
    for raw in response.events:
        if str(raw.get("type") or "").strip().lower() != "ticker_change":
            continue
        payload = raw.get("ticker_change")
        ticker = str(payload.get("ticker") or "").strip().upper() if isinstance(payload, dict) else ""
        changes.append((parse_event_date(raw.get("date")), ticker))
    if not changes:
        return binding
    tickers_by_date: dict[date, set[str]] = {}
    for event_date, ticker in changes:
        tickers_by_date.setdefault(event_date, set()).add(ticker)
    conflicting_dates = sorted(event_date for event_date, tickers in tickers_by_date.items() if len(tickers) > 1)
    if conflicting_dates:
        conflict_date = conflicting_dates[-1]
        tickers = sorted(tickers_by_date[conflict_date])
        return CanonicalBinding(
            "source_conflict",
            security_id=binding.security_id,
            listing_id=binding.listing_id,
            reason=f"multiple_provider_tickers_on_date={conflict_date.isoformat()}:{','.join(ticker or '<empty>' for ticker in tickers)}",
        )
    latest_date, latest_ticker = max(changes, key=lambda item: item[0])
    expected = entity.current_ticker.strip().upper()
    conflict = (entity.active and not latest_ticker) or (latest_ticker and expected and latest_ticker != expected)
    if not conflict:
        return binding
    return CanonicalBinding(
        "source_conflict",
        security_id=binding.security_id,
        listing_id=binding.listing_id,
        reason=f"latest_provider_ticker={latest_ticker or '<empty>'}@{latest_date.isoformat()}; inventory_ticker={expected}; active={int(entity.active)}",
    )


def build_symbol_intervals(
    entity: TickerEventEntity,
    binding: CanonicalBinding,
    changes: list[tuple[date, str, str]],
    *,
    run_id: str,
    observed_at: datetime,
) -> list[dict[str, Any]]:
    if binding.status != "mapped":
        return []
    by_date: dict[date, tuple[str, str]] = {}
    for event_date, ticker, event_id in changes:
        existing = by_date.get(event_date)
        if existing is not None and existing[0] != ticker:
            raise ValueError(f"conflicting ticker changes for {entity.provider_entity_key} on {event_date.isoformat()}")
        by_date[event_date] = (ticker, event_id)
    ordered = sorted((event_date, value[0], value[1]) for event_date, value in by_date.items())
    intervals: list[dict[str, Any]] = []
    for index, (valid_from, ticker, event_id) in enumerate(ordered):
        if not ticker:
            continue
        valid_to = ordered[index + 1][0] if index + 1 < len(ordered) else None
        interval_id = stable_id("symbol_interval", f"{entity.provider_entity_key}|{valid_from.isoformat()}|{ticker}")
        digest = sha256_text(f"{entity.provider_entity_key}|{ticker}|{valid_from.isoformat()}|{valid_to or ''}|{event_id}")
        intervals.append(
            {
                "symbol_interval_id": interval_id,
                "provider_entity_key": entity.provider_entity_key,
                "provider_identifier_kind": entity.provider_identifier_kind,
                "provider_identifier": entity.provider_identifier,
                "security_id": binding.security_id,
                "listing_id": binding.listing_id,
                "ticker": ticker,
                "ticker_normalized": ticker,
                "valid_from_date": valid_from.isoformat(),
                "valid_to_date_exclusive": valid_to.isoformat() if valid_to else None,
                "is_current": int(valid_to is None),
                "mapping_status": "mapped",
                "confidence_score": 1.0,
                "source_event_id": event_id,
                "source_system": "massive",
                "is_deleted": 0,
                "observed_at_utc": dt64(observed_at),
                "source_run_id": run_id,
                "source_content_sha256": digest,
                "inserted_at": dt64(observed_at),
            }
        )
    return intervals


def load_ticker_event_inventory(client: ClickHouseHttpClient, *, database: str) -> list[TickerEventEntity]:
    rows = query_json_each_row(
        client,
        f"SELECT * FROM {table(database, TICKER_EVENT_ENTITY_TABLE)} FINAL WHERE is_deleted = 0 ORDER BY provider_entity_key",
    )
    return [
        TickerEventEntity(
            provider_entity_key=str(row.get("provider_entity_key") or ""),
            provider_identifier_kind=str(row.get("provider_identifier_kind") or ""),
            provider_identifier=str(row.get("provider_identifier") or ""),
            current_ticker=str(row.get("current_ticker") or ""),
            entity_name=str(row.get("entity_name") or ""),
            active=bool(row.get("active")),
            composite_figi=str(row.get("composite_figi") or ""),
            share_class_figi=str(row.get("share_class_figi") or ""),
            cik=str(row.get("cik") or ""),
            primary_exchange=str(row.get("primary_exchange") or ""),
            currency_name=str(row.get("currency_name") or ""),
            provider_last_updated_utc=normalize_provider_datetime(row.get("provider_last_updated_utc")),
            source_payload_json=str(row.get("source_payload_json") or "{}"),
            source_content_sha256=str(row.get("source_content_sha256") or ""),
        )
        for row in rows
    ]


def load_ticker_event_coverage(client: ClickHouseHttpClient, *, database: str) -> dict[str, dict[str, Any]]:
    rows = query_json_each_row(client, f"SELECT * FROM {table(database, TICKER_EVENT_COVERAGE_TABLE)} FINAL")
    return {str(row.get("provider_entity_key") or ""): row for row in rows}


def select_entities(
    inventory: list[TickerEventEntity],
    coverage: dict[str, dict[str, Any]],
    *,
    mode: str,
    max_entities: int,
    stale_after_days: int,
    only_identifiers: Iterable[str],
    shard_index: int = 0,
    shard_count: int = 1,
) -> list[TickerEventEntity]:
    if shard_count < 1:
        raise ValueError("shard_count must be at least 1")
    if shard_index < 0 or shard_index >= shard_count:
        raise ValueError("shard_index must be in [0, shard_count)")
    if shard_count > 1:
        inventory = [entity for entity in inventory if entity_shard(entity.provider_entity_key, shard_count) == shard_index]
    requested = {str(value).strip().upper() for value in only_identifiers if str(value).strip()}
    if requested:
        inventory = [
            entity
            for entity in inventory
            if entity.provider_identifier.upper() in requested or entity.current_ticker.upper() in requested
        ]
    now = datetime.now(UTC)
    stale_before = now - timedelta(days=max(1, int(stale_after_days)))

    def priority(entity: TickerEventEntity) -> tuple[int, datetime, str]:
        row = coverage.get(entity.provider_entity_key)
        if row is None:
            return (1, datetime.min.replace(tzinfo=UTC), entity.provider_entity_key)
        status = str(row.get("source_status") or "")
        mapping = str(row.get("mapping_status") or "")
        last_success = parse_datetime(row.get("last_success_at_utc"))
        provider_updated = parse_datetime(entity.provider_last_updated_utc)
        if status == "failed":
            return (0, last_success or datetime.min.replace(tzinfo=UTC), entity.provider_entity_key)
        if mapping in {"unmapped", "ambiguous", "weak_ticker"}:
            return (2, last_success or datetime.min.replace(tzinfo=UTC), entity.provider_entity_key)
        if provider_updated and (last_success is None or provider_updated > last_success):
            return (3, last_success or datetime.min.replace(tzinfo=UTC), entity.provider_entity_key)
        return (4, last_success or datetime.min.replace(tzinfo=UTC), entity.provider_entity_key)

    if mode == "reconcile":
        selected = inventory
    elif mode == "historical":
        selected = [
            entity
            for entity in inventory
            if entity.provider_entity_key not in coverage
            or str(coverage[entity.provider_entity_key].get("source_status") or "") == "failed"
        ]
    elif mode == "delta":
        selected = []
        for entity in inventory:
            row = coverage.get(entity.provider_entity_key)
            last_success = parse_datetime((row or {}).get("last_success_at_utc"))
            provider_updated = parse_datetime(entity.provider_last_updated_utc)
            if row is None or str(row.get("source_status") or "") == "failed":
                selected.append(entity)
            elif provider_updated and (last_success is None or provider_updated > last_success):
                selected.append(entity)
    else:
        selected = []
        for entity in inventory:
            row = coverage.get(entity.provider_entity_key)
            last_success = parse_datetime((row or {}).get("last_success_at_utc"))
            provider_updated = parse_datetime(entity.provider_last_updated_utc)
            due = (
                row is None
                or str(row.get("source_status") or "") == "failed"
                or str(row.get("mapping_status") or "") in {"unmapped", "ambiguous", "weak_ticker"}
                or last_success is None
                or last_success < stale_before
                or (provider_updated is not None and provider_updated > last_success)
            )
            if due:
                selected.append(entity)
    selected.sort(key=priority)
    if max_entities > 0:
        selected = selected[:max_entities]
    return selected


def entity_shard(provider_entity_key: str, shard_count: int) -> int:
    digest = hashlib.sha256(provider_entity_key.encode("utf-8")).digest()
    return int.from_bytes(digest[:8], byteorder="big", signed=False) % shard_count


def load_canonical_bindings(
    client: ClickHouseHttpClient,
    *,
    database: str,
    entities: list[TickerEventEntity],
) -> dict[str, CanonicalBinding]:
    identifiers = sorted({entity.provider_identifier for entity in entities if entity.provider_identifier_kind == "composite_figi"})
    if not identifiers:
        return {entity.provider_entity_key: CanonicalBinding("unmapped", reason="stable_composite_figi_missing") for entity in entities}
    identifier_rows: list[dict[str, Any]] = []
    for identifier_batch in batches(identifiers, CLICKHOUSE_KEY_BATCH_SIZE):
        literal_list = ",".join(sql_string(value) for value in identifier_batch)
        identifier_rows.extend(
            query_json_each_row(
                client,
                f"""
                SELECT upper(identifier_value_normalized) AS provider_identifier, security_id
                FROM {table(database, 'id_security_identifier_v1')} FINAL
                WHERE lower(identifier_kind) = 'composite_figi'
                  AND upper(identifier_value_normalized) IN ({literal_list})
                """,
            )
        )
    security_by_identifier: dict[str, set[str]] = {}
    for row in identifier_rows:
        security_by_identifier.setdefault(str(row.get("provider_identifier") or "").upper(), set()).add(str(row.get("security_id") or ""))
    security_ids = sorted({security_id for values in security_by_identifier.values() for security_id in values if security_id})
    listing_rows: list[dict[str, Any]] = []
    for security_batch in batches(security_ids, CLICKHOUSE_KEY_BATCH_SIZE):
        security_list = ",".join(sql_string(value) for value in security_batch)
        listing_rows.extend(
            query_json_each_row(
                client,
                f"""
                SELECT
                    l.security_id AS security_id,
                    l.listing_id AS listing_id,
                    l.currency_code AS currency_code,
                    l.is_primary_listing AS is_primary_listing,
                    ifNull(ex.iso_country_code, '') AS exchange_country,
                    ifNull(sym.ticker, '') AS ticker,
                    ifNull(sym.primary_symbol_flag, 0) AS primary_symbol_flag
                FROM {table(database, 'id_listing_v1')} l FINAL
                LEFT JOIN {table(database, 'ref_exchange_v1')} ex FINAL ON ex.exchange_code = l.exchange_code
                LEFT JOIN {table(database, 'id_symbol_v1')} sym FINAL ON sym.listing_id = l.listing_id AND sym.status = 'active'
                WHERE l.security_id IN ({security_list})
                  AND l.listing_status = 'active'
                """,
            )
        )
    listings_by_security: dict[str, list[dict[str, Any]]] = {}
    for row in listing_rows:
        listings_by_security.setdefault(str(row.get("security_id") or ""), []).append(row)
    output: dict[str, CanonicalBinding] = {}
    for entity in entities:
        if entity.provider_identifier_kind != "composite_figi":
            output[entity.provider_entity_key] = CanonicalBinding("weak_ticker", reason="stable_composite_figi_missing")
            continue
        security_matches = sorted(security_by_identifier.get(entity.provider_identifier.upper(), set()))
        if not security_matches:
            output[entity.provider_entity_key] = CanonicalBinding("unmapped", reason="composite_figi_not_in_canonical_graph")
            continue
        if len(security_matches) != 1:
            output[entity.provider_entity_key] = CanonicalBinding("ambiguous", reason="composite_figi_maps_multiple_securities")
            continue
        security_id = security_matches[0]
        candidates = [
            row
            for row in listings_by_security.get(security_id, [])
            if str(row.get("currency_code") or "").upper() == "USD" and str(row.get("exchange_country") or "").upper() == "US"
        ]
        exact = [row for row in candidates if str(row.get("ticker") or "").upper() == entity.current_ticker.upper()]
        preferred = exact or [row for row in candidates if int(row.get("is_primary_listing") or 0) == 1] or candidates
        listing_ids = sorted({str(row.get("listing_id") or "") for row in preferred if str(row.get("listing_id") or "")})
        if len(listing_ids) == 1:
            output[entity.provider_entity_key] = CanonicalBinding("mapped", security_id, listing_ids[0], "exact_composite_figi")
        elif not listing_ids:
            output[entity.provider_entity_key] = CanonicalBinding("unmapped", security_id, reason="no_active_usd_us_listing")
        else:
            output[entity.provider_entity_key] = CanonicalBinding("ambiguous", security_id, reason="multiple_candidate_usd_us_listings")
    return output


def load_existing_rows(
    client: ClickHouseHttpClient,
    database: str,
    table_name: str,
    id_column: str,
    entities: list[TickerEventEntity],
) -> dict[str, list[dict[str, Any]]]:
    entity_keys = [entity.provider_entity_key for entity in entities]
    if not entity_keys:
        return {}
    if scalar_int(client, f"SELECT count() FROM {table(database, table_name)} FINAL WHERE is_deleted = 0") == 0:
        return {}
    rows: list[dict[str, Any]] = []
    for entity_batch in batches(entity_keys, CLICKHOUSE_KEY_BATCH_SIZE):
        literals = ",".join(sql_string(value) for value in entity_batch)
        rows.extend(
            query_json_each_row(
                client,
                f"SELECT * FROM {table(database, table_name)} FINAL WHERE provider_entity_key IN ({literals}) AND is_deleted = 0",
            )
        )
    output: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        if not row.get(id_column):
            continue
        output.setdefault(str(row.get("provider_entity_key") or ""), []).append(row)
    return output


def batches(values: list[str], size: int) -> Iterable[list[str]]:
    if size <= 0:
        raise ValueError("batch size must be positive")
    for offset in range(0, len(values), size):
        yield values[offset : offset + size]


def tombstone_rows(
    existing_rows: list[dict[str, Any]],
    desired_ids: set[str],
    *,
    id_column: str,
    run_id: str,
) -> list[dict[str, Any]]:
    now = datetime.now(UTC)
    output: list[dict[str, Any]] = []
    for row in existing_rows:
        if str(row.get(id_column) or "") in desired_ids:
            continue
        tombstone = dict(row)
        tombstone["is_deleted"] = 1
        tombstone["observed_at_utc"] = dt64(now)
        tombstone["source_run_id"] = run_id
        tombstone["inserted_at"] = dt64(now)
        output.append(tombstone)
    return output


def successful_coverage_row(
    entity: TickerEventEntity,
    binding: CanonicalBinding,
    events: list[dict[str, Any]],
    *,
    source_status: str,
    response_sha: str,
    run_id: str,
    started_at: datetime,
    finished_at: datetime,
    rows_written: int,
    rows_deleted: int,
    stale_after_days: int,
) -> dict[str, Any]:
    dates = sorted(str(row["event_date"]) for row in events)
    return {
        "provider_entity_key": entity.provider_entity_key,
        "provider_identifier_kind": entity.provider_identifier_kind,
        "provider_identifier": entity.provider_identifier,
        "current_ticker": entity.current_ticker,
        "security_id": binding.security_id or None,
        "listing_id": binding.listing_id or None,
        "source_status": source_status,
        "mapping_status": binding.status,
        "mapping_reason": binding.reason or None,
        "event_count": len(events),
        "active_event_count": len(events),
        "min_event_date": dates[0] if dates else None,
        "max_event_date": dates[-1] if dates else None,
        "source_response_sha256": response_sha,
        "provider_last_updated_utc": entity.provider_last_updated_utc or None,
        "last_started_at_utc": dt64(started_at),
        "last_finished_at_utc": dt64(finished_at),
        "last_success_at_utc": dt64(finished_at),
        "next_due_at_utc": dt64(finished_at + timedelta(days=max(1, stale_after_days))),
        "rows_written": max(0, rows_written),
        "rows_deleted": max(0, rows_deleted),
        "error_type": None,
        "error_message": None,
        "source_run_id": run_id,
        "inserted_at": dt64(finished_at),
    }


def failed_coverage_row(
    entity: TickerEventEntity,
    binding: CanonicalBinding,
    exc: Exception,
    *,
    previous: dict[str, Any],
    run_id: str,
    started_at: datetime,
    finished_at: datetime,
) -> dict[str, Any]:
    return {
        "provider_entity_key": entity.provider_entity_key,
        "provider_identifier_kind": entity.provider_identifier_kind,
        "provider_identifier": entity.provider_identifier,
        "current_ticker": entity.current_ticker,
        "security_id": binding.security_id or None,
        "listing_id": binding.listing_id or None,
        "source_status": "failed",
        "mapping_status": binding.status,
        "mapping_reason": binding.reason or None,
        "event_count": int(previous.get("event_count") or 0),
        "active_event_count": int(previous.get("active_event_count") or 0),
        "min_event_date": previous.get("min_event_date") or None,
        "max_event_date": previous.get("max_event_date") or None,
        "source_response_sha256": previous.get("source_response_sha256") or None,
        "provider_last_updated_utc": entity.provider_last_updated_utc or None,
        "last_started_at_utc": dt64(started_at),
        "last_finished_at_utc": dt64(finished_at),
        "last_success_at_utc": previous.get("last_success_at_utc") or None,
        "next_due_at_utc": dt64(finished_at),
        "rows_written": 0,
        "rows_deleted": 0,
        "error_type": type(exc).__name__,
        "error_message": safe_error(exc),
        "source_run_id": run_id,
        "inserted_at": dt64(finished_at),
    }


def ticker_event_audit(
    client: ClickHouseHttpClient,
    *,
    database: str,
    read_database: str | None = None,
) -> list[dict[str, Any]]:
    identity_database = read_database or database
    required = (TICKER_EVENT_ENTITY_TABLE, TICKER_EVENT_TABLE, TICKER_EVENT_COVERAGE_TABLE, SYMBOL_INTERVAL_TABLE)
    existing = {
        row["name"]
        for row in query_json_each_row(
            client,
            f"SELECT name FROM system.tables WHERE database = {sql_string(database)} AND name IN ({','.join(sql_string(name) for name in required)})",
        )
    }
    if len(existing) != len(required):
        return [{"check": "schema", "count": len(required) - len(existing), "status": "failed", "missing": sorted(set(required) - existing)}]
    checks = [
        (
            "entity_coverage_gaps",
            f"SELECT count() FROM {table(database, TICKER_EVENT_ENTITY_TABLE)} e FINAL LEFT JOIN {table(database, TICKER_EVENT_COVERAGE_TABLE)} c FINAL USING provider_entity_key WHERE e.is_deleted = 0 AND c.provider_entity_key = ''",
        ),
        (
            "failed_entities",
            f"SELECT count() FROM {table(database, TICKER_EVENT_COVERAGE_TABLE)} FINAL WHERE source_status = 'failed'",
        ),
        (
            "ambiguous_entities",
            f"SELECT count() FROM {table(database, TICKER_EVENT_COVERAGE_TABLE)} FINAL WHERE mapping_status = 'ambiguous'",
        ),
        (
            "source_conflict_entities",
            f"SELECT count() FROM {table(database, TICKER_EVENT_COVERAGE_TABLE)} FINAL WHERE mapping_status = 'source_conflict'",
        ),
        (
            "orphan_intervals",
            f"SELECT count() FROM {table(database, SYMBOL_INTERVAL_TABLE)} i FINAL LEFT JOIN {table(identity_database, 'id_security_v1')} s FINAL USING security_id LEFT JOIN {table(identity_database, 'id_listing_v1')} l FINAL USING listing_id WHERE i.is_deleted = 0 AND (s.security_id = '' OR l.listing_id = '')",
        ),
        (
            "overlapping_intervals",
            f"""
            SELECT count()
            FROM {table(database, SYMBOL_INTERVAL_TABLE)} a FINAL
            INNER JOIN {table(database, SYMBOL_INTERVAL_TABLE)} b FINAL
              ON a.provider_entity_key = b.provider_entity_key
             AND a.symbol_interval_id < b.symbol_interval_id
             AND a.valid_from_date < coalesce(b.valid_to_date_exclusive, toDate('2100-01-01'))
             AND b.valid_from_date < coalesce(a.valid_to_date_exclusive, toDate('2100-01-01'))
            WHERE a.is_deleted = 0 AND b.is_deleted = 0
            """,
        ),
        (
            "current_ticker_mismatch",
            f"SELECT count() FROM {table(database, SYMBOL_INTERVAL_TABLE)} i FINAL INNER JOIN {table(database, TICKER_EVENT_ENTITY_TABLE)} e FINAL USING provider_entity_key WHERE i.is_deleted = 0 AND e.is_deleted = 0 AND i.is_current = 1 AND upper(i.ticker) != upper(e.current_ticker)",
        ),
    ]
    output: list[dict[str, Any]] = []
    warning_checks = {"ambiguous_entities", "source_conflict_entities"}
    for name, sql in checks:
        count = scalar_int(client, sql)
        status = "ok" if count == 0 else "warning" if name in warning_checks else "failed"
        output.append({"check": name, "count": count, "status": status})
    return output


def point_in_time_symbol_sql(*, database: str, ticker_expression: str, date_expression: str) -> str:
    return (
        f"SELECT security_id, listing_id, ticker, provider_identifier FROM {table(database, SYMBOL_INTERVAL_TABLE)} FINAL "
        f"WHERE is_deleted = 0 AND upper(ticker_normalized) = upper({ticker_expression}) "
        f"AND valid_from_date <= {date_expression} "
        f"AND (valid_to_date_exclusive IS NULL OR {date_expression} < valid_to_date_exclusive)"
    )


def inventory_db_row(entity: TickerEventEntity, *, run_id: str, now: datetime) -> dict[str, Any]:
    return {
        "provider_entity_key": entity.provider_entity_key,
        "provider_identifier_kind": entity.provider_identifier_kind,
        "provider_identifier": entity.provider_identifier,
        "current_ticker": entity.current_ticker,
        "entity_name": entity.entity_name,
        "active": int(entity.active),
        "composite_figi": entity.composite_figi or None,
        "share_class_figi": entity.share_class_figi or None,
        "cik": entity.cik or None,
        "primary_exchange": entity.primary_exchange or None,
        "currency_name": entity.currency_name or None,
        "provider_last_updated_utc": entity.provider_last_updated_utc or None,
        "source_payload_json": entity.source_payload_json,
        "source_content_sha256": entity.source_content_sha256,
        "is_deleted": 0,
        "observed_at_utc": dt64(now),
        "source_run_id": run_id,
        "inserted_at": dt64(now),
    }


def insert_json_rows(client: ClickHouseHttpClient, database: str, table_name: str, rows: list[dict[str, Any]]) -> int:
    if not rows:
        return 0
    body = "\n".join(json.dumps(row, ensure_ascii=False, separators=(",", ":"), default=str) for row in rows)
    client.execute(f"INSERT INTO {table(database, table_name)} FORMAT JSONEachRow\n{body}")
    return len(rows)


def load_raw_inventory_rows(client: ClickHouseHttpClient, *, database: str) -> list[dict[str, Any]]:
    return query_json_each_row(
        client,
        f"SELECT * FROM {table(database, TICKER_EVENT_ENTITY_TABLE)} FINAL WHERE is_deleted = 0",
    )


def query_json_each_row(client: ClickHouseHttpClient, sql: str) -> list[dict[str, Any]]:
    text = client.execute(sql.rstrip().rstrip(";") + " FORMAT JSONEachRow").strip()
    return [json.loads(line) for line in text.splitlines() if line.strip()]


def scalar_int(client: ClickHouseHttpClient, sql: str) -> int:
    value = client.execute(sql.rstrip().rstrip(";") + " FORMAT TSV").strip()
    return int(value or "0")


def oldest_ticker_event_success(client: ClickHouseHttpClient, *, database: str) -> str:
    value = client.execute(
        f"SELECT toString(min(last_success_at_utc)) FROM {table(database, TICKER_EVENT_COVERAGE_TABLE)} FINAL WHERE last_success_at_utc IS NOT NULL FORMAT TSV"
    ).strip()
    return "" if value in {"", "\\N", "1970-01-01 00:00:00.000"} else value


def table(database: str, name: str) -> str:
    return f"{quote_ident(database)}.{quote_ident(name)}"


def ticker_event_run_id(kind: str) -> str:
    return f"ticker_events_{kind}_{datetime.now(UTC).strftime('%Y%m%d_%H%M%S')}"


def stable_id(prefix: str, value: str) -> str:
    return f"{prefix}:{hashlib.sha256(value.encode('utf-8')).hexdigest()[:32]}"


def canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)


def sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def parse_event_date(value: Any) -> date:
    try:
        parsed = date.fromisoformat(str(value or "")[:10])
    except ValueError as exc:
        raise ValueError(f"invalid ticker-event date: {value!r}") from exc
    if parsed < TICKER_EVENT_HISTORY_START:
        raise ValueError(f"ticker-event date precedes provider history frontier: {parsed.isoformat()}")
    return parsed


def parse_datetime(value: Any) -> datetime | None:
    text = str(value or "").strip()
    if not text or text == "\\N":
        return None
    text = text.replace(" ", "T", 1)
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    return parsed.replace(tzinfo=UTC) if parsed.tzinfo is None else parsed.astimezone(UTC)


def normalize_provider_datetime(value: Any) -> str:
    parsed = parse_datetime(value)
    return dt64(parsed) if parsed else ""


def dt64(value: datetime) -> str:
    return value.astimezone(UTC).strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]


def inventory_rank(entity: TickerEventEntity) -> tuple[int, str, str]:
    return (int(entity.active), entity.provider_last_updated_utc, entity.current_ticker)


def safe_error(exc: Exception, *, max_chars: int = 500) -> str:
    text = " ".join(str(exc).split())
    return text[:max_chars]


def emit(
    callback: Callable[[str, str, str, int | None], None] | None,
    source: str,
    status: str,
    message: str,
    rows: int | None,
) -> None:
    if callback is not None:
        callback(source, status, message, rows)
