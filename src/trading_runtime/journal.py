from __future__ import annotations

import json
import sqlite3
import threading
import uuid
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable


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

    def __init__(self, path: Path) -> None:
        self.path = path
        path.parent.mkdir(parents=True, exist_ok=True)
        self._connection = sqlite3.connect(path, check_same_thread=False)
        self._connection.row_factory = sqlite3.Row
        self._lock = threading.RLock()
        self._initialize()

    def close(self) -> None:
        self._connection.close()

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
        event_time = (event_time or datetime.now(timezone.utc)).astimezone(timezone.utc)
        recorded_at = datetime.now(timezone.utc)
        record_id = str(uuid.uuid4())
        payload_json = json.dumps(payload, separators=(",", ":"), sort_keys=True, default=_json_default)
        with self._lock, self._connection:
            sequence = int(
                self._connection.execute(
                    "SELECT COALESCE(MAX(sequence), 0) + 1 FROM journal WHERE run_id = ?",
                    (run_id,),
                ).fetchone()[0]
            )
            self._connection.execute(
                """
                INSERT INTO journal(record_id, run_id, sequence, event_time, recorded_at, category,
                                    entity_type, entity_id, account_id, payload_json)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    record_id,
                    run_id,
                    sequence,
                    event_time.isoformat(),
                    recorded_at.isoformat(),
                    category,
                    entity_type,
                    entity_id,
                    account_id,
                    payload_json,
                ),
            )
            self._connection.execute(
                "INSERT INTO outbox(record_id, attempts, last_error, delivered_at) VALUES (?, 0, '', NULL)",
                (record_id,),
            )
        return JournalRecord(record_id, run_id, sequence, event_time, recorded_at, category, entity_type, entity_id, account_id, payload)

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
        row = self._connection.execute("SELECT * FROM checkpoints WHERE run_id = ?", (run_id,)).fetchone()
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
        rows = self._connection.execute(
            "SELECT account_id, state_json FROM portfolio_states ORDER BY account_id"
        ).fetchall()
        return {str(row["account_id"]): json.loads(row["state_json"]) for row in rows}

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
            rows = self._connection.execute(
                "SELECT * FROM order_management_states WHERE run_id = ? ORDER BY updated_at",
                (run_id,),
            ).fetchall()
        else:
            rows = self._connection.execute(
                "SELECT * FROM order_management_states ORDER BY updated_at"
            ).fetchall()
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

    def save_trading_configuration_draft(self, payload: dict[str, Any]) -> dict[str, Any]:
        updated_at = datetime.now(timezone.utc).isoformat()
        with self._lock, self._connection:
            self._connection.execute(
                """
                INSERT INTO trading_configuration_draft(singleton_id, payload_json, updated_at)
                VALUES (1, ?, ?)
                ON CONFLICT(singleton_id) DO UPDATE SET
                    payload_json=excluded.payload_json, updated_at=excluded.updated_at
                """,
                (
                    json.dumps(payload, sort_keys=True, default=_json_default),
                    updated_at,
                ),
            )
        return {**payload, "updated_at": updated_at}

    def trading_configuration_draft(self) -> dict[str, Any] | None:
        row = self._connection.execute(
            "SELECT payload_json, updated_at FROM trading_configuration_draft WHERE singleton_id = 1"
        ).fetchone()
        if row is None:
            return None
        return {**json.loads(row["payload_json"]), "updated_at": str(row["updated_at"])}

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

    def trading_configuration_revisions(self) -> list[dict[str, Any]]:
        rows = self._connection.execute(
            "SELECT * FROM trading_configuration_revisions ORDER BY revision DESC"
        ).fetchall()
        return [_configuration_revision(row) for row in rows]

    def approved_trading_configuration(self) -> dict[str, Any] | None:
        row = self._connection.execute(
            "SELECT * FROM trading_configuration_revisions ORDER BY revision DESC LIMIT 1"
        ).fetchone()
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
        row = self._connection.execute("SELECT * FROM trade_annotations WHERE episode_id = ?", (episode_id,)).fetchone()
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
            row = self._connection.execute(
                "SELECT * FROM strategies WHERE strategy_id = ? ORDER BY revision DESC LIMIT 1", (strategy_id,)
            ).fetchone()
        else:
            row = self._connection.execute(
                "SELECT * FROM strategies WHERE strategy_id = ? AND revision = ?", (strategy_id, revision)
            ).fetchone()
        if row is None:
            return None
        result = dict(row)
        result["automatic"] = bool(result["automatic"])
        result["enabled"] = bool(result["enabled"])
        result["config"] = json.loads(result.pop("config_json"))
        return result

    def strategies(self, *, latest_only: bool = True) -> list[dict[str, Any]]:
        if latest_only:
            rows = self._connection.execute(
                """
                SELECT strategies.* FROM strategies
                JOIN (SELECT strategy_id, MAX(revision) AS revision FROM strategies GROUP BY strategy_id) latest
                  ON latest.strategy_id = strategies.strategy_id AND latest.revision = strategies.revision
                ORDER BY strategies.name, strategies.strategy_id
                """
            ).fetchall()
        else:
            rows = self._connection.execute("SELECT * FROM strategies ORDER BY name, strategy_id, revision DESC").fetchall()
        results = []
        for row in rows:
            result = dict(row)
            result["automatic"] = bool(result["automatic"])
            result["enabled"] = bool(result["enabled"])
            result["config"] = json.loads(result.pop("config_json"))
            results.append(result)
        return results

    def save_strategy_assignment(self, payload: dict[str, Any]) -> dict[str, Any]:
        assignment_id = str(payload.get("assignment_id") or "").strip()
        if not assignment_id:
            raise ValueError("assignment_id is required")
        now = datetime.now(timezone.utc).isoformat()
        created_at = str(payload.get("created_at") or now)
        updated_at = str(payload.get("updated_at") or now)
        with self._lock, self._connection:
            self._connection.execute(
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
                (
                    assignment_id,
                    str(payload.get("strategy_id") or ""),
                    int(payload.get("strategy_revision") or 0),
                    str(payload.get("account_id") or ""),
                    str(payload.get("ticker") or "").upper(),
                    int(payload.get("conid") or 0),
                    str(payload.get("status") or ""),
                    json.dumps(payload.get("permissions") or {}, sort_keys=True, default=_json_default),
                    json.dumps(payload.get("parameters") or {}, sort_keys=True, default=_json_default),
                    json.dumps(payload.get("state") or {}, sort_keys=True, default=_json_default),
                    str(payload.get("source") or "order_entry"),
                    created_at,
                    updated_at,
                ),
            )
        return self.strategy_assignment(assignment_id) or {}

    def strategy_assignment(self, assignment_id: str) -> dict[str, Any] | None:
        row = self._connection.execute(
            "SELECT * FROM strategy_assignments WHERE assignment_id = ?", (assignment_id,)
        ).fetchone()
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
        rows = self._connection.execute(
            f"SELECT * FROM strategy_assignments{where} ORDER BY updated_at DESC", values
        ).fetchall()
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
        rows = self._connection.execute(
            f"SELECT * FROM journal WHERE {' AND '.join(clauses)} ORDER BY event_time DESC, recorded_at DESC LIMIT ?",
            values,
        ).fetchall()
        return [_record(row) for row in reversed(rows)]

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
        rows = self._connection.execute(
            f"SELECT * FROM journal WHERE {' AND '.join(clauses)} "
            "ORDER BY event_time DESC, recorded_at DESC LIMIT ?",
            values,
        ).fetchall()
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
        rows = self._connection.execute(
            f"SELECT * FROM journal WHERE {' AND '.join(clauses)} "
            "ORDER BY event_time DESC, recorded_at DESC LIMIT ?",
            values,
        ).fetchall()
        return [_record(row) for row in reversed(rows)]

    def records(self, run_id: str, *, after_sequence: int = 0) -> list[JournalRecord]:
        rows = self._connection.execute(
            "SELECT * FROM journal WHERE run_id = ? AND sequence > ? ORDER BY sequence",
            (run_id, after_sequence),
        ).fetchall()
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
        rows = self._connection.execute(
            f"""
            SELECT * FROM (
                SELECT * FROM journal
                WHERE run_id = ? AND category IN ({placeholders})
                ORDER BY sequence DESC LIMIT ?
            ) ORDER BY sequence
            """,
            (run_id, *categories, bounded_limit),
        ).fetchall()
        return [_record(row) for row in rows]

    def pending_outbox(self, limit: int = 500) -> list[JournalRecord]:
        rows = self._connection.execute(
            """
            SELECT journal.* FROM journal
            JOIN outbox USING(record_id)
            WHERE outbox.delivered_at IS NULL
            ORDER BY journal.recorded_at, journal.sequence LIMIT ?
            """,
            (limit,),
        ).fetchall()
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
            self._connection.execute("PRAGMA synchronous=FULL")
            self._connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS journal(
                    record_id TEXT PRIMARY KEY, run_id TEXT NOT NULL, sequence INTEGER NOT NULL,
                    event_time TEXT NOT NULL, recorded_at TEXT NOT NULL, category TEXT NOT NULL,
                    entity_type TEXT NOT NULL, entity_id TEXT NOT NULL, account_id TEXT NOT NULL,
                    payload_json TEXT NOT NULL, UNIQUE(run_id, sequence)
                );
                CREATE INDEX IF NOT EXISTS idx_journal_entity ON journal(entity_type, entity_id, sequence);
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
                CREATE TABLE IF NOT EXISTS order_management_states(
                    group_id TEXT PRIMARY KEY, run_id TEXT NOT NULL, account_id TEXT NOT NULL,
                    state_json TEXT NOT NULL, updated_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_order_management_state_run
                    ON order_management_states(run_id, account_id, updated_at);
                CREATE TABLE IF NOT EXISTS trading_configuration_draft(
                    singleton_id INTEGER PRIMARY KEY CHECK(singleton_id = 1),
                    payload_json TEXT NOT NULL, updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS trading_configuration_revisions(
                    revision_id TEXT PRIMARY KEY, revision INTEGER NOT NULL UNIQUE,
                    label TEXT NOT NULL, content_hash TEXT NOT NULL UNIQUE,
                    payload_json TEXT NOT NULL, approved_at TEXT NOT NULL
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


def _json_default(value: Any) -> Any:
    if isinstance(value, datetime):
        return value.astimezone(timezone.utc).isoformat()
    try:
        return asdict(value)
    except TypeError:
        return str(value)
