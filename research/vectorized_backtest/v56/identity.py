"""Bind the unchanged V42 dependency tree and V56 experiment sources."""
import hashlib
import json
from pathlib import Path


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':'),
                                     allow_nan=False).encode()).hexdigest()


def source_hash():
    from research.vectorized_backtest.v42.run_search import source_hash as base_hash
    h = hashlib.sha256(base_hash().encode())
    for path in sorted(Path(__file__).parent.glob('*.py')):
        h.update(path.name.encode())
        h.update(path.read_bytes().replace(b'\r\n', b'\n'))
    return h.hexdigest()
