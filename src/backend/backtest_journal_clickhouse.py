"""Backtest-only ClickHouse journal publication contract.

The broker never imports this module.  A bounded journal worker owns INSERTs
into these tables only; market products remain SELECT-only.  Schema creation
is an operator action, not an execution or preflight action.
"""
from __future__ import annotations

import asyncio
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass
from datetime import date, datetime, timezone
from hashlib import sha256
import json
import os
from pathlib import Path
from typing import Any, Mapping, Sequence
from uuid import UUID, uuid5

from src.trading_runtime.journal_contract import VERSION, canonical_json, journal_row, payload_hash
from src.trading_runtime.journal_evidence import decode_evidence, encode_evidence


TABLE_PREFIX = "arte.bt_"
STORAGE_POLICY = "live_market_ssd"
TABLES = ("bt_run_v1", "bt_event_v1", "bt_blob_v1", "bt_commit_v1")
WRITABLE_TABLES = frozenset(f"arte.{name}" for name in TABLES)
_BATCH_NAMESPACE = UUID("9c911ad1-61b5-48ba-a858-6bba42e27f70")
_LAYOUT = {
    "bt_run_v1": ("toYYYYMM(run_month)", "run_id"),
    "bt_event_v1": ("toYYYYMM(run_month)", "run_id, attempt_id, sequence, batch_id"),
    "bt_blob_v1": ("cityHash64(sha256) % 16", "sha256"),
    "bt_commit_v1": ("toYYYYMM(run_month)", "run_id, attempt_id, last_sequence, fence_id"),
}
_COLUMNS = {
    "bt_run_v1": ("run_id", "run_month", "contract_version", "definition_hash",
                  "configuration_hash", "market_plan_token", "v7_plan_token",
                  "code_hash", "created_at"),
    "bt_event_v1": ("run_id", "run_month", "attempt_id", "batch_id", "record_id",
                    "sequence", "event_time", "recorded_at", "category",
                    "entity_type", "entity_id", "account_id", "payload_hash",
                    "payload_json"),
    "bt_blob_v1": ("sha256", "kind", "raw_bytes", "payload_json", "created_at"),
    "bt_commit_v1": ("run_id", "run_month", "attempt_id", "fence_id",
                     "prior_fence_id", "first_sequence", "last_sequence", "event_count",
                     "batch_ids", "batch_hash", "checkpoint_hash", "source_cursor",
                     "status", "committed_at", "contract_version"),
}


def journal_clickhouse_client() -> Any:
    """Open the separately provisioned journal writer, never market-data credentials.

    The database account must be granted SELECT/INSERT only on the four
    ``arte.bt_*`` tables. Backtest does not create tables or grant privileges.
    """
    from research.mlops.clickhouse import ClickHouseHttpClient

    url = os.environ.get("BACKTEST_JOURNAL_CLICKHOUSE_URL", "").strip()
    user = os.environ.get("BACKTEST_JOURNAL_CLICKHOUSE_USER", "").strip()
    password = os.environ.get("BACKTEST_JOURNAL_CLICKHOUSE_PASSWORD", "")
    market_user = os.environ.get("BACKTEST_CLICKHOUSE_USER", "").strip()
    if not url or not user or not market_user:
        raise ValueError("Backtest requires dedicated journal and market-data credentials")
    if user == market_user:
        raise ValueError("Backtest journal writer must not share the market-data reader account")
    return ClickHouseHttpClient(
        url, user, password, timeout_seconds=60, persistent=True,
        default_query_params={"max_threads": 2, "max_execution_time": 60},
    )


def backtest_code_hash(root: Path) -> str:
    """Fingerprint the deployed Python source, including research dependencies.

    This uses source files rather than a Git checkout because workstation code
    synchronization does not require a ``.git`` directory at execution time.
    """
    digest = sha256()
    count = 0
    for directory in ("src", "research"):
        source = root / directory
        if not source.is_dir():
            raise ValueError(f"Backtest code identity lacks {directory} source")
        for path in sorted(source.rglob("*.py")):
            if not path.is_file() or path.is_symlink():
                raise ValueError(f"Backtest code identity contains an invalid source: {path}")
            relative = path.relative_to(root).as_posix().encode("utf-8")
            digest.update(len(relative).to_bytes(4, "big"))
            digest.update(relative)
            with path.open("rb") as source_file:
                while chunk := source_file.read(1024 * 1024):
                    digest.update(chunk)
            count += 1
    if not count:
        raise ValueError("Backtest code identity has no source files")
    return digest.hexdigest()


def schema_ddl() -> tuple[str, ...]:
    """DDL for the separately authorized schema owner; never called by Backtest."""
    return (
        """CREATE TABLE IF NOT EXISTS arte.bt_run_v1 (
            run_id UUID, run_month Date, contract_version LowCardinality(String),
            definition_hash FixedString(64),
            configuration_hash FixedString(64), market_plan_token String,
            v7_plan_token String, code_hash FixedString(64),
            created_at DateTime64(6, 'UTC'))
            ENGINE=MergeTree PARTITION BY toYYYYMM(run_month)
            ORDER BY (run_id) SETTINGS storage_policy='live_market_ssd'""",
        """CREATE TABLE IF NOT EXISTS arte.bt_event_v1 (
            run_id UUID, run_month Date, attempt_id UUID, batch_id UUID,
            record_id UUID, sequence UInt64, event_time DateTime64(9, 'UTC'),
            recorded_at DateTime64(6, 'UTC'), category LowCardinality(String),
            entity_type LowCardinality(String), entity_id String, account_id String,
            payload_hash FixedString(64), payload_json String CODEC(ZSTD(3)))
            ENGINE=MergeTree PARTITION BY toYYYYMM(run_month)
            ORDER BY (run_id, attempt_id, sequence, batch_id)
            SETTINGS storage_policy='live_market_ssd'""",
        """CREATE TABLE IF NOT EXISTS arte.bt_blob_v1 (
            sha256 FixedString(64), kind LowCardinality(String), raw_bytes UInt64,
            payload_json String CODEC(ZSTD(3)), created_at DateTime64(6, 'UTC'))
            ENGINE=MergeTree PARTITION BY cityHash64(sha256) % 16
            ORDER BY (sha256) SETTINGS storage_policy='live_market_ssd'""",
        """CREATE TABLE IF NOT EXISTS arte.bt_commit_v1 (
            run_id UUID, run_month Date, attempt_id UUID, fence_id UUID,
            prior_fence_id UUID,
            first_sequence UInt64, last_sequence UInt64, event_count UInt32,
            batch_ids Array(UUID),
            batch_hash FixedString(64), checkpoint_hash FixedString(64),
            source_cursor String, status LowCardinality(String),
            committed_at DateTime64(6, 'UTC'), contract_version LowCardinality(String))
            ENGINE=MergeTree PARTITION BY toYYYYMM(run_month)
            ORDER BY (run_id, attempt_id, last_sequence, fence_id)
            SETTINGS storage_policy='live_market_ssd'""",
    )


def storage_preflight(client: Any) -> None:
    """Read-only policy, schema-presence, and actual-part placement check."""
    policies = _rows(client,
        "SELECT disks FROM system.storage_policies "
        "WHERE policy_name='live_market_ssd' FORMAT JSONEachRow")
    if not policies or any(row.get("disks") != [STORAGE_POLICY] for row in policies):
        raise ValueError("Backtest journal requires an SSD-only live_market_ssd policy")
    names = ",".join(_literal(name) for name in TABLES)
    tables = _rows(client,
        "SELECT name,storage_policy,engine,partition_key,sorting_key "
        "FROM system.tables WHERE database='arte' "
        f"AND name IN ({names}) FORMAT JSONEachRow")
    if ({row["name"] for row in tables} != set(TABLES)
            or any(row["storage_policy"] != STORAGE_POLICY for row in tables)):
        raise ValueError("Backtest journal tables are missing or have the wrong storage policy")
    if any((row["engine"], row["partition_key"], row["sorting_key"]) !=
           ("MergeTree", *_LAYOUT[row["name"]]) for row in tables):
        raise ValueError("Backtest journal table partition/order contract differs")
    columns = _rows(client,
        "SELECT table,name,type FROM system.columns WHERE database='arte' "
        f"AND table IN ({names}) ORDER BY table,position FORMAT JSONEachRow")
    actual = {table: tuple(row["name"] for row in columns if row["table"] == table)
              for table in TABLES}
    if actual != _COLUMNS:
        raise ValueError("Backtest journal columns differ from the typed contract")
    types = {(row["table"], row["name"]): row["type"] for row in columns}
    required_types = {
        ("bt_event_v1", "event_time"): "DateTime64(9, 'UTC')",
        ("bt_event_v1", "sequence"): "UInt64",
        ("bt_event_v1", "payload_hash"): "FixedString(64)",
        ("bt_blob_v1", "sha256"): "FixedString(64)",
        ("bt_commit_v1", "batch_ids"): "Array(UUID)",
        ("bt_commit_v1", "checkpoint_hash"): "FixedString(64)",
    }
    if any(types.get(key) != expected for key, expected in required_types.items()):
        raise ValueError("Backtest journal key, timestamp, or hash types differ")
    parts = _rows(client,
        "SELECT table,disk_name FROM system.parts WHERE active AND database='arte' "
        f"AND table IN ({names}) AND disk_name!='live_market_ssd' LIMIT 1 FORMAT JSONEachRow")
    if parts:
        raise ValueError("Backtest journal has active parts outside live_market_ssd")


@dataclass(frozen=True, slots=True)
class JournalBatch:
    run_id: str
    run_month: str
    attempt_id: str
    batch_id: str
    first_sequence: int
    last_sequence: int
    batch_hash: str
    blobs: tuple[dict[str, Any], ...]
    events: tuple[dict[str, Any], ...]


@dataclass(frozen=True, slots=True)
class JournalFence:
    run_id: str
    attempt_id: str
    fence_id: str
    checkpoint_hash: str
    batches: tuple[JournalBatch, ...]
    blobs: tuple[dict[str, Any], ...]
    commit: dict[str, Any]


def prepare_batch(*, records: Sequence[Any], attempt_id: str,
                  run_date: date) -> JournalBatch:
    """Freeze one bounded ordered event batch, without a costly checkpoint."""
    if not records:
        raise ValueError("A journal batch needs events")
    run_id = str(UUID(str(records[0].run_id)))
    attempt = str(UUID(str(attempt_id)))
    sequences = [int(record.sequence) for record in records]
    if (any(str(UUID(str(record.run_id))) != run_id for record in records)
            or sequences != list(range(sequences[0], sequences[-1] + 1))
            or sequences[0] < 1):
        raise ValueError("Journal batch must have one run and contiguous sequences")
    run_month = run_date.replace(day=1).isoformat()
    now = datetime.now(timezone.utc).isoformat()
    evidence: dict[str, str] = {}
    events: list[dict[str, Any]] = []
    identities: list[tuple[int, str, str]] = []
    for record in records:
        row = journal_row(record)
        raw_hash = payload_hash(record)
        encoded = encode_evidence(record.payload, canonical_json, evidence)
        row["payload_json"] = canonical_json(encoded)
        row.update(run_month=run_month, attempt_id=attempt,
                   payload_hash=raw_hash)
        events.append(row)
        identities.append((int(record.sequence), str(UUID(str(record.record_id))), raw_hash))
    batch_hash = sha256(canonical_json(identities).encode("utf-8")).hexdigest()
    batch_id = str(uuid5(_BATCH_NAMESPACE,
                         f"{run_id}:{attempt}:{sequences[0]}:{sequences[-1]}:{batch_hash}"))
    for row in events:
        row["batch_id"] = batch_id
    blobs = tuple({"sha256": digest, "kind": "evidence",
                   "raw_bytes": len(raw.encode("utf-8")), "payload_json": raw,
                   "created_at": now}
                  for digest, raw in sorted(evidence.items()))
    return JournalBatch(run_id, run_month, attempt, batch_id, sequences[0],
                        sequences[-1], batch_hash, blobs, tuple(events))


def prepare_fence(*, batches: Sequence[JournalBatch], checkpoint: dict[str, Any],
                  source_cursor: str, status: str = "running",
                  prior_last_sequence: int = 0,
                  prior_fence_id: str = "00000000-0000-0000-0000-000000000000",
                  additional_evidence: Mapping[str, str] | None = None) -> JournalFence:
    """Commit already-staged contiguous batches with one recovery snapshot."""
    if not batches or status not in {"running", "completed", "stopped", "failed"}:
        raise ValueError("A journal fence needs batches and a valid run status")
    if not isinstance(checkpoint, dict) or not checkpoint or not source_cursor:
        raise ValueError("Journal fence requires recovery state and a source cursor")
    first = batches[0]
    if any(batch.run_id != first.run_id or batch.attempt_id != first.attempt_id
           or batch.run_month != first.run_month for batch in batches):
        raise ValueError("Journal fence cannot mix runs or attempts")
    if any(left.last_sequence + 1 != right.first_sequence
           for left, right in zip(batches, batches[1:])):
        raise ValueError("Journal fence batches must be contiguous")
    prior = str(UUID(prior_fence_id))
    if (first.first_sequence != prior_last_sequence + 1
            or (prior_last_sequence == 0) != (UUID(prior).int == 0)):
        raise ValueError("Journal fence does not extend its committed predecessor")
    evidence: dict[str, str] = {}
    encoded = encode_evidence(checkpoint, canonical_json, evidence)
    body = canonical_json(encoded)
    checkpoint_hash = sha256(body.encode("utf-8")).hexdigest()
    evidence[checkpoint_hash] = body
    for digest, raw in (additional_evidence or {}).items():
        if sha256(raw.encode("utf-8")).hexdigest() != digest:
            raise ValueError("Backtest journal evidence hash does not match its contents")
        existing = evidence.setdefault(digest, raw)
        if existing != raw:
            raise ValueError("Conflicting Backtest journal evidence contents")
    now = datetime.now(timezone.utc).isoformat()
    blobs = tuple({"sha256": digest, "kind": "checkpoint" if digest == checkpoint_hash else "evidence",
                   "raw_bytes": len(raw.encode("utf-8")), "payload_json": raw,
                   "created_at": now} for digest, raw in sorted(evidence.items()))
    batch_hash = sha256(canonical_json([(batch.batch_id, batch.batch_hash)
                                         for batch in batches]).encode("utf-8")).hexdigest()
    fence_id = str(uuid5(_BATCH_NAMESPACE,
                         f"{first.run_id}:{first.attempt_id}:{batches[-1].last_sequence}:"
                         f"{prior}:{batch_hash}:{checkpoint_hash}"))
    commit = {
        "run_id": first.run_id, "run_month": first.run_month,
        "attempt_id": first.attempt_id, "fence_id": fence_id,
        "prior_fence_id": prior,
        "first_sequence": first.first_sequence,
        "last_sequence": batches[-1].last_sequence,
        "event_count": sum(len(batch.events) for batch in batches),
        "batch_ids": [batch.batch_id for batch in batches],
        "batch_hash": batch_hash, "checkpoint_hash": checkpoint_hash,
        "source_cursor": source_cursor, "status": status,
        "committed_at": now, "contract_version": VERSION,
    }
    return JournalFence(first.run_id, first.attempt_id, fence_id,
                        checkpoint_hash, tuple(batches), blobs, commit)


def _literal(value: str) -> str:
    return "'" + value.replace("\\", "\\\\").replace("'", "\\'") + "'"


def _insert(client: Any, table: str, rows: Sequence[dict[str, Any]], token: str) -> None:
    if table not in WRITABLE_TABLES:
        raise ValueError("Backtest journal cannot insert outside its four journal tables")
    if not rows:
        return
    body = "\n".join(canonical_json(row) for row in rows)
    client.execute(
        f"INSERT INTO {table} SETTINGS async_insert=1,wait_for_async_insert=1,"
        f"insert_deduplicate=1,insert_deduplication_token={_literal(token)} "
        f"FORMAT JSONEachRow\n{body}"
    )


def _rows(client: Any, sql: str) -> list[dict[str, Any]]:
    return [json.loads(line) for line in client.execute(sql).splitlines() if line.strip()]


def publish_run(client: Any, *, run_id: str, run_date: date,
                definition: dict[str, Any], configuration_hash: str,
                market_plan_token: str, v7_plan_token: str, code_hash: str) -> str:
    """Publish immutable run identity after its content-addressed definition."""
    normalized = str(UUID(run_id))
    if any(len(value) != 64 for value in (configuration_hash, code_hash)):
        raise ValueError("Run configuration and code require SHA-256 identities")
    body = canonical_json(definition)
    identity = sha256(body.encode("utf-8")).hexdigest()
    created = datetime.now(timezone.utc).isoformat()
    _insert(client, "arte.bt_blob_v1", ({
        "sha256": identity, "kind": "definition", "raw_bytes": len(body.encode("utf-8")),
        "payload_json": body, "created_at": created,
    },), f"{normalized}:definition")
    rows = _rows(client,
        "SELECT definition_hash,configuration_hash,"
        "market_plan_token,v7_plan_token,code_hash,contract_version "
        f"FROM arte.bt_run_v1 WHERE run_id=toUUID({_literal(normalized)}) "
        "FORMAT JSONEachRow")
    expected = (identity, configuration_hash, market_plan_token,
                v7_plan_token, code_hash, VERSION)
    for row in rows:
        actual = tuple(str(row[field]) for field in (
            "definition_hash", "configuration_hash",
            "market_plan_token", "v7_plan_token", "code_hash", "contract_version"))
        if actual != expected:
            raise ValueError("Backtest run identity conflicts with an existing publication")
    if not rows:
        _insert(client, "arte.bt_run_v1", ({
            "run_id": normalized, "run_month": run_date.replace(day=1).isoformat(),
            "contract_version": VERSION, "definition_hash": identity,
            "configuration_hash": configuration_hash,
            "market_plan_token": market_plan_token, "v7_plan_token": v7_plan_token,
            "code_hash": code_hash, "created_at": created,
        },), f"{normalized}:run")
    confirmed = _rows(client,
        "SELECT definition_hash,configuration_hash,"
        "market_plan_token,v7_plan_token,code_hash,contract_version "
        f"FROM arte.bt_run_v1 WHERE run_id=toUUID({_literal(normalized)}) "
        "FORMAT JSONEachRow")
    if not confirmed or any(tuple(str(row[field]) for field in (
            "definition_hash", "configuration_hash",
            "market_plan_token", "v7_plan_token", "code_hash", "contract_version"))
            != expected for row in confirmed):
        raise ValueError("Backtest run identity was not durably published")
    return identity


def _verify_events(client: Any, batch: JournalBatch) -> None:
    rows = _rows(client,
        "SELECT sequence,toString(record_id) AS record_id,"
        "toString(payload_hash) AS payload_hash FROM arte.bt_event_v1 "
        f"WHERE run_id=toUUID({_literal(batch.run_id)}) "
        f"AND attempt_id=toUUID({_literal(batch.attempt_id)}) "
        f"AND batch_id=toUUID({_literal(batch.batch_id)}) "
        "ORDER BY sequence FORMAT JSONEachRow")
    actual: dict[int, tuple[str, str]] = {}
    for row in rows:
        sequence = int(row["sequence"])
        identity = (str(UUID(str(row["record_id"]))), str(row["payload_hash"]))
        if sequence in actual and actual[sequence] != identity:
            raise ValueError("Conflicting persisted Backtest journal event")
        actual[sequence] = identity
    expected = {int(row["sequence"]): (str(UUID(str(row["record_id"]))),
                                      str(row["payload_hash"])) for row in batch.events}
    if actual != expected:
        raise ValueError("Backtest journal batch was not durably published")


def publish_batch(client: Any, batch: JournalBatch) -> str:
    """Stage an acknowledged batch; it is not reviewable until fenced."""
    _insert(client, "arte.bt_blob_v1", batch.blobs, f"{batch.batch_id}:blobs")
    _insert(client, "arte.bt_event_v1", batch.events, f"{batch.batch_id}:events")
    _verify_events(client, batch)
    return batch.batch_id


def publish_fence(client: Any, fence: JournalFence) -> str:
    """Make already-staged batches durable for recovery after checkpoint ACK."""
    prior = str(fence.commit["prior_fence_id"])
    if UUID(prior).int:
        rows = _rows(client,
            "SELECT last_sequence FROM arte.bt_commit_v1 "
            f"WHERE run_id=toUUID({_literal(fence.run_id)}) "
            f"AND fence_id=toUUID({_literal(prior)}) FORMAT JSONEachRow")
        if not rows or any(int(row["last_sequence"]) !=
                           int(fence.commit["first_sequence"]) - 1 for row in rows):
            raise ValueError("Backtest journal predecessor fence is unavailable")
    for batch in fence.batches:
        _verify_events(client, batch)
    _insert(client, "arte.bt_blob_v1", fence.blobs, f"{fence.fence_id}:blobs")
    _insert(client, "arte.bt_commit_v1", (fence.commit,), f"{fence.fence_id}:commit")
    rows = _rows(client,
        "SELECT batch_hash,checkpoint_hash,first_sequence,last_sequence,event_count "
        "FROM arte.bt_commit_v1 "
        f"WHERE run_id=toUUID({_literal(fence.run_id)}) "
        f"AND attempt_id=toUUID({_literal(fence.attempt_id)}) "
        f"AND fence_id=toUUID({_literal(fence.fence_id)}) FORMAT JSONEachRow")
    expected = (fence.commit["batch_hash"], fence.checkpoint_hash,
                fence.commit["first_sequence"], fence.commit["last_sequence"],
                fence.commit["event_count"])
    if not rows or any((str(row["batch_hash"]), str(row["checkpoint_hash"]),
                        int(row["first_sequence"]), int(row["last_sequence"]),
                        int(row["event_count"])) != expected for row in rows):
        raise ValueError("Backtest journal commit fence was not confirmed")
    return fence.fence_id


def _fence_event_proof(client: Any, fence: dict[str, Any]) -> None:
    rows = _rows(client,
        "SELECT toString(batch_id) AS batch_id,sequence,toString(record_id) AS record_id,"
        "toString(payload_hash) AS payload_hash FROM arte.bt_event_v1 "
        f"WHERE run_id=toUUID({_literal(str(fence['run_id']))}) "
        f"AND attempt_id=toUUID({_literal(str(fence['attempt_id']))}) "
        f"AND sequence BETWEEN {int(fence['first_sequence'])} AND {int(fence['last_sequence'])} "
        "ORDER BY sequence FORMAT JSONEachRow")
    by_batch: dict[str, dict[int, tuple[str, str]]] = {}
    for row in rows:
        batch_id = str(UUID(str(row["batch_id"])))
        sequence = int(row["sequence"])
        identity = (str(UUID(str(row["record_id"]))), str(row["payload_hash"]))
        saved = by_batch.setdefault(batch_id, {})
        if sequence in saved and saved[sequence] != identity:
            raise ValueError("Conflicting Backtest journal event on recovery")
        saved[sequence] = identity
    ordered_ids = [str(UUID(str(value))) for value in fence["batch_ids"]]
    if set(by_batch) != set(ordered_ids) or len(ordered_ids) != len(set(ordered_ids)):
        raise ValueError("Backtest journal recovery batch population changed")
    identities: list[tuple[str, str]] = []
    sequences: list[int] = []
    for batch_id in ordered_ids:
        entries = by_batch[batch_id]
        values = [(sequence, *entries[sequence]) for sequence in sorted(entries)]
        identities.append((batch_id, sha256(canonical_json(values).encode("utf-8")).hexdigest()))
        sequences.extend(sequence for sequence, _, _ in values)
    if (sequences != list(range(int(fence["first_sequence"]),
                                int(fence["last_sequence"]) + 1))
            or len(sequences) != int(fence["event_count"])
            or sha256(canonical_json(identities).encode("utf-8")).hexdigest()
            != str(fence["batch_hash"])):
        raise ValueError("Backtest journal recovery event range failed integrity")


def load_fenced_checkpoint(client: Any, run_id: str) -> dict[str, Any] | None:
    """Read only a complete, hash-verified checkpoint chain; ignore staged tail."""
    normalized = str(UUID(run_id))
    rows = _rows(client,
        "SELECT run_id,attempt_id,fence_id,prior_fence_id,first_sequence,"
        "last_sequence,event_count,batch_ids,batch_hash,checkpoint_hash,"
        "source_cursor,status,contract_version "
        f"FROM arte.bt_commit_v1 WHERE run_id=toUUID({_literal(normalized)}) "
        "ORDER BY last_sequence FORMAT JSONEachRow")
    if not rows:
        return None
    fences: dict[str, dict[str, Any]] = {}
    for row in rows:
        if str(row["contract_version"]) != VERSION:
            raise ValueError("Unsupported Backtest journal contract version")
        fence_id = str(UUID(str(row["fence_id"])))
        existing = fences.get(fence_id)
        logical = {key: value for key, value in row.items() if key != "committed_at"}
        if existing is not None and existing != logical:
            raise ValueError("Conflicting Backtest journal fence identity")
        fences[fence_id] = logical
    heads = sorted(fences.values(), key=lambda row: int(row["last_sequence"]), reverse=True)
    latest = heads[0]
    if len(heads) > 1 and int(heads[1]["last_sequence"]) == int(latest["last_sequence"]):
        raise ValueError("Ambiguous Backtest journal checkpoint head")
    chain: list[dict[str, Any]] = []
    current = latest
    seen: set[str] = set()
    while True:
        identity = str(UUID(str(current["fence_id"])))
        if identity in seen:
            raise ValueError("Cyclic Backtest journal checkpoint chain")
        seen.add(identity)
        chain.append(current)
        prior = str(UUID(str(current["prior_fence_id"])))
        if UUID(prior).int == 0:
            if int(current["first_sequence"]) != 1:
                raise ValueError("Backtest journal checkpoint chain lacks its opening range")
            break
        previous = fences.get(prior)
        if previous is None or int(previous["last_sequence"]) + 1 != int(current["first_sequence"]):
            raise ValueError("Backtest journal checkpoint predecessor changed")
        current = previous
    if len(chain) != len(fences):
        raise ValueError("Backtest journal has a divergent committed branch")
    # A valid head hash does not prove earlier committed event ranges.  Every
    # predecessor is part of the same recovery authority and must be checked.
    for committed in reversed(chain):
        _fence_event_proof(client, committed)
    cache: dict[str, str] = {}
    def fetch(digest: str) -> str | None:
        if digest in cache:
            return cache[digest]
        candidates = _rows(client,
            "SELECT payload_json FROM arte.bt_blob_v1 "
            f"WHERE sha256={_literal(digest)} FORMAT JSONEachRow")
        if not candidates:
            return None
        values = {str(row["payload_json"]) for row in candidates}
        if len(values) != 1:
            raise ValueError("Conflicting Backtest journal evidence blob")
        raw = values.pop()
        if sha256(raw.encode("utf-8")).hexdigest() != digest:
            raise ValueError("Corrupt Backtest journal evidence blob")
        cache[digest] = raw
        return raw
    root_hash = str(latest["checkpoint_hash"])
    raw = fetch(root_hash)
    if raw is None:
        raise ValueError("Backtest journal checkpoint blob is missing")
    state = decode_evidence(json.loads(raw), fetch)
    committed_batches = tuple(
        str(UUID(str(batch_id)))
        for committed in reversed(chain)
        for batch_id in committed["batch_ids"]
    )
    if len(committed_batches) != len(set(committed_batches)):
        raise ValueError("Backtest journal committed a batch more than once")
    return {"run_id": normalized, "fence_id": str(latest["fence_id"]),
            "sequence": int(latest["last_sequence"]),
            "batch_ids": committed_batches,
            "source_cursor": str(latest["source_cursor"]),
            "status": str(latest["status"]), "state": state}


class BacktestJournalWriter:
    """Bounded single-owner nonblocking transport; fences wait for durability."""

    def __init__(self, client: Any, *, pending_batches: int = 3) -> None:
        if pending_batches < 1:
            raise ValueError("Journal pending_batches must be positive")
        self.client = client
        self._slots = asyncio.Semaphore(pending_batches)
        self._pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="backtest-journal")
        self._error: BaseException | None = None
        self._closed = False

    def _stage(self, batch: JournalBatch) -> str:
        try:
            if self._error is not None:
                raise RuntimeError("Backtest journal writer is poisoned") from self._error
            return publish_batch(self.client, batch)
        except BaseException as exc:
            self._error = exc
            raise

    async def stage(self, batch: JournalBatch) -> Future[str]:
        """Queue a batch without blocking the event loop on ClickHouse I/O."""
        if self._closed:
            raise RuntimeError("Backtest journal writer is closed")
        await self._slots.acquire()
        try:
            if self._error is not None:
                raise RuntimeError("Backtest journal writer is poisoned") from self._error
            loop = asyncio.get_running_loop()
            future = self._pool.submit(self._stage, batch)
            future.add_done_callback(lambda _done: loop.call_soon_threadsafe(self._slots.release))
            return future
        except BaseException:
            self._slots.release()
            raise

    async def fence(self, fence: JournalFence) -> str:
        """Wait at a safe checkpoint boundary, then publish the commit fence."""
        if self._closed:
            raise RuntimeError("Backtest journal writer is closed")
        def commit() -> str:
            if self._error is not None:
                raise RuntimeError("Backtest journal writer is poisoned") from self._error
            try:
                return publish_fence(self.client, fence)
            except BaseException as exc:
                self._error = exc
                raise
        return await asyncio.wrap_future(self._pool.submit(commit))

    async def close(self) -> None:
        self._closed = True
        await asyncio.to_thread(self._pool.shutdown, True, cancel_futures=False)
        close = getattr(self.client, "close", None)
        if close is not None:
            await asyncio.to_thread(close)
