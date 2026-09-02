from __future__ import annotations

import json
import sqlite3
import threading
import uuid
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable

from src.request_context import causal_identity, current_request_identity


@dataclass(frozen=True, slots=True)
class JournalRecord:
    record_id: str
    run_id: str
    sequence: int
    event_time: datetime
    recorded_at: datetime
    category: str
    entity_type: str
    entity_id: str
    account_id: str
    payload: dict[str, Any]


class TradingJournal:
    """Crash-safe command/event journal and ClickHouse outbox.

    ClickHouse is the durable analytics/audit destination, but SQLite WAL owns
    the local transactional boundary needed to recover order commands without
    repeating or losing them after a process crash.
    """

    def __init__(
        self,
        path: Path,
        *,
        read_only: bool = False,
        synchronous: str = "FULL",
    ) -> None:
        self.path = path
        self.read_only = read_only
        self.synchronous = str(synchronous or "FULL").strip().upper()
        if self.synchronous not in {"FULL", "NORMAL"}:
            raise ValueError("SQLite synchronous mode must be FULL or NORMAL")
        if read_only:
            if not path.is_file():
                raise FileNotFoundError(path)
            self._connection = sqlite3.connect(
                f"file:{path.as_posix()}?mode=ro",
                uri=True,
                check_same_thread=False,
            )
            self._connection.execute("PRAGMA query_only=ON")
        else:
            path.parent.mkdir(parents=True, exist_ok=True)
            self._connection = sqlite3.connect(path, check_same_thread=False)
        self._connection.row_factory = sqlite3.Row
        self._lock = threading.RLock()
        if not read_only:
            self._initialize()

    def close(self) -> None:
        with self._lock:
            self._connection.close()

    def _fetchone(
        self, query: str, parameters: Iterable[Any] = ()
    ) -> sqlite3.Row | None:
        """Fetch one row without overlapping another use of the shared connection."""

        with self._lock:
            return self._connection.execute(query, tuple(parameters)).fetchone()

    def _fetchall(
        self, query: str, parameters: Iterable[Any] = ()
    ) -> list[sqlite3.Row]:
        """Fetch rows under the connection lock and decode them after releasing it."""

        with self._lock:
            return self._connection.execute(query, tuple(parameters)).fetchall()

    def append(
        self,
        *,
        run_id: str,
        category: str,
        entity_type: str,
        entity_id: str,
        payload: dict[str, Any],
        account_id: str = "",
        event_time: datetime | None = None,
    ) -> JournalRecord:
        return self.append_many(
            [
                {
                    "run_id": run_id,
                    "category": category,
                    "entity_type": entity_type,
                    "entity_id": entity_id,
                    "payload": payload,
                    "account_id": account_id,
                    "event_time": event_time,
                }
            ]
        )[0]

    def append_many(self, entries: Iterable[dict[str, Any]]) -> list[JournalRecord]:
        """Append an ordered event batch in one durable SQLite transaction."""

        prepared: list[dict[str, Any]] = []
        for entry in entries:
            run_id = str(entry["run_id"])
            category = str(entry["category"])
            entity_type = str(entry["entity_type"])
            entity_id = str(entry["entity_id"])
            account_id = str(entry.get("account_id") or "")
            event_time = (
                entry.get("event_time") or datetime.now(timezone.utc)
            ).astimezone(timezone.utc)
            recorded_at = datetime.now(timezone.utc)
            record_id = str(uuid.uuid4())
            payload = dict(entry.get("payload") or {})
            active_lineage = current_request_identity()
            needs_generic_lineage = not payload.get("intent_id") or bool(active_lineage)
            lineage = (
                causal_identity(
                    correlation_seed=run_id,
                    causation_seed=(
                        f"{category}:{entity_type}:{entity_id}:"
                        f"{event_time.isoformat(timespec='microseconds')}"
                    ),
                )
                if needs_generic_lineage
                else {}
            )
            payload = {**lineage, **payload}
            prepared.append(
                {
                    "record_id": record_id,
                    "run_id": run_id,
                    "category": category,
                    "entity_type": entity_type,
                    "entity_id": entity_id,
                    "account_id": account_id,
                    "event_time": event_time,
                    "recorded_at": recorded_at,
                    "payload": payload,
                    "payload_json": json.dumps(
                        payload,
                        separators=(",", ":"),
                        sort_keys=True,
                        default=_json_default,
                    ),
                }
            )
        if not prepared:
            return []

        records: list[JournalRecord] = []
        with self._lock, self._connection:
            next_sequence: dict[str, int] = {}
            for entry in prepared:
                run_id = entry["run_id"]
                if run_id not in next_sequence:
                    next_sequence[run_id] = int(
                        self._connection.execute(
                            "SELECT COALESCE(MAX(sequence), 0) + 1 FROM journal WHERE run_id = ?",
                            (run_id,),
                        ).fetchone()[0]
                    )
                sequence = next_sequence[run_id]
                next_sequence[run_id] += 1
                self._connection.execute(
                    """
                    INSERT INTO journal(record_id, run_id, sequence, event_time, recorded_at, category,
                                        entity_type, entity_id, account_id, payload_json)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        entry["record_id"],
                        run_id,
                        sequence,
                        entry["event_time"].isoformat(),
                        entry["recorded_at"].isoformat(),
                        entry["category"],
                        entry["entity_type"],
                        entry["entity_id"],
                        entry["account_id"],
                        entry["payload_json"],
                    ),
                )
                self._connection.execute(
                    "INSERT INTO outbox(record_id, attempts, last_error, delivered_at) VALUES (?, 0, '', NULL)",
                    (entry["record_id"],),
                )
                records.append(
                    JournalRecord(
                        entry["record_id"],
                        run_id,
                        sequence,
                        entry["event_time"],
                        entry["recorded_at"],
                        entry["category"],
                        entry["entity_type"],
                        entry["entity_id"],
                        entry["account_id"],
                        entry["payload"],
                    )
                )
        return records

    def append_once(
        self,
        *,
        run_id: str,
        category: str,
        entity_type: str,
        entity_id: str,
        payload: dict[str, Any],
        account_id: str = "",
        event_time: datetime | None = None,
    ) -> tuple[JournalRecord, bool]:
        """Append an event exactly once for its category/type/identity tuple."""

        with self._lock:
            existing = self._connection.execute(
                "SELECT * FROM journal WHERE category = ? AND entity_type = ? AND entity_id = ? LIMIT 1",
                (category, entity_type, entity_id),
            ).fetchone()
            if existing is not None:
                return _record(existing), False
            try:
                return (
                    self.append(
                        run_id=run_id,
                        category=category,
                        entity_type=entity_type,
                        entity_id=entity_id,
                        payload=payload,
                        account_id=account_id,
                        event_time=event_time,
                    ),
                    True,
                )
            except sqlite3.IntegrityError:
                existing = self._connection.execute(
                    "SELECT * FROM journal WHERE category = ? AND entity_type = ? AND entity_id = ? LIMIT 1",
                    (category, entity_type, entity_id),
                ).fetchone()
                if existing is None:
                    raise
                return _record(existing), False

    def append_once_many(
        self, entries: Iterable[dict[str, Any]]
    ) -> list[tuple[JournalRecord, bool]]:
        """Append an ordered event batch idempotently in one durable transaction.

        Identity follows :meth:`append_once`: category, entity type, and entity
        id are globally unique occurrence coordinates. Existing records and
        duplicate identities within the input are returned in input order.
        """

        pending = [dict(entry) for entry in entries]
        if not pending:
            return []
        keys = [
            (
                str(entry["category"]),
                str(entry["entity_type"]),
                str(entry["entity_id"]),
            )
            for entry in pending
        ]
        with self._lock:
            existing_by_key: dict[tuple[str, str, str], JournalRecord] = {}
            grouped_ids: dict[tuple[str, str], list[str]] = {}
            for category, entity_type, entity_id in dict.fromkeys(keys):
                grouped_ids.setdefault((category, entity_type), []).append(entity_id)
            for (category, entity_type), entity_ids in grouped_ids.items():
                for offset in range(0, len(entity_ids), 500):
                    chunk = entity_ids[offset : offset + 500]
                    placeholders = ",".join("?" for _ in chunk)
                    rows = self._connection.execute(
                        f"SELECT * FROM journal WHERE category = ? AND entity_type = ? "
                        f"AND entity_id IN ({placeholders})",
                        (category, entity_type, *chunk),
                    ).fetchall()
                    for row in rows:
                        record = _record(row)
                        existing_by_key[(category, entity_type, record.entity_id)] = record

            new_entries: list[dict[str, Any]] = []
            new_keys: list[tuple[str, str, str]] = []
            seen_new: set[tuple[str, str, str]] = set()
            for entry, key in zip(pending, keys, strict=True):
                if key not in existing_by_key and key not in seen_new:
                    new_entries.append(entry)
                    new_keys.append(key)
                    seen_new.add(key)
            inserted_records = self.append_many(new_entries)
            inserted_by_key = dict(zip(new_keys, inserted_records, strict=True))

            returned: list[tuple[JournalRecord, bool]] = []
            emitted_new: set[tuple[str, str, str]] = set()
            for key in keys:
                if key in existing_by_key:
                    returned.append((existing_by_key[key], False))
                    continue
                record = inserted_by_key[key]
                inserted = key not in emitted_new
                returned.append((record, inserted))
                emitted_new.add(key)
            return returned

    def save_checkpoint(self, run_id: str, cursor: str, state: dict[str, Any], event_time: datetime) -> None:
        with self._lock, self._connection:
            self._connection.execute(
                """
                INSERT INTO checkpoints(run_id, cursor, event_time, state_json, updated_at)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(run_id) DO UPDATE SET cursor=excluded.cursor, event_time=excluded.event_time,
                    state_json=excluded.state_json, updated_at=excluded.updated_at
                """,
                (run_id, cursor, event_time.astimezone(timezone.utc).isoformat(), json.dumps(state, sort_keys=True, default=_json_default), datetime.now(timezone.utc).isoformat()),
            )

    def load_checkpoint(self, run_id: str) -> dict[str, Any] | None:
        with self._lock:
            row = self._connection.execute(
                "SELECT * FROM checkpoints WHERE run_id = ?", (run_id,)
            ).fetchone()
        if row is None:
            return None
        return {"run_id": row["run_id"], "cursor": row["cursor"], "event_time": row["event_time"], "state": json.loads(row["state_json"]), "updated_at": row["updated_at"]}

    def save_portfolio_state(self, account_id: str, state: dict[str, Any]) -> None:
        if not account_id:
            raise ValueError("account_id is required")
        with self._lock, self._connection:
            self._connection.execute(
                """
                INSERT INTO portfolio_states(account_id, state_json, updated_at)
                VALUES (?, ?, ?)
                ON CONFLICT(account_id) DO UPDATE SET
                    state_json=excluded.state_json, updated_at=excluded.updated_at
                """,
                (
                    account_id,
                    json.dumps(state, sort_keys=True, default=_json_default),
                    datetime.now(timezone.utc).isoformat(),
                ),
            )

    def portfolio_states(self) -> dict[str, dict[str, Any]]:
        rows = self._fetchall(
            "SELECT account_id, state_json FROM portfolio_states ORDER BY account_id"
        )
        return {str(row["account_id"]): json.loads(row["state_json"]) for row in rows}

    def portfolio_reservation(
        self, account_id: str, reservation_id: str
    ) -> dict[str, Any] | None:
        if not account_id or not reservation_id:
            return None
        with self._lock:
            row = self._connection.execute(
                "SELECT state_json FROM portfolio_states WHERE account_id = ?",
                (account_id,),
            ).fetchone()
        if row is None:
            return None
        state = json.loads(str(row["state_json"]))
        return next(
            (
                dict(reservation)
                for reservation in state.get("reservations") or []
                if str(reservation.get("reservation_id") or "") == reservation_id
            ),
            None,
        )

    def acquire_portfolio_admission_lease(
        self,
        resource_id: str,
        *,
        owner_id: str,
        ttl_seconds: float = 30.0,
    ) -> dict[str, Any] | None:
        """Atomically acquire a cross-process fenced Portfolio admission lease."""
        if not resource_id or not owner_id or ttl_seconds <= 0:
            raise ValueError("Portfolio lease resource, owner, and positive TTL are required")
        now = datetime.now(timezone.utc)
        expires_at = now + timedelta(seconds=float(ttl_seconds))
        with self._lock:
            try:
                self._connection.execute("BEGIN IMMEDIATE")
                row = self._connection.execute(
                    "SELECT owner_id, epoch, expires_at FROM portfolio_admission_leases WHERE resource_id = ?",
                    (resource_id,),
                ).fetchone()
                if row is not None:
                    existing_expiry = datetime.fromisoformat(str(row["expires_at"]))
                    if existing_expiry > now and str(row["owner_id"]) != owner_id:
                        self._connection.rollback()
                        return None
                    epoch = int(row["epoch"]) + 1
                else:
                    epoch = 1
                self._connection.execute(
                    """
                    INSERT INTO portfolio_admission_leases(
                        resource_id, owner_id, epoch, expires_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?)
                    ON CONFLICT(resource_id) DO UPDATE SET
                        owner_id=excluded.owner_id, epoch=excluded.epoch,
                        expires_at=excluded.expires_at, updated_at=excluded.updated_at
                    """,
                    (
                        resource_id,
                        owner_id,
                        epoch,
                        expires_at.isoformat(),
                        now.isoformat(),
                    ),
                )
                self._connection.commit()
                return {
                    "resource_id": resource_id,
                    "owner_id": owner_id,
                    "epoch": epoch,
                    "expires_at": expires_at.isoformat(),
                }
            except Exception:
                self._connection.rollback()
                raise

    def portfolio_admission_lease_is_current(
        self, resource_id: str, *, owner_id: str, epoch: int
    ) -> bool:
        now = datetime.now(timezone.utc)
        with self._lock:
            row = self._connection.execute(
                "SELECT owner_id, epoch, expires_at FROM portfolio_admission_leases WHERE resource_id = ?",
                (resource_id,),
            ).fetchone()
        return bool(
            row is not None
            and str(row["owner_id"]) == owner_id
            and int(row["epoch"]) == int(epoch)
            and datetime.fromisoformat(str(row["expires_at"])) > now
        )

    def release_portfolio_admission_lease(
        self, resource_id: str, *, owner_id: str, epoch: int
    ) -> bool:
        """Release only the exact epoch; stale owners cannot clear newer leases."""
        with self._lock, self._connection:
            cursor = self._connection.execute(
                """
                DELETE FROM portfolio_admission_leases
                WHERE resource_id = ? AND owner_id = ? AND epoch = ?
                """,
                (resource_id, owner_id, int(epoch)),
            )
        return cursor.rowcount == 1

    def acquire_campaign_session_ownership(
        self,
        resource_id: str,
        *,
        session_key: str,
        owner_id: str,
        state: str,
    ) -> dict[str, Any] | None:
        """Atomically reserve or confirm one ticker owner for one session."""

        if not resource_id or not session_key or not owner_id:
            raise ValueError("Campaign ownership requires resource, session, and owner")
        if state not in {"reserved", "confirmed"}:
            raise ValueError("Campaign ownership state must be reserved or confirmed")
        now = datetime.now(timezone.utc).isoformat()
        with self._lock:
            try:
                self._connection.execute("BEGIN IMMEDIATE")
                row = self._connection.execute(
                    "SELECT owner_id, state, epoch FROM campaign_session_ownership "
                    "WHERE resource_id = ? AND session_key = ?",
                    (resource_id, session_key),
                ).fetchone()
                if row is not None and str(row["owner_id"]) != owner_id:
                    self._connection.rollback()
                    return None
                epoch = int(row["epoch"]) + 1 if row is not None else 1
                resolved_state = (
                    "confirmed"
                    if row is not None and str(row["state"]) == "confirmed"
                    else state
                )
                self._connection.execute(
                    """
                    INSERT INTO campaign_session_ownership(
                        resource_id, session_key, owner_id, state, epoch, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?)
                    ON CONFLICT(resource_id, session_key) DO UPDATE SET
                        owner_id=excluded.owner_id, state=excluded.state,
                        epoch=excluded.epoch, updated_at=excluded.updated_at
                    """,
                    (resource_id, session_key, owner_id, resolved_state, epoch, now),
                )
                self._connection.commit()
                return {
                    "resource_id": resource_id,
                    "session_key": session_key,
                    "owner_id": owner_id,
                    "state": resolved_state,
                    "epoch": epoch,
                }
            except Exception:
                self._connection.rollback()
                raise

    def campaign_session_ownership(
        self, resource_id: str, *, session_key: str
    ) -> dict[str, Any] | None:
        with self._lock:
            row = self._connection.execute(
                "SELECT owner_id, state, epoch, updated_at FROM campaign_session_ownership "
                "WHERE resource_id = ? AND session_key = ?",
                (resource_id, session_key),
            ).fetchone()
        if row is None:
            return None
        return {
            "resource_id": resource_id,
            "session_key": session_key,
            "owner_id": str(row["owner_id"]),
            "state": str(row["state"]),
            "epoch": int(row["epoch"]),
            "updated_at": str(row["updated_at"]),
        }

    def release_campaign_session_reservation(
        self, resource_id: str, *, session_key: str, owner_id: str
    ) -> bool:
        """Release a failed pre-fill reservation; confirmed owners are retained."""

        with self._lock, self._connection:
            cursor = self._connection.execute(
                "DELETE FROM campaign_session_ownership WHERE resource_id = ? "
                "AND session_key = ? AND owner_id = ? AND state = 'reserved'",
                (resource_id, session_key, owner_id),
            )
        return cursor.rowcount == 1

    def save_order_management_state(
        self,
        group_id: str,
        *,
        run_id: str,
        account_id: str,
        state: dict[str, Any],
    ) -> None:
        if not group_id or not account_id:
            raise ValueError("order group and account identity are required")
        with self._lock, self._connection:
            self._connection.execute(
                """
                INSERT INTO order_management_states(group_id, run_id, account_id, state_json, updated_at)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(group_id) DO UPDATE SET
                    state_json=excluded.state_json, updated_at=excluded.updated_at
                """,
                (
                    group_id,
                    run_id,
                    account_id,
                    json.dumps(state, sort_keys=True, default=_json_default),
                    datetime.now(timezone.utc).isoformat(),
                ),
            )

    def order_management_states(self, *, run_id: str | None = None) -> list[dict[str, Any]]:
        if run_id:
            rows = self._fetchall(
                "SELECT * FROM order_management_states WHERE run_id = ? ORDER BY updated_at",
                (run_id,),
            )
        else:
            rows = self._fetchall(
                "SELECT * FROM order_management_states ORDER BY updated_at"
            )
        return [
            {
                "group_id": str(row["group_id"]),
                "run_id": str(row["run_id"]),
                "account_id": str(row["account_id"]),
                "state": json.loads(row["state_json"]),
                "updated_at": str(row["updated_at"]),
            }
            for row in rows
        ]

    def publish_trading_configuration(
        self,
        *,
        revision_id: str,
        revision: int,
        label: str,
        content_hash: str,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        approved_at = datetime.now(timezone.utc).isoformat()
        with self._lock, self._connection:
            self._connection.execute(
                """
                INSERT INTO trading_configuration_revisions(
                    revision_id, revision, label, content_hash, payload_json, approved_at
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    revision_id,
                    revision,
                    label,
                    content_hash,
                    json.dumps(payload, sort_keys=True, default=_json_default),
                    approved_at,
                ),
            )
        return {
            "revision_id": revision_id,
            "revision": revision,
            "label": label,
            "content_hash": content_hash,
            "approved_at": approved_at,
            "payload": payload,
        }

    def save_trading_configuration_candidate(
        self,
        *,
        candidate_id: str,
        candidate_revision: int,
        label: str,
        content_hash: str,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        created_at = datetime.now(timezone.utc).isoformat()
        with self._lock, self._connection:
            self._connection.execute(
                """
                INSERT INTO trading_configuration_candidates(
                    candidate_id, candidate_revision, label, content_hash, payload_json, created_at
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    candidate_id,
                    candidate_revision,
                    label,
                    content_hash,
                    json.dumps(payload, sort_keys=True, default=_json_default),
                    created_at,
                ),
            )
        return {
            "candidate_id": candidate_id,
            "candidate_revision": candidate_revision,
            "label": label,
            "content_hash": content_hash,
            "created_at": created_at,
            "release_state": "test_candidate",
            "payload": payload,
        }

    def trading_configuration_candidates(self) -> list[dict[str, Any]]:
        rows = self._fetchall(
            "SELECT * FROM trading_configuration_candidates ORDER BY candidate_revision DESC"
        )
        return [_configuration_candidate(row) for row in rows]

    def trading_configuration_candidate(self, candidate_id: str = "") -> dict[str, Any] | None:
        row = (
            self._fetchone(
                "SELECT * FROM trading_configuration_candidates WHERE candidate_id = ?",
                (candidate_id,),
            )
            if candidate_id
            else self._fetchone(
                "SELECT * FROM trading_configuration_candidates ORDER BY candidate_revision DESC LIMIT 1"
            )
        )
        return _configuration_candidate(row) if row is not None else None

    def trading_configuration_revisions(self) -> list[dict[str, Any]]:
        rows = self._fetchall(
            "SELECT * FROM trading_configuration_revisions ORDER BY revision DESC"
        )
        return [_configuration_revision(row) for row in rows]

    def approved_trading_configuration(self) -> dict[str, Any] | None:
        row = self._fetchone(
            "SELECT * FROM trading_configuration_revisions ORDER BY revision DESC LIMIT 1"
        )
        return _configuration_revision(row) if row is not None else None

    def save_trade_annotation(
        self,
        episode_id: str,
        *,
        note: str = "",
        tags: Iterable[str] = (),
        review_status: str = "unreviewed",
        setup_override: str = "",
    ) -> dict[str, Any]:
        normalized_tags = tuple(dict.fromkeys(str(tag).strip() for tag in tags if str(tag).strip()))
        updated_at = datetime.now(timezone.utc).isoformat()
        with self._lock, self._connection:
            self._connection.execute(
                """
                INSERT INTO trade_annotations(episode_id, note, tags_json, review_status, setup_override, updated_at)
                VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(episode_id) DO UPDATE SET note=excluded.note, tags_json=excluded.tags_json,
                    review_status=excluded.review_status, setup_override=excluded.setup_override,
                    updated_at=excluded.updated_at
                """,
                (episode_id, note, json.dumps(normalized_tags), review_status, setup_override, updated_at),
            )
        return self.trade_annotation(episode_id) or {}

    def trade_annotation(self, episode_id: str) -> dict[str, Any] | None:
        row = self._fetchone(
            "SELECT * FROM trade_annotations WHERE episode_id = ?", (episode_id,)
        )
        if row is None:
            return None
        return {
            "episode_id": row["episode_id"],
            "note": row["note"],
            "tags": json.loads(row["tags_json"]),
            "review_status": row["review_status"],
            "setup_override": row["setup_override"],
            "updated_at": row["updated_at"],
        }

    def save_strategy(
        self,
        *,
        strategy_id: str,
        revision: int,
        name: str,
        implementation: str,
        automatic: bool,
        config: dict[str, Any],
        enabled: bool = True,
    ) -> None:
        with self._lock, self._connection:
            self._connection.execute(
                """
                INSERT INTO strategies(strategy_id, revision, name, implementation, automatic, enabled, config_json, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (strategy_id, revision, name, implementation, int(automatic), int(enabled), json.dumps(config, sort_keys=True, default=_json_default), datetime.now(timezone.utc).isoformat()),
            )

    def strategy(self, strategy_id: str, revision: int | None = None) -> dict[str, Any] | None:
        if revision is None:
            row = self._fetchone(
                "SELECT * FROM strategies WHERE strategy_id = ? ORDER BY revision DESC LIMIT 1", (strategy_id,)
            )
        else:
            row = self._fetchone(
                "SELECT * FROM strategies WHERE strategy_id = ? AND revision = ?", (strategy_id, revision)
            )
        if row is None:
            return None
        result = dict(row)
        result["automatic"] = bool(result["automatic"])
        result["enabled"] = bool(result["enabled"])
        result["config"] = json.loads(result.pop("config_json"))
        return result

    def strategies(self, *, latest_only: bool = True) -> list[dict[str, Any]]:
        if latest_only:
            rows = self._fetchall(
                """
                SELECT strategies.* FROM strategies
                JOIN (SELECT strategy_id, MAX(revision) AS revision FROM strategies GROUP BY strategy_id) latest
                  ON latest.strategy_id = strategies.strategy_id AND latest.revision = strategies.revision
                ORDER BY strategies.name, strategies.strategy_id
                """
            )
        else:
            rows = self._fetchall(
                "SELECT * FROM strategies ORDER BY name, strategy_id, revision DESC"
            )
        results = []
        for row in rows:
            result = dict(row)
            result["automatic"] = bool(result["automatic"])
            result["enabled"] = bool(result["enabled"])
            result["config"] = json.loads(result.pop("config_json"))
            results.append(result)
        return results

    def save_strategy_assignment(self, payload: dict[str, Any]) -> dict[str, Any]:
        saved = self.save_strategy_assignments([payload])
        return saved[0] if saved else {}

    def save_strategy_assignments(
        self, payloads: Iterable[dict[str, Any]]
    ) -> list[dict[str, Any]]:
        """Upsert an assignment set in one crash-safe transaction."""

        prepared: list[tuple[Any, ...]] = []
        assignment_ids: list[str] = []
        now = datetime.now(timezone.utc).isoformat()
        for raw in payloads:
            payload = dict(raw)
            assignment_id = str(payload.get("assignment_id") or "").strip()
            if not assignment_id:
                raise ValueError("assignment_id is required")
            assignment_ids.append(assignment_id)
            prepared.append(
                (
                    assignment_id,
                    str(payload.get("strategy_id") or ""),
                    int(payload.get("strategy_revision") or 0),
                    str(payload.get("account_id") or ""),
                    str(payload.get("ticker") or "").upper(),
                    int(payload.get("conid") or 0),
                    str(payload.get("status") or ""),
                    json.dumps(
                        payload.get("permissions") or {},
                        sort_keys=True,
                        default=_json_default,
                    ),
                    json.dumps(
                        payload.get("parameters") or {},
                        sort_keys=True,
                        default=_json_default,
                    ),
                    json.dumps(
                        payload.get("state") or {},
                        sort_keys=True,
                        default=_json_default,
                    ),
                    str(payload.get("source") or "order_entry"),
                    str(payload.get("created_at") or now),
                    str(payload.get("updated_at") or now),
                )
            )
        if not prepared:
            return []
        with self._lock, self._connection:
            self._connection.executemany(
                """
                INSERT INTO strategy_assignments(
                    assignment_id, strategy_id, strategy_revision, account_id, ticker, conid,
                    status, permissions_json, parameters_json, state_json, source, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(assignment_id) DO UPDATE SET
                    status=excluded.status, permissions_json=excluded.permissions_json,
                    parameters_json=excluded.parameters_json, state_json=excluded.state_json,
                    updated_at=excluded.updated_at
                """,
                prepared,
            )
        placeholders = ",".join("?" for _ in assignment_ids)
        rows = self._fetchall(
            f"SELECT * FROM strategy_assignments WHERE assignment_id IN ({placeholders})",
            assignment_ids,
        )
        by_id = {
            str(row["assignment_id"]): _assignment(row)
            for row in rows
        }
        return [by_id[assignment_id] for assignment_id in assignment_ids]

    def strategy_assignment(self, assignment_id: str) -> dict[str, Any] | None:
        row = self._fetchone(
            "SELECT * FROM strategy_assignments WHERE assignment_id = ?", (assignment_id,)
        )
        return _assignment(row) if row is not None else None

    def strategy_assignments(
        self,
        *,
        account_id: str = "",
        ticker: str = "",
        active_only: bool = False,
    ) -> list[dict[str, Any]]:
        clauses: list[str] = []
        values: list[Any] = []
        if account_id:
            clauses.append("account_id = ?")
            values.append(account_id)
        if ticker:
            clauses.append("ticker = ?")
            values.append(ticker.upper())
        if active_only:
            clauses.append("status NOT IN ('disabled', 'completed', 'error')")
        where = f" WHERE {' AND '.join(clauses)}" if clauses else ""
        rows = self._fetchall(
            f"SELECT * FROM strategy_assignments{where} ORDER BY updated_at DESC", values
        )
        return [_assignment(row) for row in rows]

    def strategy_records(
        self,
        *,
        ticker: str = "",
        strategy_id: str = "",
        as_of: datetime | None = None,
        limit: int = 5000,
    ) -> list[JournalRecord]:
        clauses = ["category IN ('strategy', 'strategy_decision')"]
        values: list[Any] = []
        if ticker:
            clauses.append("upper(json_extract(payload_json, '$.ticker')) = ?")
            values.append(ticker.upper())
        if strategy_id:
            clauses.append("json_extract(payload_json, '$.strategy_id') = ?")
            values.append(strategy_id)
        if as_of is not None:
            clauses.append("event_time <= ?")
            values.append(as_of.astimezone(timezone.utc).isoformat())
        values.append(max(1, min(int(limit), 50_000)))
        rows = self._fetchall(
            f"SELECT * FROM journal WHERE {' AND '.join(clauses)} ORDER BY event_time DESC, recorded_at DESC LIMIT ?",
            values,
        )
        return [_record(row) for row in reversed(rows)]

    def strategy_activity_records(
        self,
        *,
        strategy_id: str = "",
        run_id: str = "",
        ticker: str = "",
        as_of: datetime | None = None,
        limit: int = 2000,
        consequential_only: bool = False,
    ) -> list[JournalRecord]:
        """Return newest-first durable strategy events across ticker campaigns.

        This query intentionally excludes broker and portfolio records. Those
        authorities have their own Canvas surfaces. Strategy Activity includes
        the causal discovery signal, Watchlist admission, semantic strategy
        intent, campaign-state transition, and order-management handoff that
        explain why the strategy did or did not act.
        """
        clauses = [
            "category IN ('market_discovery_signal', 'watchlist_membership', "
            "'strategy', 'strategy_decision', 'order_management')"
        ]
        values: list[Any] = []
        if strategy_id:
            clauses.append("json_extract(payload_json, '$.strategy_id') = ?")
            values.append(strategy_id)
        if run_id:
            clauses.append("run_id = ?")
            values.append(run_id)
        if ticker:
            clauses.append("upper(json_extract(payload_json, '$.ticker')) = ?")
            values.append(ticker.upper())
        if as_of is not None:
            clauses.append("event_time <= ?")
            values.append(as_of.astimezone(timezone.utc).isoformat())
        if consequential_only:
            clauses.append(
                "(category = 'market_discovery_signal' OR "
                "(category IN ('strategy', 'strategy_decision') AND "
                "entity_type <> 'strategy_assignment_state' AND "
                "lower(coalesce(json_extract(payload_json, '$.action'), '')) "
                "NOT IN ('', 'wait')) OR "
                "(entity_type = 'strategy_assignment_state' AND "
                "lower(coalesce(json_extract(payload_json, '$.action'), "
                "json_extract(payload_json, '$.status'), "
                "json_extract(payload_json, '$.state'), '')) "
                "<> 'assignment_state_saved'))"
            )
        values.append(max(1, min(int(limit), 50_000)))
        rows = self._fetchall(
            f"SELECT * FROM journal WHERE {' AND '.join(clauses)} "
            "ORDER BY event_time DESC, recorded_at DESC, sequence DESC LIMIT ?",
            values,
        )
        return [_record(row) for row in rows]

    def order_management_records(
        self,
        *,
        ticker: str = "",
        strategy_id: str = "",
        as_of: datetime | None = None,
        limit: int = 2000,
    ) -> list[JournalRecord]:
        clauses = [
            "category IN ('order_management', 'broker_policy', 'broker', 'command', 'execution', 'risk')"
        ]
        values: list[Any] = []
        if ticker:
            clauses.append("upper(json_extract(payload_json, '$.ticker')) = ?")
            values.append(ticker.upper())
        if strategy_id:
            clauses.append("json_extract(payload_json, '$.strategy_id') = ?")
            values.append(strategy_id)
        if as_of is not None:
            clauses.append("event_time <= ?")
            values.append(as_of.astimezone(timezone.utc).isoformat())
        values.append(max(1, min(int(limit), 50_000)))
        rows = self._fetchall(
            f"SELECT * FROM journal WHERE {' AND '.join(clauses)} "
            "ORDER BY event_time DESC, recorded_at DESC LIMIT ?",
            values,
        )
        return [_record(row) for row in reversed(rows)]

    def portfolio_management_records(
        self,
        *,
        account_id: str = "",
        limit: int = 2000,
    ) -> list[JournalRecord]:
        clauses = ["category = 'portfolio_management'"]
        values: list[Any] = []
        if account_id:
            clauses.append("account_id = ?")
            values.append(account_id)
        values.append(max(1, min(int(limit), 50_000)))
        rows = self._fetchall(
            f"SELECT * FROM journal WHERE {' AND '.join(clauses)} "
            "ORDER BY event_time DESC, recorded_at DESC LIMIT ?",
            values,
        )
        return [_record(row) for row in reversed(rows)]

    def records(self, run_id: str, *, after_sequence: int = 0) -> list[JournalRecord]:
        rows = self._fetchall(
            "SELECT * FROM journal WHERE run_id = ? AND sequence > ? ORDER BY sequence",
            (run_id, after_sequence),
        )
        return [_record(row) for row in rows]

    def latest_sequence(self, run_id: str) -> int:
        row = self._fetchone(
            "SELECT COALESCE(MAX(sequence), 0) AS sequence FROM journal WHERE run_id = ?",
            (run_id,),
        )
        return int(row["sequence"] if row is not None else 0)

    def next_record_after_time(
        self,
        run_id: str,
        event_time: datetime,
        *,
        categories: tuple[str, ...],
    ) -> JournalRecord | None:
        if not categories:
            return None
        placeholders = ",".join("?" for _ in categories)
        row = self._fetchone(
            f"SELECT * FROM journal WHERE run_id = ? AND event_time > ? "
            f"AND category IN ({placeholders}) "
            "ORDER BY event_time ASC, sequence ASC LIMIT 1",
            (
                run_id,
                event_time.astimezone(timezone.utc).isoformat(),
                *categories,
            ),
        )
        return _record(row) if row is not None else None

    def watchlist_membership_records(
        self,
        *,
        watchlist_id: str = "",
        limit: int = 10_000,
    ) -> list[JournalRecord]:
        clauses = ["category = 'watchlist_membership'"]
        values: list[Any] = []
        if watchlist_id:
            clauses.append("json_extract(payload_json, '$.watchlist_id') = ?")
            values.append(watchlist_id)
        values.append(max(1, min(int(limit), 50_000)))
        rows = self._fetchall(
            f"SELECT * FROM journal WHERE {' AND '.join(clauses)} "
            "ORDER BY event_time ASC, recorded_at ASC, sequence ASC LIMIT ?",
            values,
        )
        return [_record(row) for row in rows]

    def signal_stream_records(
        self,
        *,
        run_id: str = "",
        signal_stream_id: str = "",
        from_time: datetime | None = None,
        as_of: datetime | None = None,
        limit: int = 10_000,
    ) -> list[JournalRecord]:
        clauses = ["category = 'market_discovery_signal'", "entity_type = 'signal_occurrence'"]
        values: list[Any] = []
        if run_id:
            clauses.append("run_id = ?")
            values.append(run_id)
        if signal_stream_id:
            clauses.append("json_extract(payload_json, '$.signal_stream_id') = ?")
            values.append(signal_stream_id)
        if from_time is not None:
            clauses.append("event_time >= ?")
            values.append(from_time.astimezone(timezone.utc).isoformat())
        if as_of is not None:
            clauses.append("event_time <= ?")
            values.append(as_of.astimezone(timezone.utc).isoformat())
        values.append(max(1, min(int(limit), 50_000)))
        rows = self._fetchall(
            f"SELECT * FROM journal WHERE {' AND '.join(clauses)} "
            "ORDER BY event_time DESC, recorded_at DESC, sequence DESC LIMIT ?",
            values,
        )
        return [_record(row) for row in rows]

    def recent_records(
        self,
        run_id: str,
        *,
        categories: tuple[str, ...],
        limit: int = 5_000,
    ) -> list[JournalRecord]:
        if not categories:
            return []
        bounded_limit = max(1, min(int(limit), 50_000))
        placeholders = ",".join("?" for _ in categories)
        rows = self._fetchall(
            f"""
            SELECT * FROM (
                SELECT * FROM journal
                WHERE run_id = ? AND category IN ({placeholders})
                ORDER BY sequence DESC LIMIT ?
            ) ORDER BY sequence
            """,
            (run_id, *categories, bounded_limit),
        )
        return [_record(row) for row in rows]

    def pending_outbox(self, limit: int = 500) -> list[JournalRecord]:
        rows = self._fetchall(
            """
            SELECT journal.* FROM journal
            JOIN outbox USING(record_id)
            WHERE outbox.delivered_at IS NULL
            ORDER BY journal.recorded_at, journal.sequence LIMIT ?
            """,
            (limit,),
        )
        return [_record(row) for row in rows]

    def mark_delivered(self, record_ids: Iterable[str]) -> None:
        now = datetime.now(timezone.utc).isoformat()
        with self._lock, self._connection:
            self._connection.executemany("UPDATE outbox SET delivered_at = ? WHERE record_id = ?", ((now, record_id) for record_id in record_ids))

    def mark_failed(self, record_ids: Iterable[str], error: str) -> None:
        with self._lock, self._connection:
            self._connection.executemany(
                "UPDATE outbox SET attempts = attempts + 1, last_error = ? WHERE record_id = ?",
                ((error[:2000], record_id) for record_id in record_ids),
            )

    def _initialize(self) -> None:
        with self._connection:
            self._connection.execute("PRAGMA journal_mode=WAL")
            self._connection.execute(f"PRAGMA synchronous={self.synchronous}")
            self._connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS journal(
                    record_id TEXT PRIMARY KEY, run_id TEXT NOT NULL, sequence INTEGER NOT NULL,
                    event_time TEXT NOT NULL, recorded_at TEXT NOT NULL, category TEXT NOT NULL,
                    entity_type TEXT NOT NULL, entity_id TEXT NOT NULL, account_id TEXT NOT NULL,
                    payload_json TEXT NOT NULL, UNIQUE(run_id, sequence)
                );
                CREATE INDEX IF NOT EXISTS idx_journal_entity ON journal(entity_type, entity_id, sequence);
                CREATE INDEX IF NOT EXISTS idx_journal_signal_time ON journal(category, entity_type, event_time DESC);
                CREATE INDEX IF NOT EXISTS idx_journal_run_event_time
                    ON journal(run_id, event_time DESC, sequence DESC);
                CREATE INDEX IF NOT EXISTS idx_journal_run_category_sequence
                    ON journal(run_id, category, sequence DESC);
                CREATE TABLE IF NOT EXISTS outbox(
                    record_id TEXT PRIMARY KEY REFERENCES journal(record_id), attempts INTEGER NOT NULL,
                    last_error TEXT NOT NULL, delivered_at TEXT
                );
                CREATE TABLE IF NOT EXISTS checkpoints(
                    run_id TEXT PRIMARY KEY, cursor TEXT NOT NULL, event_time TEXT NOT NULL,
                    state_json TEXT NOT NULL, updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS strategies(
                    strategy_id TEXT NOT NULL, revision INTEGER NOT NULL, name TEXT NOT NULL,
                    implementation TEXT NOT NULL, automatic INTEGER NOT NULL, enabled INTEGER NOT NULL,
                    config_json TEXT NOT NULL, created_at TEXT NOT NULL,
                    PRIMARY KEY(strategy_id, revision)
                );
                CREATE TABLE IF NOT EXISTS trade_annotations(
                    episode_id TEXT PRIMARY KEY, note TEXT NOT NULL, tags_json TEXT NOT NULL,
                    review_status TEXT NOT NULL, setup_override TEXT NOT NULL, updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS strategy_assignments(
                    assignment_id TEXT PRIMARY KEY, strategy_id TEXT NOT NULL,
                    strategy_revision INTEGER NOT NULL, account_id TEXT NOT NULL,
                    ticker TEXT NOT NULL, conid INTEGER NOT NULL, status TEXT NOT NULL,
                    permissions_json TEXT NOT NULL, parameters_json TEXT NOT NULL,
                    state_json TEXT NOT NULL, source TEXT NOT NULL,
                    created_at TEXT NOT NULL, updated_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_strategy_assignment_scope
                    ON strategy_assignments(account_id, ticker, updated_at);
                CREATE TABLE IF NOT EXISTS portfolio_states(
                    account_id TEXT PRIMARY KEY, state_json TEXT NOT NULL, updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS portfolio_admission_leases(
                    resource_id TEXT PRIMARY KEY, owner_id TEXT NOT NULL,
                    epoch INTEGER NOT NULL, expires_at TEXT NOT NULL, updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS campaign_session_ownership(
                    resource_id TEXT NOT NULL, session_key TEXT NOT NULL,
                    owner_id TEXT NOT NULL, state TEXT NOT NULL,
                    epoch INTEGER NOT NULL, updated_at TEXT NOT NULL,
                    PRIMARY KEY(resource_id, session_key)
                );
                CREATE TABLE IF NOT EXISTS order_management_states(
                    group_id TEXT PRIMARY KEY, run_id TEXT NOT NULL, account_id TEXT NOT NULL,
                    state_json TEXT NOT NULL, updated_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_order_management_state_run
                    ON order_management_states(run_id, account_id, updated_at);
                DROP TABLE IF EXISTS trading_configuration_draft;
                CREATE TABLE IF NOT EXISTS trading_configuration_revisions(
                    revision_id TEXT PRIMARY KEY, revision INTEGER NOT NULL UNIQUE,
                    label TEXT NOT NULL, content_hash TEXT NOT NULL UNIQUE,
                    payload_json TEXT NOT NULL, approved_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS trading_configuration_candidates(
                    candidate_id TEXT PRIMARY KEY,
                    candidate_revision INTEGER NOT NULL UNIQUE,
                    label TEXT NOT NULL,
                    content_hash TEXT NOT NULL UNIQUE,
                    payload_json TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                """
            )


def _record(row: sqlite3.Row) -> JournalRecord:
    return JournalRecord(
        record_id=row["record_id"], run_id=row["run_id"], sequence=int(row["sequence"]),
        event_time=datetime.fromisoformat(row["event_time"]), recorded_at=datetime.fromisoformat(row["recorded_at"]),
        category=row["category"], entity_type=row["entity_type"], entity_id=row["entity_id"],
        account_id=row["account_id"], payload=json.loads(row["payload_json"]),
    )


def _assignment(row: sqlite3.Row) -> dict[str, Any]:
    result = dict(row)
    result["permissions"] = json.loads(result.pop("permissions_json"))
    result["parameters"] = json.loads(result.pop("parameters_json"))
    result["state"] = json.loads(result.pop("state_json"))
    return result


def _configuration_revision(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "revision_id": str(row["revision_id"]),
        "revision": int(row["revision"]),
        "label": str(row["label"]),
        "content_hash": str(row["content_hash"]),
        "approved_at": str(row["approved_at"]),
        "payload": json.loads(row["payload_json"]),
    }


def _configuration_candidate(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "candidate_id": str(row["candidate_id"]),
        "candidate_revision": int(row["candidate_revision"]),
        "label": str(row["label"]),
        "content_hash": str(row["content_hash"]),
        "created_at": str(row["created_at"]),
        "release_state": "test_candidate",
        "payload": json.loads(row["payload_json"]),
    }


def _json_default(value: Any) -> Any:
    if isinstance(value, datetime):
        return value.astimezone(timezone.utc).isoformat()
    try:
        return asdict(value)
    except TypeError:
        return str(value)
