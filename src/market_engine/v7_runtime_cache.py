"""Bounded, process-private spill storage for lossless QMD V7 working state.

This is derived scratch storage, never a replacement for canonical source data.
Each worker gets a fresh namespace; no stale cache can survive a source restart.
"""
from contextlib import closing
import hashlib
import json
from pathlib import Path
import sqlite3
from tempfile import TemporaryDirectory
import zlib
import weakref


def _close(db, directory):
    db.close()
    directory.cleanup()


class RuntimeCache:
    def __init__(self, root, *, max_bytes=32 * 1024**3):
        root = Path(root)
        root.mkdir(parents=True, exist_ok=True)
        self.directory = TemporaryDirectory(prefix="v7-worker-", dir=root)
        self.path = Path(self.directory.name) / "cache.sqlite3"
        self.max_bytes = max_bytes
        self.db = sqlite3.connect(self.path, check_same_thread=False)
        self._finalizer = weakref.finalize(self, _close, self.db, self.directory)
        # This namespace is never recovered after process death. Transaction
        # rollback is useful, crash durability is not: canonical inputs and
        # the trading journal retain their independent durability contracts.
        self.db.execute("PRAGMA journal_mode=MEMORY")
        self.db.execute("PRAGMA synchronous=OFF")
        self.db.execute("PRAGMA cache_size=-32768")
        self.db.executescript("""
            CREATE TABLE sources (identity TEXT PRIMARY KEY, metadata TEXT NOT NULL);
            CREATE TABLE bars (identity TEXT, t REAL, o REAL, h REAL, l REAL, c REAL, v REAL,
                PRIMARY KEY(identity,t)) WITHOUT ROWID;
            CREATE TABLE states (identity TEXT PRIMARY KEY, payload BLOB, sha256 TEXT);
        """)
        # SQLite fails closed at the budget; it must never evict source/state.
        page_size = self.db.execute("PRAGMA page_size").fetchone()[0]
        self.db.execute(f"PRAGMA max_page_count={max(16, max_bytes // page_size)}")
        self.metrics = dict(source_loads=0, source_hits=0, state_spills=0, state_restores=0)

    @staticmethod
    def key(identity):
        return json.dumps(identity, separators=(",", ":"))

    def source(self, identity, start, end):
        key = self.key(identity)
        row = self.db.execute("SELECT metadata FROM sources WHERE identity=?", (key,)).fetchone()
        if row is None:
            return None
        with closing(self.db.execute("SELECT t,o,h,l,c,v FROM bars WHERE identity=? AND t>? AND t<=? ORDER BY t", (key, start, end))) as cursor:
            bars = [dict(zip(("t", "open", "high", "low", "close", "volume"), row)) for row in cursor]
        self.metrics["source_hits"] += 1
        return bars, json.loads(row[0])

    def save_source(self, identity, bars, metadata):
        key = self.key(identity)
        with self.db:
            self.db.executemany("INSERT INTO bars VALUES (?,?,?,?,?,?,?)",
                ((key, *(bar[k] for k in ("t", "open", "high", "low", "close", "volume"))) for bar in bars))
            self.db.execute("INSERT INTO sources VALUES (?,?)", (key, json.dumps(metadata, allow_nan=False)))
        self.metrics["source_loads"] += 1

    def save_state(self, identity, value):
        raw = json.dumps(value, separators=(",", ":"), allow_nan=False).encode()
        with self.db:
            self.db.execute("INSERT OR REPLACE INTO states VALUES (?,?,?)",
                (self.key(identity), zlib.compress(raw), hashlib.sha256(raw).hexdigest()))
        self.metrics["state_spills"] += 1

    def take_state(self, identity):
        key = self.key(identity)
        row = self.db.execute("SELECT payload,sha256 FROM states WHERE identity=?", (key,)).fetchone()
        if row is None:
            return None
        raw = zlib.decompress(row[0])
        if hashlib.sha256(raw).hexdigest() != row[1]:
            raise ValueError("V7 spilled state integrity mismatch")
        value = json.loads(raw)
        with self.db:
            self.db.execute("DELETE FROM states WHERE identity=?", (key,))
        self.metrics["state_restores"] += 1
        return value

    def close(self):
        self._finalizer()
