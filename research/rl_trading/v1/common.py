"""Shared, database-free provenance and time helpers for RL labels."""
from contextlib import contextmanager
from datetime import datetime, time
from hashlib import sha256
import json
import os
from pathlib import Path
from zoneinfo import ZoneInfo

NY = ZoneInfo('America/New_York')


def digest(value):
    return sha256(json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()).hexdigest()


def bounds(day):
    return tuple(int(datetime.combine(day, time(hour), NY).timestamp() * 1_000_000) for hour in (4, 20))


def file_hash(path):
    result = sha256()
    with Path(path).open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            result.update(block)
    return result.hexdigest()


@contextmanager
def exclusive(path):
    with Path(path).open('a+b') as stream:
        stream.seek(0)
        stream.write(b'0')
        stream.flush()
        stream.seek(0)
        if os.name == 'nt':
            import msvcrt
            msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl
            fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
        yield


def verified(folder, ready):
    for name, expected in ready['files'].items():
        path = Path(folder) / name
        if not path.is_file() or file_hash(path) != expected:
            raise ValueError('Checkpoint integrity failure: ' + str(path))
