"""Bind the unchanged V42 dependency tree and V60 experiment sources."""
import hashlib
import json
from pathlib import Path


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':'),
                                     allow_nan=False).encode()).hexdigest()


def source_hash():
    h = hashlib.sha256(b'v60-self-contained-v1')
    root=Path(__file__).parent
    for path in sorted(p for p in root.rglob('*') if p.suffix in ('.py','.json')):
        h.update(path.relative_to(root).as_posix().encode())
        h.update(path.read_bytes().replace(b'\r\n', b'\n'))
    return h.hexdigest()
