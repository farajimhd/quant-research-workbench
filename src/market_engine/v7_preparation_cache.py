"""Local, bounded derived-artifact cache; source files remain authoritative.

Certificates bind every observed dependency (including absent publications).
Hits verify dependency identities and payload checksums. Mutable playback state
must never be saved here: callers receive a freshly deserialized value.
"""
from contextlib import contextmanager
from contextvars import ContextVar
import hashlib
import json
from pathlib import Path
import pickle
import sqlite3
import zlib

_dependencies = ContextVar('v7_preparation_dependencies', default=None)


def identity(path):
    try:
        s = Path(path).stat()
        return [s.st_size, s.st_mtime_ns, s.st_ctime_ns, s.st_ino]
    except FileNotFoundError:
        return None


def watch(path):
    # Catalog roots are already absolute. Do not resolve every UNC dependency
    # again: on Windows that adds remote filesystem requests before stat().
    path = str(Path(path).absolute())
    observed = identity(path)
    dependencies = _dependencies.get()
    if dependencies is not None:
        old = dependencies.setdefault(path, observed)
        if old != observed:
            raise ValueError('V7 authority changed during preparation: ' + path)
    return observed


@contextmanager
def dependencies():
    value = {}
    token = _dependencies.set(value)
    try:
        yield value
    finally:
        _dependencies.reset(token)


class ArtifactCache:
    def __init__(self, root, max_bytes=2 * 1024**3):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self.path = self.root / 'artifacts.sqlite3'
        self.max_bytes = max_bytes
        with self.connect() as db:
            db.execute('CREATE TABLE IF NOT EXISTS artifacts (key TEXT PRIMARY KEY, dependencies TEXT NOT NULL, checksum TEXT NOT NULL, payload BLOB NOT NULL, used REAL NOT NULL)')

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.path, timeout=60)
        try:
            with db:
                yield db
        finally:
            db.close()

    @staticmethod
    def key(value):
        return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':')).encode()).hexdigest()

    def get(self, key):
        with self.connect() as db:
            row = db.execute('SELECT dependencies,checksum,payload FROM artifacts WHERE key=?', (key,)).fetchone()
        if row is None:
            return None
        deps, checksum, raw = row
        if hashlib.sha256(deps.encode() + raw).hexdigest() != checksum:
            raise ValueError('V7 preparation cache checksum mismatch')
        dependencies = json.loads(deps)
        for path, expected in dependencies.items():
            if watch(path) != expected:
                return None
        # Locally generated cache only, never a remote/untrusted pickle.
        compressed = raw.startswith(b'V7Z1')
        value = pickle.loads(zlib.decompress(raw[4:]) if compressed else raw)
        if not compressed:
            self.put(key, value, dependencies)
        return value

    def put(self, key, value, deps):
        if any(identity(path) != expected for path, expected in deps.items()):
            raise ValueError('V7 authority changed during preparation')
        # Disk budget is separate from the worker's unchanged RSS budget.
        # Compress repetitive books so a full session fits without FIFO churn.
        raw = b'V7Z1' + zlib.compress(pickle.dumps(value, protocol=5), level=1)
        encoded = json.dumps(deps, sort_keys=True)
        if len(raw) > self.max_bytes:
            return
        checksum = hashlib.sha256(encoded.encode() + raw).hexdigest()
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            db.execute('DELETE FROM artifacts WHERE key=?', (key,))
            size = db.execute('SELECT coalesce(sum(length(payload)),0) FROM artifacts').fetchone()[0]
            while size + len(raw) > self.max_bytes:
                oldest = db.execute('SELECT key,length(payload) FROM artifacts ORDER BY used LIMIT 1').fetchone()
                db.execute('DELETE FROM artifacts WHERE key=?', (oldest[0],))
                size -= oldest[1]
            db.execute("INSERT INTO artifacts VALUES (?,?,?,?,julianday('now'))", (key, encoded, checksum, raw))
