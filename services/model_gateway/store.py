from __future__ import annotations

import json
import sqlite3
import threading
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


class AuditStore:
    def __init__(self, root: Path):
        root.mkdir(parents=True, exist_ok=True)
        self.path = root / "audit.sqlite3"
        self._lock = threading.Lock()
        with self._connect() as db:
            db.execute(
                """CREATE TABLE IF NOT EXISTS inference_audit (
                idempotency_key TEXT PRIMARY KEY, route TEXT NOT NULL, request_hash TEXT NOT NULL,
                status TEXT NOT NULL, provider TEXT NOT NULL, model TEXT NOT NULL,
                response_json TEXT NOT NULL, input_tokens INTEGER NOT NULL,
                output_tokens INTEGER NOT NULL, cost_usd REAL NOT NULL,
                latency_ms INTEGER NOT NULL, created_at_utc TEXT NOT NULL)"""
            )

    def get(self, key: str, request_hash: str) -> dict[str, Any] | None:
        with self._lock, self._connect() as db:
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
        with self._lock, self._connect() as db:
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
        with self._lock, self._connect() as db:
            value = db.execute(
                "SELECT COALESCE(SUM(cost_usd),0) FROM inference_audit WHERE route=? AND created_at_utc>=?",
                (route, day),
            ).fetchone()[0]
        return float(value)

    def _connect(self) -> sqlite3.Connection:
        return sqlite3.connect(self.path, timeout=30)
