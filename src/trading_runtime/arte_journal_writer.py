"""Non-blocking submission of normalized ARTE journal rows.

This is a transport primitive, not the live/Backtest cutover. Producers must
first map every logical record to its typed family rows. The caller never waits
for ClickHouse in ``submit``; its future becomes durable only after the commit
row is verified. No local file is used.
"""
from __future__ import annotations

from concurrent.futures import Future
from dataclasses import dataclass
from datetime import date, datetime, timezone
from decimal import Decimal, InvalidOperation, localcontext
from hashlib import sha256
import json
import os
import re
from queue import Empty, Full, Queue
from threading import Thread
from types import MappingProxyType
from typing import Any, Mapping
from uuid import UUID

from src.trading_runtime.arte_journal_schema import TABLES, journal_permission_preflight, storage_preflight
from src.trading_runtime.journal_contract import canonical_json


_CONTRACTS = {table.name: table for table in TABLES}
_FAMILIES = (
    ("trading_event_v1", "events", "event_count", "event_hash"),
    ("trading_strategy_signal_v1", "signals", "signal_count", "signal_hash"),
    ("trading_signal_source_v1", "signal_sources", "signal_source_count",
     "signal_source_hash"),
    ("trading_execution_v1", "executions", "execution_count", "execution_hash"),
    ("trading_commission_v1", "commissions", "commission_count", "commission_hash"),
    ("trading_order_command_v1", "order_commands", "order_command_count", "order_command_hash"),
    ("trading_order_transition_v1", "order_transitions", "order_transition_count",
     "order_transition_hash"),
    ("trading_account_snapshot_v1", "account_snapshots", "account_snapshot_count",
     "account_snapshot_hash"),
    ("trading_position_snapshot_v1", "position_snapshots", "position_snapshot_count",
     "position_snapshot_hash"),
)
_ZERO_UUID = "00000000-0000-0000-0000-000000000000"
_EVENT_DETAILS = {
    ("run_state", "lifecycle"): None,
    ("strategy_decision", "signal"): "trading_strategy_signal_v1",
    ("execution", "fill"): "trading_execution_v1",
    ("execution", "commission"): "trading_commission_v1",
    ("order_management", "order_command"): "trading_order_command_v1",
    ("order_management", "order_transition"): "trading_order_transition_v1",
    ("snapshot", "portfolio"): "trading_account_snapshot_v1",
    ("snapshot", "position"): "trading_position_snapshot_v1",
}
_COMMIT_COLUMNS = tuple(name for name, _ in _CONTRACTS["trading_commit_v1"].columns
                        if name not in ("run_month", "committed_at"))


class JournalQueueFull(RuntimeError):
    """The bounded persistence lane cannot accept another batch immediately."""


def journal_client_from_env() -> Any:
    """Open the dedicated typed-journal principal, never market credentials."""
    from research.mlops.clickhouse import ClickHouseHttpClient

    url = os.environ.get("TRADING_JOURNAL_CLICKHOUSE_URL", "").strip()
    user = os.environ.get("TRADING_JOURNAL_CLICKHOUSE_USER", "").strip()
    password = os.environ.get("TRADING_JOURNAL_CLICKHOUSE_PASSWORD", "")
    if not url or not user or not password:
        raise ValueError("Typed journal requires dedicated ClickHouse URL, user, and password")
    market_users = {os.environ.get(key, "").strip() for key in (
        "BACKTEST_CLICKHOUSE_USER", "REAL_LIVE_CLICKHOUSE_READ_USER",
        "REAL_LIVE_CLICKHOUSE_USER",
    )}
    if user in market_users:
        raise ValueError("Typed journal principal must differ from market-data readers")
    return ClickHouseHttpClient(
        url, user, password, timeout_seconds=60, persistent=True,
        default_query_params={"max_threads": 2, "max_execution_time": 60},
    )


@dataclass(frozen=True, slots=True)
class TypedJournalBatch:
    run_id: str
    run_month: date
    attempt_id: str
    batch_id: str
    prior_batch_id: str
    first_sequence: int
    last_sequence: int
    source_cursor: str
    status: str
    events: tuple[Mapping[str, Any], ...]
    signals: tuple[Mapping[str, Any], ...] = ()
    signal_sources: tuple[Mapping[str, Any], ...] = ()
    executions: tuple[Mapping[str, Any], ...] = ()
    commissions: tuple[Mapping[str, Any], ...] = ()
    order_commands: tuple[Mapping[str, Any], ...] = ()
    order_transitions: tuple[Mapping[str, Any], ...] = ()
    account_snapshots: tuple[Mapping[str, Any], ...] = ()
    position_snapshots: tuple[Mapping[str, Any], ...] = ()

    def __post_init__(self) -> None:
        if not self.run_id or self.first_sequence < 1 or self.last_sequence < self.first_sequence:
            raise ValueError("Journal batch has invalid run or sequence identity")
        if not self.source_cursor or self.status not in {"running", "completed", "stopped", "failed"}:
            raise ValueError("Journal batch requires a source cursor and valid status")
        for value in (self.attempt_id, self.batch_id, self.prior_batch_id):
            UUID(value)
        # Copy only the small typed row envelopes at submission. Full schema
        # checks, JSON wire serialization, and hashing happen on the writer.
        for _, family, _, _ in _FAMILIES:
            snapshots = []
            for row in getattr(self, family):
                if any(isinstance(value, (Mapping, list, tuple, set, bytearray, memoryview))
                       for value in row.values()):
                    raise ValueError(f"{family} contains mutable or opaque journal data")
                snapshots.append(MappingProxyType(dict(row)))
            object.__setattr__(self, family, tuple(snapshots))

    def families(self) -> tuple[tuple[str, tuple[Mapping[str, Any], ...]], ...]:
        return tuple((name, getattr(self, attribute)) for name, attribute, _, _ in _FAMILIES)


def _sealed_families(batch: TypedJournalBatch) -> tuple[tuple[str, tuple[dict[str, Any], ...]], ...]:
    """Validate and hash the immutable snapshot on the persistence lane."""
    if len(batch.events) != batch.last_sequence - batch.first_sequence + 1:
        raise ValueError("Journal batch must cover a contiguous event sequence")
    sequences = [int(row["sequence"]) for row in batch.events]
    if sequences != list(range(batch.first_sequence, batch.last_sequence + 1)):
        raise ValueError("Journal event sequence is not contiguous")
    event_ids = {str(UUID(str(row["record_id"]))) for row in batch.events}
    if len(event_ids) != len(batch.events):
        raise ValueError("Journal batch repeated an event identity")
    events_by_id = {str(UUID(str(row["record_id"]))): row for row in batch.events}
    result: list[tuple[str, tuple[dict[str, Any], ...]]] = []
    for name, rows in batch.families():
        allowed = {column for column, _ in _CONTRACTS[name].columns}
        identities: set[str] = set()
        sealed: list[dict[str, Any]] = []
        for row in rows:
            if set(row) not in (allowed, allowed - {"content_hash"}):
                raise ValueError(f"{name} has missing or extra typed columns")
            if str(row["run_id"]) != batch.run_id or str(UUID(str(row["batch_id"]))) != batch.batch_id:
                raise ValueError(f"{name} mixed runs or batches")
            record_id = str(UUID(str(row["record_id"])))
            parent_id = (str(UUID(str(row["parent_record_id"])))
                         if name == "trading_signal_source_v1" else record_id)
            if name != "trading_event_v1" and parent_id not in event_ids:
                raise ValueError(f"{name} has no parent journal event")
            if name != "trading_event_v1":
                parent = events_by_id[parent_id]
                if (str(row["event_month"]) != str(parent["event_month"])
                        or (name != "trading_signal_source_v1"
                            and str(row["account_id"]) != str(parent["account_id"]))):
                    raise ValueError(f"{name} differs from its parent event identity")
            if name == "trading_event_v1" and str(UUID(str(row["attempt_id"]))) != batch.attempt_id:
                raise ValueError("Journal event mixed attempts")
            if record_id in identities:
                raise ValueError(f"{name} has duplicate record identities")
            identities.add(record_id)
            content = {key: value for key, value in row.items() if key != "content_hash"}
            if any(isinstance(value, (Mapping, list, tuple, bytearray))
                   for value in content.values()):
                raise ValueError(f"{name} contains an opaque or mutable value")
            canonical = _canonical_typed_content(name, content)
            if (name == "trading_event_v1"
                    and canonical["event_month"] != canonical["event_time"][:7] + "-01"):
                raise ValueError("Journal event partition differs from its UTC event time")
            digest = sha256(canonical_json(canonical).encode("utf-8")).hexdigest()
            if "content_hash" in row and str(row["content_hash"]) != digest:
                raise ValueError(f"{name} has an incorrect content hash")
            sealed.append({**content, "content_hash": digest})
        result.append((name, tuple(sealed)))
    by_family = dict(result)
    account_snapshots = {
        (str(row["account_id"]), str(row["snapshot_id"]))
        for row in by_family["trading_account_snapshot_v1"]
    }
    if len(account_snapshots) != len(by_family["trading_account_snapshot_v1"]):
        raise ValueError("Journal batch repeated an account snapshot identity")
    for row in by_family["trading_position_snapshot_v1"]:
        if (str(row["account_id"]), str(row["snapshot_id"])) not in account_snapshots:
            raise ValueError("Position snapshot lacks its complete account snapshot")
    details_by_record: dict[str, str] = {}
    for name, rows in result:
        if name in {"trading_event_v1", "trading_signal_source_v1"}:
            continue
        for row in rows:
            record_id = str(UUID(str(row["record_id"])))
            if record_id in details_by_record:
                raise ValueError("Journal event has multiple typed detail families")
            details_by_record[record_id] = name
    for event in by_family["trading_event_v1"]:
        key = (str(event["category"]), str(event["entity_type"]))
        if key not in _EVENT_DETAILS:
            raise ValueError(f"Journal event has no typed contract: {key}")
        record_id = str(UUID(str(event["record_id"])))
        if details_by_record.get(record_id) != _EVENT_DETAILS[key]:
            raise ValueError("Journal event lacks its required typed detail")
    sources_by_parent: dict[str, list[dict[str, Any]]] = {}
    for row in by_family["trading_signal_source_v1"]:
        parent_id = str(UUID(str(row["parent_record_id"])))
        if details_by_record.get(parent_id) != "trading_strategy_signal_v1":
            raise ValueError("Signal source lacks a typed strategy signal")
        if not str(row["source_signal_id"]):
            raise ValueError("Signal source identity is empty")
        sources_by_parent.setdefault(parent_id, []).append(row)
    for signal in by_family["trading_strategy_signal_v1"]:
        signal_id = str(UUID(str(signal["record_id"])))
        source_rows = sources_by_parent.get(signal_id, [])
        if (len(source_rows) != int(signal["source_signal_count"])
                or sorted(int(row["source_ordinal"]) for row in source_rows)
                != list(range(len(source_rows)))):
            raise ValueError("Signal sources do not match the typed signal count")
    return tuple(result)


@dataclass(frozen=True, slots=True)
class CommittedPrefix:
    run_id: str
    last_sequence: int
    last_batch_id: str
    source_cursor: str
    status: str
    batch_ids: tuple[str, ...]


def _can_coalesce(left: TypedJournalBatch, right: TypedJournalBatch,
                  max_events: int) -> bool:
    return (
        left.status == "running"
        and left.run_id == right.run_id
        and left.run_month == right.run_month
        and left.attempt_id == right.attempt_id
        and right.prior_batch_id == left.batch_id
        and right.first_sequence == left.last_sequence + 1
        and right.last_sequence - left.first_sequence + 1 <= max_events
    )


def _coalesce_unpublished(batches: tuple[TypedJournalBatch, ...]) -> TypedJournalBatch:
    """Rekey contiguous unpublished microbatches under the final batch ID."""
    if not batches:
        raise ValueError("Cannot coalesce an empty journal batch")
    for prior, current in zip(batches, batches[1:]):
        if not _can_coalesce(prior, current, 2**64 - 1):
            raise ValueError("Journal microbatches are not contiguous")
    if len(batches) == 1:
        return batches[0]
    last = batches[-1]
    families: dict[str, tuple[dict[str, Any], ...]] = {}
    for _, attribute, _, _ in _FAMILIES:
        families[attribute] = tuple(
            {**{key: value for key, value in row.items() if key != "content_hash"},
             "batch_id": last.batch_id}
            for batch in batches for row in getattr(batch, attribute)
        )
    return TypedJournalBatch(
        batches[0].run_id, batches[0].run_month, batches[0].attempt_id,
        last.batch_id, batches[0].prior_batch_id,
        batches[0].first_sequence, last.last_sequence,
        last.source_cursor, last.status,
        **families,
    )


def typed_row(name: str, values: Mapping[str, Any]) -> dict[str, Any]:
    """Hash the persisted typed representation, not source-side spellings."""
    row = dict(values)
    if "content_hash" in row:
        raise ValueError("Caller cannot provide a content hash")
    row["content_hash"] = sha256(
        canonical_json(_canonical_typed_content(name, row)).encode("utf-8")
    ).hexdigest()
    return row


def _literal(value: str) -> str:
    return "'" + value.replace("\\", "\\\\").replace("'", "\\'") + "'"


_ISO_INSTANT = re.compile(
    r"^(\d{4}-\d{2}-\d{2})[T ](\d{2}:\d{2}:\d{2})(?:\.(\d{1,9}))?(Z|[+-]\d{2}:\d{2})$"
)


def _datetime_wire(value: Any, scale: int, *, stored_utc: bool = False) -> str:
    """Render a timezone-aware instant in ClickHouse's lossless UTC format."""
    source = value.isoformat() if isinstance(value, datetime) else str(value)
    if stored_utc:
        if not re.fullmatch(r"\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}\.\d{6}(?:\d{3})?", source):
            raise ValueError("Stored journal timestamp is not a UTC DateTime64 value")
        source += "+00:00"
    match = _ISO_INSTANT.fullmatch(source)
    if match is None:
        raise ValueError("Journal timestamps must be timezone-aware ISO instants")
    fraction = (match.group(3) or "").ljust(9, "0")
    if scale == 6 and fraction[6:] != "000":
        raise ValueError("Submicrosecond timestamp cannot fit DateTime64(6)")
    zone = "+00:00" if match.group(4) == "Z" else match.group(4)
    parsed = datetime.fromisoformat(
        f"{match.group(1)}T{match.group(2)}.{fraction[:6]}{zone}"
    ).astimezone(timezone.utc)
    result = parsed.strftime("%Y-%m-%d %H:%M:%S.%f")
    return result + fraction[6:] if scale == 9 else result


def _canonical_typed_content(
    name: str, row: Mapping[str, Any], *, stored_utc: bool = False,
) -> dict[str, Any]:
    """Canonicalize every persisted field for reproducible row-hash recovery."""
    if name not in _CONTRACTS:
        raise ValueError("Unknown typed journal family")
    expected = {column for column, _ in _CONTRACTS[name].columns} - {"content_hash"}
    if set(row) != expected:
        raise ValueError(f"{name} has missing or extra typed columns")
    canonical: dict[str, Any] = {}
    for column, kind in _CONTRACTS[name].columns:
        if column == "content_hash":
            continue
        value = row[column]
        nullable = kind.startswith("Nullable(")
        if value is None:
            if not nullable:
                raise ValueError(f"{name}.{column} cannot be null")
            canonical[column] = None
            continue
        base = kind[9:-1] if nullable else kind
        if base.startswith("DateTime64(9"):
            canonical[column] = _datetime_wire(value, 9, stored_utc=stored_utc)
        elif base.startswith("DateTime64(6"):
            canonical[column] = _datetime_wire(value, 6, stored_utc=stored_utc)
        elif base.startswith("Decimal("):
            scale = int(base.rsplit(",", 1)[1].rstrip(") "))
            try:
                with localcontext() as context:
                    context.prec = 50
                    number = Decimal(str(value))
                    quantized = number.quantize(Decimal(1).scaleb(-scale))
            except (InvalidOperation, ValueError) as exc:
                raise ValueError(f"{name}.{column} is not a valid decimal") from exc
            if not number.is_finite() or number != quantized:
                raise ValueError(f"{name}.{column} loses decimal precision")
            canonical[column] = format(quantized, f".{scale}f")
        elif base.startswith("UInt"):
            if isinstance(value, bool) or not str(value).isdigit():
                raise ValueError(f"{name}.{column} is not an unsigned integer")
            number = int(value)
            if number >= 1 << int(base[4:]):
                raise ValueError(f"{name}.{column} exceeds its unsigned width")
            canonical[column] = number
        elif base == "UUID":
            canonical[column] = str(UUID(str(value)))
        elif base == "Date":
            canonical[column] = date.fromisoformat(str(value)).isoformat()
        elif base in {"String", "LowCardinality(String)", "FixedString(64)"}:
            if not isinstance(value, str):
                raise ValueError(f"{name}.{column} is not a string")
            canonical[column] = value
        else:
            raise ValueError(f"Unsupported typed journal field {name}.{column}: {kind}")
    return canonical


def _wire_row(name: str, row: Mapping[str, Any]) -> dict[str, Any]:
    content = {key: value for key, value in row.items() if key != "content_hash"}
    result = _canonical_typed_content(name, content)
    if "content_hash" in row:
        result["content_hash"] = str(row["content_hash"])
    return result


def _rows(client: Any, sql: str) -> list[dict[str, Any]]:
    return [json.loads(line) for line in client.execute(sql).splitlines() if line.strip()]


def _identity(rows: tuple[Mapping[str, Any], ...] | list[dict[str, Any]]) -> list[tuple[str, str]]:
    return sorted((str(UUID(str(row["record_id"]))), str(row["content_hash"])) for row in rows)


def _family_identities(client: Any, batch_id: str) -> dict[str, list[tuple[str, str]]]:
    """Read every typed family in one network request, including empty ones."""
    token = f"batch_id=toUUID({_literal(batch_id)})"
    selects = [
        "(SELECT groupArray((toString(record_id),toString(content_hash))) "
        f"FROM arte.{name} WHERE {token}) AS {name}"
        for name, _, _, _ in _FAMILIES
    ]
    response = _rows(client, "SELECT " + ",".join(selects) + " FORMAT JSONEachRow")
    names = {name for name, _, _, _ in _FAMILIES}
    if len(response) != 1 or set(response[0]) != names:
        raise RuntimeError("Typed journal family readback is incomplete")
    return {
        name: sorted((str(UUID(str(record_id))), str(digest))
                     for record_id, digest in response[0][name])
        for name in names
    }


def _insert(client: Any, name: str, rows: tuple[Mapping[str, Any], ...], token: str) -> None:
    if name not in _CONTRACTS:
        raise ValueError("Journal writer cannot insert outside typed journal tables")
    if not rows:
        return
    columns = tuple(column for column, _ in _CONTRACTS[name].columns)
    body = "\n".join(canonical_json(_wire_row(name, row)) for row in rows)
    client.execute(
        f"INSERT INTO arte.{name} ({','.join(columns)}) "
        f"SETTINGS async_insert=1,wait_for_async_insert=1,insert_deduplicate=1,"
        f"insert_deduplication_token={_literal(token)} FORMAT JSONEachRow\n{body}"
    )


def publish_typed_run(client: Any, run: Mapping[str, Any]) -> str:
    """Publish the parent identity; runtime context still needs its own fence."""
    expected_columns = {column for column, _ in _CONTRACTS["trading_run_v1"].columns}
    if set(run) != expected_columns:
        raise ValueError("Typed run has missing or extra columns")
    run_id = str(run["run_id"])
    interval = run["evaluation_interval_ms"]
    if (not run_id or run["mode"] not in {"live", "paper", "replay", "backtest", "integration_test"}
            or (interval is not None and (not isinstance(interval, int)
                                          or interval < 100 or interval % 100))):
        raise ValueError("Typed run identity or evaluation interval is invalid")
    for field in ("configuration_hash", "code_hash"):
        if not re.fullmatch(r"[0-9a-f]{64}", str(run[field])):
            raise ValueError(f"Typed run {field} must be a SHA-256 digest")
    wire = _wire_row("trading_run_v1", run)
    columns = ",".join(column for column, _ in _CONTRACTS["trading_run_v1"].columns)
    query = (f"SELECT {columns} FROM arte.trading_run_v1 "
             f"WHERE run_id={_literal(run_id)} FORMAT JSONEachRow")
    existing = _rows(client, query)
    if existing and (len(existing) != 1 or existing[0] != wire):
        raise RuntimeError("Typed run identity conflicts with existing publication")
    if not existing:
        _insert(client, "trading_run_v1", (run,), f"run:{run_id}")
        if _rows(client, query) != [wire]:
            raise RuntimeError("Typed run identity was not durably published")
    return run_id


_RUN_CONFIG_FIELDS = frozenset({
    "strategy_id", "strategy_revision", "anchor_date", "run_plan_id",
    "safety_supervisor_enabled", "checkpoint_interval_events",
    "write_progress_checkpoints",
})


def publish_typed_run_context(
    client: Any, *, run_id: str, config: Mapping[str, Any],
    account_ids: tuple[str, ...],
) -> None:
    """Publish the normalized RunConfig and account membership, fence last."""
    if set(config) != _RUN_CONFIG_FIELDS:
        raise ValueError("Runtime configuration has missing or extra typed fields")
    if (not str(config["strategy_id"]).strip()
            or int(config["strategy_revision"]) < 0
            or int(config["checkpoint_interval_events"]) < 1
            or any(config[key] not in (0, 1, False, True) for key in (
                "safety_supervisor_enabled", "write_progress_checkpoints"))):
        raise ValueError("Runtime configuration contains invalid values")
    if (not account_ids or len(account_ids) > 65535
            or any(not account.strip() for account in account_ids)
            or len(set(account_ids)) != len(account_ids)):
        raise ValueError("Runtime account membership is invalid")
    parent_columns = ",".join(column for column, _ in _CONTRACTS["trading_run_v1"].columns)
    parent = _rows(client,
        f"SELECT {parent_columns} FROM arte.trading_run_v1 "
        f"WHERE run_id={_literal(run_id)} FORMAT JSONEachRow")
    if len(parent) != 1:
        raise RuntimeError("Typed run parent is missing or duplicated")
    month = str(parent[0]["run_month"])
    run_hash = sha256(canonical_json(_canonical_typed_content(
        "trading_run_v1", parent[0], stored_utc=True)).encode("utf-8")).hexdigest()
    context = typed_row("trading_runtime_config_v1", {
        "run_id": run_id, "run_month": month,
        **{key: (int(value) if key in {"safety_supervisor_enabled",
                                        "write_progress_checkpoints"} else value)
           for key, value in config.items()},
    })
    members = tuple(typed_row("trading_run_account_v1", {
        "run_id": run_id, "run_month": month, "ordinal": ordinal,
        "account_id": account,
    }) for ordinal, account in enumerate(account_ids))
    account_hash = sha256(canonical_json([
        (row["ordinal"], row["content_hash"]) for row in members
    ]).encode("utf-8")).hexdigest()
    fenced = _rows(client,
        "SELECT run_id FROM arte.trading_run_context_commit_v1 "
        f"WHERE run_id={_literal(run_id)} FORMAT JSONEachRow")
    if len(fenced) > 1:
        raise RuntimeError("Typed run context has duplicated commit fences")
    for name, expected_rows in (("trading_runtime_config_v1", (context,)),
                                ("trading_run_account_v1", members)):
        columns = ",".join(column for column, _ in _CONTRACTS[name].columns)
        query = (f"SELECT {columns} FROM arte.{name} "
                 f"WHERE run_id={_literal(run_id)} FORMAT JSONEachRow")
        expected = [_wire_row(name, row) for row in expected_rows]
        actual = _rows(client, query)
        if actual and sorted(actual, key=canonical_json) != sorted(expected, key=canonical_json):
            raise RuntimeError(f"Typed run {name} conflicts with existing publication")
        if not actual:
            if fenced:
                raise RuntimeError("Committed run context has missing typed rows")
            _insert(client, name, expected_rows, f"run-context:{run_id}:{name}")
            actual = _rows(client, query)
        if sorted(actual, key=canonical_json) != sorted(expected, key=canonical_json):
            raise RuntimeError(f"Typed run {name} did not become durable")
    fence = {
        "run_id": run_id, "run_month": month,
        "run_hash": run_hash,
        "config_hash": context["content_hash"],
        "account_count": len(members), "account_hash": account_hash,
        "committed_at": datetime.now(timezone.utc).isoformat(),
    }
    fence_query = (
        "SELECT run_id,run_month,run_hash,config_hash,account_count,account_hash "
        "FROM arte.trading_run_context_commit_v1 "
        f"WHERE run_id={_literal(run_id)} FORMAT JSONEachRow"
    )
    stable = {key: value for key, value in fence.items() if key != "committed_at"}
    existing = _rows(client, fence_query)
    if existing and (len(existing) != 1 or existing[0] != stable):
        raise RuntimeError("Typed run context fence conflicts with existing publication")
    if not existing:
        _insert(client, "trading_run_context_commit_v1", (fence,),
                f"run-context:{run_id}:commit")
        if _rows(client, fence_query) != [stable]:
            raise RuntimeError("Typed run context fence did not become durable")


def load_typed_run_context(client: Any, run_id: str) -> dict[str, Any]:
    """Recover only a fully fenced and hash-verified runtime configuration."""
    parent_columns = ",".join(column for column, _ in _CONTRACTS["trading_run_v1"].columns)
    parents = _rows(client, f"SELECT {parent_columns} FROM arte.trading_run_v1 "
                    f"WHERE run_id={_literal(run_id)} FORMAT JSONEachRow")
    config_columns = ",".join(column for column, _ in
                              _CONTRACTS["trading_runtime_config_v1"].columns)
    account_columns = ",".join(column for column, _ in
                               _CONTRACTS["trading_run_account_v1"].columns)
    config_rows = _rows(client, f"SELECT {config_columns} "
                        "FROM arte.trading_runtime_config_v1 "
                        f"WHERE run_id={_literal(run_id)} FORMAT JSONEachRow")
    accounts = _rows(client, f"SELECT {account_columns} "
                     "FROM arte.trading_run_account_v1 "
                     f"WHERE run_id={_literal(run_id)} FORMAT JSONEachRow")
    fences = _rows(client,
        "SELECT run_id,run_month,run_hash,config_hash,account_count,account_hash "
        "FROM arte.trading_run_context_commit_v1 "
        f"WHERE run_id={_literal(run_id)} FORMAT JSONEachRow")
    if len(parents) != 1 or len(config_rows) != 1 or len(fences) != 1 or not accounts:
        raise RuntimeError("Typed run context has no complete publication fence")
    config = config_rows[0]
    month = str(config["run_month"])
    run_hash = sha256(canonical_json(_canonical_typed_content(
        "trading_run_v1", parents[0], stored_utc=True)).encode("utf-8")).hexdigest()
    if (str(config["run_id"]) != run_id
            or str(parents[0]["run_id"]) != run_id
            or str(parents[0]["run_month"]) != month
            or str(fences[0]["run_id"]) != run_id
            or str(fences[0]["run_month"]) != month):
        raise RuntimeError("Typed run context identity differs from its fence")
    def verified_hash(name: str, row: Mapping[str, Any]) -> str:
        content = {key: value for key, value in row.items() if key != "content_hash"}
        digest = sha256(canonical_json(_canonical_typed_content(name, content))
                        .encode("utf-8")).hexdigest()
        if digest != str(row["content_hash"]):
            raise RuntimeError(f"Typed run {name} row content differs from its hash")
        return digest
    config_hash = verified_hash("trading_runtime_config_v1", config)
    accounts.sort(key=lambda row: int(row["ordinal"]))
    if (len(accounts) > 65535
            or [int(row["ordinal"]) for row in accounts] != list(range(len(accounts)))
            or any(str(row["run_id"]) != run_id or str(row["run_month"]) != month
                   for row in accounts)):
        raise RuntimeError("Typed run account membership is not contiguous")
    account_hash = sha256(canonical_json([
        (int(row["ordinal"]), verified_hash("trading_run_account_v1", row))
        for row in accounts
    ]).encode("utf-8")).hexdigest()
    if (str(fences[0]["run_hash"]) != run_hash
            or str(fences[0]["config_hash"]) != config_hash
            or int(fences[0]["account_count"]) != len(accounts)
            or str(fences[0]["account_hash"]) != account_hash):
        raise RuntimeError("Typed run context differs from its committed fence")
    return {key: config[key] for key in _RUN_CONFIG_FIELDS} | {
        "run_id": run_id, "run_month": month,
        "account_ids": tuple(str(row["account_id"]) for row in accounts),
    }


def _verify_run_identity(client: Any, run_id: str) -> None:
    rows = _rows(client,
        "SELECT run_id FROM arte.trading_run_v1 "
        f"WHERE run_id={_literal(run_id)} FORMAT JSONEachRow")
    if rows != [{"run_id": run_id}]:
        raise RuntimeError("Typed journal run identity is missing or duplicated")
    load_typed_run_context(client, run_id)


def _verify_commission_links(
    client: Any, batch: TypedJournalBatch,
    families: tuple[tuple[str, tuple[dict[str, Any], ...]], ...],
) -> None:
    by_family = dict(families)
    same_batch = {
        (str(row["account_id"]), str(row["execution_id"]))
        for row in by_family["trading_execution_v1"]
    }
    for fee in by_family["trading_commission_v1"]:
        account_id, execution_id = str(fee["account_id"]), str(fee["execution_id"])
        if (account_id, execution_id) in same_batch:
            continue
        candidates = _rows(client,
            "SELECT batch_id,record_id FROM arte.trading_execution_v1 "
            f"WHERE run_id={_literal(batch.run_id)} "
            f"AND account_id={_literal(account_id)} "
            f"AND execution_id={_literal(execution_id)} FORMAT JSONEachRow")
        committed = 0
        for candidate in candidates:
            source_batch = str(UUID(str(candidate["batch_id"])))
            fences = _rows(client,
                "SELECT batch_id FROM arte.trading_commit_v1 "
                f"WHERE run_id={_literal(batch.run_id)} "
                f"AND batch_id=toUUID({_literal(source_batch)}) FORMAT JSONEachRow")
            if len(fences) > 1:
                raise RuntimeError("Commission execution has duplicated commit fences")
            committed += len(fences)
        if committed != 1:
            raise RuntimeError("Commission revision requires one committed execution")


def publish_typed_batch(client: Any, batch: TypedJournalBatch) -> str:
    """Publish and verify one typed batch, with the commit row written last."""
    families = _sealed_families(batch)
    _verify_commission_links(client, batch, families)
    existing = _rows(client,
        f"SELECT {','.join(_COMMIT_COLUMNS)} "
        "FROM arte.trading_commit_v1 "
        f"WHERE batch_id=toUUID({_literal(batch.batch_id)}) FORMAT JSONEachRow")
    if not existing:
        prior = _rows(client,
            "SELECT batch_id,last_sequence FROM arte.trading_commit_v1 "
            f"WHERE run_id={_literal(batch.run_id)} "
            "ORDER BY last_sequence DESC LIMIT 1 FORMAT JSONEachRow")
        expected_prior = ((str(UUID(str(prior[0]["batch_id"]))), int(prior[0]["last_sequence"]))
                          if prior else (_ZERO_UUID, 0))
        if expected_prior != (batch.prior_batch_id, batch.first_sequence - 1):
            raise RuntimeError("Typed journal batch does not extend the committed prefix")
    hashes: dict[str, str] = {}
    actual = _family_identities(client, batch.batch_id)
    inserted = False
    for name, rows in families:
        expected_ids = _identity(rows)
        hashes[name] = sha256(canonical_json(expected_ids).encode("utf-8")).hexdigest()
        if actual[name] and actual[name] != expected_ids:
            raise RuntimeError(f"{name} has a conflicting or duplicated batch")
        if rows and not actual[name]:
            _insert(client, name, rows, f"{batch.batch_id}:{name}")
            inserted = True
    if inserted:
        actual = _family_identities(client, batch.batch_id)
        for name, rows in families:
            if actual[name] != _identity(rows):
                raise RuntimeError(f"{name} did not become durable")
    commit = {
        "run_id": batch.run_id,
        "run_month": batch.run_month.isoformat(),
        "attempt_id": batch.attempt_id,
        "batch_id": batch.batch_id,
        "prior_batch_id": batch.prior_batch_id,
        "first_sequence": batch.first_sequence,
        "last_sequence": batch.last_sequence,
        "source_cursor": batch.source_cursor,
        "status": batch.status,
        "committed_at": datetime.now(timezone.utc).isoformat(),
    }
    for name, attribute, count_column, hash_column in _FAMILIES:
        commit[count_column] = len(getattr(batch, attribute))
        commit[hash_column] = hashes[name]
    expected = {key: value for key, value in commit.items() if key not in ("run_month", "committed_at")}
    if existing and (len(existing) != 1 or existing[0] != expected):
        raise RuntimeError("Typed journal commit conflicts with an existing batch")
    if not existing:
        _insert(client, "trading_commit_v1", (commit,), f"{batch.batch_id}:commit")
        verified = _rows(client,
            f"SELECT {','.join(_COMMIT_COLUMNS)} "
            "FROM arte.trading_commit_v1 "
            f"WHERE batch_id=toUUID({_literal(batch.batch_id)}) FORMAT JSONEachRow")
        if verified != [expected]:
            raise RuntimeError("Typed journal commit was not durably published")
    return batch.batch_id


def load_committed_prefix(client: Any, run_id: str) -> CommittedPrefix | None:
    """Verify the entire contiguous typed prefix; ignore unfenced fact rows."""
    if not run_id:
        raise ValueError("Journal run identity is required")
    commits = _rows(client,
        f"SELECT {','.join(_COMMIT_COLUMNS)} FROM arte.trading_commit_v1 "
        f"WHERE run_id={_literal(run_id)} ORDER BY last_sequence,batch_id FORMAT JSONEachRow")
    if not commits:
        return None
    prior_id = _ZERO_UUID
    prior_sequence = 0
    prior_status = "running"
    batch_ids: list[str] = []
    for commit in commits:
        batch_id = str(UUID(str(commit["batch_id"])))
        if (str(commit["run_id"]) != run_id
                or str(UUID(str(commit["prior_batch_id"]))) != prior_id
                or int(commit["first_sequence"]) != prior_sequence + 1
                or int(commit["last_sequence"]) < int(commit["first_sequence"])
                or int(commit["event_count"]) != (
                    int(commit["last_sequence"]) - int(commit["first_sequence"]) + 1)):
            raise RuntimeError("Typed journal commit chain is not contiguous")
        if commit["status"] not in {"running", "completed", "stopped", "failed"}:
            raise RuntimeError("Typed journal commit has invalid status")
        if batch_ids and prior_status != "running":
            raise RuntimeError("Typed journal continues after terminal status")
        prior_id = batch_id
        prior_sequence = int(commit["last_sequence"])
        prior_status = str(commit["status"])
        batch_ids.append(batch_id)
    chunk: list[dict[str, Any]] = []
    row_budget = 0
    for commit in commits:
        expected_rows = sum(int(commit[count_key]) for _, _, count_key, _ in _FAMILIES)
        if chunk and (len(chunk) >= 32 or row_budget + expected_rows > 50_000):
            _verify_recovery_chunk(client, chunk)
            chunk = []
            row_budget = 0
        chunk.append(commit)
        row_budget += expected_rows
    if chunk:
        _verify_recovery_chunk(client, chunk)
    return CommittedPrefix(run_id, prior_sequence, prior_id,
                           str(commits[-1]["source_cursor"]),
                           str(commits[-1]["status"]), tuple(batch_ids))


def _verify_recovery_chunk(client: Any, commits: list[dict[str, Any]]) -> None:
    batch_ids = tuple(str(UUID(str(commit["batch_id"]))) for commit in commits)
    ids = ",".join(f"toUUID({_literal(batch_id)})" for batch_id in batch_ids)
    actual = {batch_id: {name: [] for name, _, _, _ in _FAMILIES}
              for batch_id in batch_ids}
    for name, _, _, _ in _FAMILIES:
        columns = ",".join(column for column, _ in _CONTRACTS[name].columns)
        rows = _rows(client, f"SELECT {columns} FROM arte.{name} "
                     f"WHERE batch_id IN ({ids}) FORMAT JSONEachRow")
        for row in rows:
            batch_id = str(UUID(str(row["batch_id"])))
            if batch_id not in actual:
                raise RuntimeError("Typed journal recovery returned an unexpected batch")
            content = {key: value for key, value in row.items() if key != "content_hash"}
            digest = sha256(canonical_json(_canonical_typed_content(
                name, content, stored_utc=True)).encode("utf-8")).hexdigest()
            if digest != str(row["content_hash"]):
                raise RuntimeError(f"Typed journal {name} row content differs from its hash")
            actual[batch_id][name].append((str(UUID(str(row["record_id"]))), digest))
    for commit, batch_id in zip(commits, batch_ids):
        for name, _, count_key, hash_key in _FAMILIES:
            rows = sorted(actual[batch_id][name])
            digest = sha256(canonical_json(rows).encode("utf-8")).hexdigest()
            if len(rows) != int(commit[count_key]) or digest != str(commit[hash_key]):
                raise RuntimeError(f"Typed journal {name} differs from committed fence")


class ArteJournalWriter:
    """A bounded, single-owner persistence lane with asynchronous receipts."""

    def __init__(self, client: Any, *, run_id: str, capacity: int = 8,
                 max_events_per_commit: int = 4096) -> None:
        if capacity < 1 or max_events_per_commit < 1:
            raise ValueError("Journal queue capacity and commit bound must be positive")
        # Startup/control-plane validation, before a publication thread exists.
        # Never attempt to create tables or repair misplaced parts here.
        storage_preflight(client)
        journal_permission_preflight(client)
        _verify_run_identity(client, run_id)
        self._client = client
        self._run_id = run_id
        self._max_events_per_commit = max_events_per_commit
        self._queue: Queue[tuple[TypedJournalBatch, Future[str]] | None] = Queue(maxsize=capacity)
        self._error: BaseException | None = None
        self._closed = False
        self._thread = Thread(target=self._run, name="arte-journal-writer", daemon=False)
        self._thread.start()

    def submit(self, batch: TypedJournalBatch) -> Future[str]:
        """Enqueue without waiting; the receipt names the durable combined batch."""
        if self._closed:
            raise RuntimeError("Typed journal writer is closed")
        if self._error is not None:
            raise RuntimeError("Typed journal writer failed") from self._error
        if batch.run_id != self._run_id:
            raise ValueError("Typed journal writer cannot mix runs")
        receipt: Future[str] = Future()
        try:
            self._queue.put_nowait((batch, receipt))
        except Full as exc:
            raise JournalQueueFull("Typed journal queue is full; stop new admission") from exc
        return receipt

    def _run(self) -> None:
        held: tuple[TypedJournalBatch, Future[str]] | None = None
        while True:
            item = held if held is not None else self._queue.get()
            held = None
            if item is None:
                self._queue.task_done()
                return
            group = [item]
            stopping = False
            while True:
                try:
                    following = self._queue.get_nowait()
                except Empty:
                    break
                if following is None:
                    stopping = True
                    break
                if (_can_coalesce(group[-1][0], following[0], self._max_events_per_commit)
                        and following[0].last_sequence - group[0][0].first_sequence + 1
                        <= self._max_events_per_commit):
                    group.append(following)
                else:
                    held = following
                    break
            try:
                if self._error is not None:
                    raise RuntimeError("Typed journal writer failed earlier") from self._error
                batch = _coalesce_unpublished(tuple(row for row, _ in group))
                committed_id = publish_typed_batch(self._client, batch)
                for _, receipt in group:
                    receipt.set_result(committed_id)
            except BaseException as exc:
                self._error = exc
                for _, receipt in group:
                    receipt.set_exception(exc)
            finally:
                for _ in group:
                    self._queue.task_done()
            if stopping:
                self._queue.task_done()
                return

    def close(self) -> None:
        """Drain only from a control-plane shutdown, never a market callback."""
        if self._closed:
            if self._error is not None:
                raise RuntimeError("Typed journal did not drain durably") from self._error
            return
        self._closed = True
        self._queue.put(None)
        self._thread.join()
        close = getattr(self._client, "close", None)
        try:
            if close is not None:
                close()
        finally:
            if self._error is not None:
                raise RuntimeError("Typed journal did not drain durably") from self._error
