from __future__ import annotations

import gzip
import hashlib
import json
import os
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from .cache import RawBar


SNAPSHOT_SCHEMA_VERSION = 2
NEW_YORK = ZoneInfo("America/New_York")


class WarmSnapshotStore:
    """Same-session, contract-bound calendar context for restart-safe warm-up."""

    def __init__(self, root: Path, contract_hash: str) -> None:
        self.root = root.resolve()
        self.contract_hash = contract_hash.strip().lower()

    def load(self, ticker: str, origin_us: int, source_revision: dict[str, Any]) -> list[RawBar]:
        path = self._path(ticker, origin_us)
        if not path.is_file():
            return []
        try:
            with gzip.open(path, "rt", encoding="utf-8") as handle:
                payload = json.load(handle)
            rows = payload.get("rows")
            expected = str(payload.get("payload_sha256") or "")
            if (
                int(payload.get("schema_version") or 0) != SNAPSHOT_SCHEMA_VERSION
                or str(payload.get("contract_hash") or "").lower() != self.contract_hash
                or str(payload.get("ticker") or "").upper() != ticker.upper()
                or str(payload.get("session_date") or "") != self._session_date(origin_us)
                or payload.get("source_revision") != source_revision
                or not isinstance(rows, list)
                or expected != _rows_hash(rows)
            ):
                return []
            result = [_decode_row(row) for row in rows]
            if any(row.available_at_us > origin_us for row in result):
                return []
            return result
        except (OSError, ValueError, TypeError, json.JSONDecodeError):
            return []

    def save(
        self, ticker: str, origin_us: int, rows: list[RawBar], source_revision: dict[str, Any]
    ) -> Path | None:
        calendar = [row for row in rows if row.view in {"1D", "1W", "1MO"} and row.available_at_us <= origin_us]
        if not calendar:
            return None
        encoded = [_encode_row(row) for row in calendar]
        payload = {
            "schema_version": SNAPSHOT_SCHEMA_VERSION,
            "contract_hash": self.contract_hash,
            "ticker": ticker.upper(),
            "session_date": self._session_date(origin_us),
            "created_at_utc": datetime.now(UTC).isoformat(),
            "source_frontier_us": max(row.available_at_us for row in calendar),
            "source_revision": source_revision,
            "rows": encoded,
            "payload_sha256": _rows_hash(encoded),
        }
        path = self._path(ticker, origin_us)
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(path.suffix + f".{os.getpid()}.tmp")
        with gzip.open(temporary, "wt", encoding="utf-8") as handle:
            json.dump(payload, handle, separators=(",", ":"), allow_nan=False)
        os.replace(temporary, path)
        return path

    def _path(self, ticker: str, origin_us: int) -> Path:
        return self.root / self.contract_hash / self._session_date(origin_us) / f"{ticker.upper()}.json.gz"

    @staticmethod
    def _session_date(origin_us: int) -> str:
        return datetime.fromtimestamp(origin_us / 1_000_000, tz=UTC).astimezone(NEW_YORK).date().isoformat()


def _encode_row(row: RawBar) -> dict[str, Any]:
    return {
        "ticker": row.ticker,
        "view": row.view,
        "bar_start_us": row.bar_start_us,
        "bar_end_us": row.bar_end_us,
        "available_at_us": row.available_at_us,
        "values": list(row.values),
        "revision": row.revision,
        "source": row.source,
        "source_revision": row.source_revision,
    }


def _decode_row(row: dict[str, Any]) -> RawBar:
    return RawBar(
        ticker=str(row["ticker"]).upper(),
        view=str(row["view"]),
        bar_start_us=int(row["bar_start_us"]),
        bar_end_us=int(row["bar_end_us"]),
        available_at_us=int(row["available_at_us"]),
        values=tuple(float(value) for value in row["values"]),
        revision=int(row["revision"]),
        source=str(row["source"]),
        source_revision=str(row.get("source_revision") or ""),
    )


def _rows_hash(rows: list[dict[str, Any]]) -> str:
    encoded = json.dumps(rows, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()
