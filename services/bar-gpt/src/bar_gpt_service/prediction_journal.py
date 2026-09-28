"""Serialized, immediately flushed JSONL prediction journal with daily rotation."""
from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from threading import RLock
from typing import Any, TextIO


class PredictionJournal:
    def __init__(self, root: Path) -> None:
        self.root = root
        self._lock = RLock()
        self._handle: TextIO | None = None
        self._day = ""
        self._closed = False

    def append(self, prediction: dict[str, Any]) -> None:
        payload = json.dumps(prediction, separators=(",", ":"), allow_nan=False) + "\n"
        with self._lock:
            if self._closed:
                raise RuntimeError("prediction journal is closed")
            day = datetime.now(UTC).date().isoformat()
            if self._handle is None or day != self._day:
                if self._handle is not None:
                    self._handle.close()
                    self._handle = None
                self.root.mkdir(parents=True, exist_ok=True)
                self._handle = (self.root / f"{day}.jsonl").open("a", encoding="utf-8")
                self._day = day
            self._handle.write(payload)
            # Match the former per-record close's buffer visibility. Neither
            # implementation promises fsync/power-loss durability.
            self._handle.flush()

    def close(self) -> None:
        with self._lock:
            self._closed = True
            if self._handle is not None:
                self._handle.close()
                self._handle = None
