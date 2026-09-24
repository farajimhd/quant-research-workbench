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
from hashlib import sha256
import json
import re
from queue import Full, Queue
from threading import Thread
from types import MappingProxyType
from typing import Any, Mapping
from uuid import UUID

from src.trading_runtime.arte_journal_schema import TABLES, storage_preflight
from src.trading_runtime.journal_contract import canonical_json


_CONTRACTS = {table.name: table for table in TABLES}
_FAMILIES = (
    ("trading_event_v1", "events", "event_count", "event_hash"),
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
_COMMIT_COLUMNS = tuple(name for name, _ in _CONTRACTS["trading_commit_v1"].columns
                        if name not in ("run_month", "committed_at"))


class JournalQueueFull(RuntimeError):
    """The bounded persistence lane cannot accept another batch immediately."""


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
            object.__setattr__(self, family, tuple(
                MappingProxyType(dict(row)) for row in getattr(self, family)
            ))

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
            if name != "trading_event_v1" and record_id not in event_ids:
                raise ValueError(f"{name} has no parent journal event")
            if name == "trading_event_v1" and str(UUID(str(row["attempt_id"]))) != batch.attempt_id:
                raise ValueError("Journal event mixed attempts")
            if record_id in identities:
                raise ValueError(f"{name} has duplicate record identities")
            identities.add(record_id)
            content = {key: value for key, value in row.items() if key != "content_hash"}
            if any(isinstance(value, (Mapping, list, tuple, bytearray))
                   for value in content.values()):
                raise ValueError(f"{name} contains an opaque or mutable value")
            digest = sha256(canonical_json(content).encode("utf-8")).hexdigest()
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
    return tuple(result)


@dataclass(frozen=True, slots=True)
class CommittedPrefix:
    run_id: str
    last_sequence: int
    last_batch_id: str
    source_cursor: str
    status: str
    batch_ids: tuple[str, ...]


def typed_row(values: Mapping[str, Any]) -> dict[str, Any]:
    """Seal an explicitly typed family row; JSON is transport only."""
    row = dict(values)
    if "content_hash" in row:
        raise ValueError("Caller cannot provide a content hash")
    row["content_hash"] = sha256(canonical_json(row).encode("utf-8")).hexdigest()
    return row


def _literal(value: str) -> str:
    return "'" + value.replace("\\", "\\\\").replace("'", "\\'") + "'"


_ISO_INSTANT = re.compile(
    r"^(\d{4}-\d{2}-\d{2})[T ](\d{2}:\d{2}:\d{2})(?:\.(\d{1,9}))?(Z|[+-]\d{2}:\d{2})$"
)


def _datetime_wire(value: Any, scale: int) -> str:
    """Render a timezone-aware instant in ClickHouse's lossless UTC format."""
    source = value.isoformat() if isinstance(value, datetime) else str(value)
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


def _wire_row(name: str, row: Mapping[str, Any]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for column, kind in _CONTRACTS[name].columns:
        value = row[column]
        if kind.startswith("DateTime64(9"):
            value = _datetime_wire(value, 9)
        elif kind.startswith("DateTime64(6"):
            value = _datetime_wire(value, 6)
        result[column] = value
    return result


def _rows(client: Any, sql: str) -> list[dict[str, Any]]:
    return [json.loads(line) for line in client.execute(sql).splitlines() if line.strip()]


def _identity(rows: tuple[Mapping[str, Any], ...] | list[dict[str, Any]]) -> list[tuple[str, str]]:
    return sorted((str(UUID(str(row["record_id"]))), str(row["content_hash"])) for row in rows)


def _verify_family(client: Any, name: str, batch_id: str,
                   rows: tuple[Mapping[str, Any], ...]) -> bool:
    actual = _rows(client,
        f"SELECT record_id,content_hash FROM arte.{name} "
        f"WHERE batch_id=toUUID({_literal(batch_id)}) FORMAT JSONEachRow")
    if not actual:
        return not rows
    if _identity(actual) != _identity(rows):
        raise RuntimeError(f"{name} has a conflicting or duplicated batch")
    return True


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
    """Publish one immutable run identity before accepting its journal rows."""
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


def _verify_run_identity(client: Any, run_id: str) -> None:
    rows = _rows(client,
        "SELECT run_id FROM arte.trading_run_v1 "
        f"WHERE run_id={_literal(run_id)} FORMAT JSONEachRow")
    if rows != [{"run_id": run_id}]:
        raise RuntimeError("Typed journal run identity is missing or duplicated")


def publish_typed_batch(client: Any, batch: TypedJournalBatch) -> str:
    """Publish and verify one typed batch, with the commit row written last."""
    families = _sealed_families(batch)
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
    for name, rows in families:
        hashes[name] = sha256(canonical_json(_identity(rows)).encode("utf-8")).hexdigest()
        if not _verify_family(client, name, batch.batch_id, rows):
            _insert(client, name, rows, f"{batch.batch_id}:{name}")
            if not _verify_family(client, name, batch.batch_id, rows):
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
                or int(commit["last_sequence"]) < int(commit["first_sequence"])):
            raise RuntimeError("Typed journal commit chain is not contiguous")
        for name, _, count_key, hash_key in _FAMILIES:
            rows = _rows(client,
                f"SELECT record_id,content_hash FROM arte.{name} "
                f"WHERE batch_id=toUUID({_literal(batch_id)}) FORMAT JSONEachRow")
            digest = sha256(canonical_json(_identity(rows)).encode("utf-8")).hexdigest()
            if len(rows) != int(commit[count_key]) or digest != str(commit[hash_key]):
                raise RuntimeError(f"Typed journal {name} differs from committed fence")
        if commit["status"] not in {"running", "completed", "stopped", "failed"}:
            raise RuntimeError("Typed journal commit has invalid status")
        if batch_ids and prior_status != "running":
            raise RuntimeError("Typed journal continues after terminal status")
        prior_id = batch_id
        prior_sequence = int(commit["last_sequence"])
        prior_status = str(commit["status"])
        batch_ids.append(batch_id)
    return CommittedPrefix(run_id, prior_sequence, prior_id,
                           str(commits[-1]["source_cursor"]),
                           str(commits[-1]["status"]), tuple(batch_ids))


class ArteJournalWriter:
    """A bounded, single-owner persistence lane with asynchronous receipts."""

    def __init__(self, client: Any, *, run_id: str, capacity: int = 8) -> None:
        if capacity < 1:
            raise ValueError("Journal queue capacity must be positive")
        # Startup/control-plane validation, before a publication thread exists.
        # Never attempt to create tables or repair misplaced parts here.
        storage_preflight(client)
        _verify_run_identity(client, run_id)
        self._client = client
        self._run_id = run_id
        self._queue: Queue[tuple[TypedJournalBatch, Future[str]] | None] = Queue(maxsize=capacity)
        self._error: BaseException | None = None
        self._closed = False
        self._thread = Thread(target=self._run, name="arte-journal-writer", daemon=False)
        self._thread.start()

    def submit(self, batch: TypedJournalBatch) -> Future[str]:
        """Never wait for network I/O or free queue space on the caller thread."""
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
        while True:
            item = self._queue.get()
            try:
                if item is None:
                    return
                batch, receipt = item
                if self._error is not None:
                    receipt.set_exception(RuntimeError("Typed journal writer failed earlier"))
                    continue
                try:
                    receipt.set_result(publish_typed_batch(self._client, batch))
                except BaseException as exc:
                    self._error = exc
                    receipt.set_exception(exc)
            finally:
                self._queue.task_done()

    def close(self) -> None:
        """Drain only from a control-plane shutdown, never a market callback."""
        if self._closed:
            return
        self._closed = True
        self._queue.put(None)
        self._thread.join()
        close = getattr(self._client, "close", None)
        if close is not None:
            close()
