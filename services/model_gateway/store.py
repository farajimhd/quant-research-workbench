from __future__ import annotations

import json
import sqlite3
import threading
from contextlib import closing
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


class AuditStore:
    def __init__(self, root: Path):
        root.mkdir(parents=True, exist_ok=True)
        self.path = root / "audit.sqlite3"
        self._lock = threading.Lock()
        with closing(self._connect()) as db, db:
            db.execute(
                """CREATE TABLE IF NOT EXISTS inference_audit (
                idempotency_key TEXT PRIMARY KEY, route TEXT NOT NULL, request_hash TEXT NOT NULL,
                status TEXT NOT NULL, provider TEXT NOT NULL, model TEXT NOT NULL,
                response_json TEXT NOT NULL, input_tokens INTEGER NOT NULL,
                output_tokens INTEGER NOT NULL, cost_usd REAL NOT NULL,
                latency_ms INTEGER NOT NULL, created_at_utc TEXT NOT NULL)"""
            )
            db.execute(
                """CREATE TABLE IF NOT EXISTS inference_attempt_audit (
                attempt_id INTEGER PRIMARY KEY AUTOINCREMENT,
                idempotency_key TEXT NOT NULL, route TEXT NOT NULL,
                provider TEXT NOT NULL, model TEXT NOT NULL,
                attempt INTEGER NOT NULL, status TEXT NOT NULL,
                error TEXT NOT NULL, latency_ms INTEGER NOT NULL,
                created_at_utc TEXT NOT NULL)"""
            )

    def get(self, key: str, request_hash: str) -> dict[str, Any] | None:
        with self._lock, closing(self._connect()) as db:
            row = db.execute(
                "SELECT route,status,provider,model,response_json,input_tokens,output_tokens,cost_usd,latency_ms,"
                "request_hash FROM inference_audit WHERE idempotency_key=?",
                (key,),
            ).fetchone()
        if row is None:
            return None
        if row[-1] != request_hash:
            raise ValueError("idempotency key was reused with a different request")
        return {
            "route": row[0], "status": row[1], "provider": row[2], "model": row[3],
            "result": json.loads(row[4]), "input_tokens": row[5], "output_tokens": row[6],
            "cost_usd": row[7], "latency_ms": row[8],
        }

    def put(self, key: str, request_hash: str, row: dict[str, Any]) -> None:
        with self._lock, closing(self._connect()) as db, db:
            db.execute(
                "INSERT OR REPLACE INTO inference_audit VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    key, row["route"], request_hash, row["status"], row["provider"], row["model"],
                    json.dumps(row["result"], separators=(",", ":")), row["input_tokens"],
                    row["output_tokens"], row["cost_usd"], row["latency_ms"],
                    datetime.now(UTC).isoformat(),
                ),
            )

    def spent_today(self, route: str) -> float:
        day = datetime.now(UTC).date().isoformat()
        with self._lock, closing(self._connect()) as db:
            value = db.execute(
                "SELECT COALESCE(SUM(cost_usd),0) FROM inference_audit WHERE route=? AND created_at_utc>=?",
                (route, day),
            ).fetchone()[0]
        return float(value)

    def record_attempt(
        self,
        *,
        idempotency_key: str,
        route: str,
        provider: str,
        model: str,
        attempt: int,
        status: str,
        error: str,
        latency_ms: int,
    ) -> None:
        with self._lock, closing(self._connect()) as db, db:
            db.execute(
                """INSERT INTO inference_attempt_audit
                (idempotency_key,route,provider,model,attempt,status,error,latency_ms,created_at_utc)
                VALUES (?,?,?,?,?,?,?,?,?)""",
                (
                    idempotency_key,
                    route,
                    provider,
                    model,
                    attempt,
                    status,
                    error[:1000],
                    max(0, int(latency_ms)),
                    datetime.now(UTC).isoformat(),
                ),
            )

    def attempt_metrics(self) -> dict[str, Any]:
        day = datetime.now(UTC).date().isoformat()
        with self._lock, closing(self._connect()) as db:
            total, failed = db.execute(
                """SELECT count(),COALESCE(SUM(CASE WHEN status='failed' THEN 1 ELSE 0 END),0)
                FROM inference_attempt_audit WHERE created_at_utc>=?""",
                (day,),
            ).fetchone()
            latest = db.execute(
                """SELECT route,provider,status,error,latency_ms,created_at_utc
                FROM inference_attempt_audit ORDER BY attempt_id DESC LIMIT 1"""
            ).fetchone()
        result: dict[str, Any] = {
            "attempts_today": int(total),
            "failed_attempts_today": int(failed),
        }
        if latest:
            result["latest_attempt"] = {
                "route": latest[0],
                "provider": latest[1],
                "status": latest[2],
                "error": latest[3],
                "latency_ms": int(latest[4]),
                "created_at_utc": latest[5],
            }
        return result

    def _connect(self) -> sqlite3.Connection:
        return sqlite3.connect(self.path, timeout=30)
