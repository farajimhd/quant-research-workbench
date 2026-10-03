"""Shared deterministic scalar seal for producer and SELECT-only consumer."""
from hashlib import sha256
import json
import struct


def scalar_hash(rows):
    digest = sha256()
    for row in rows:
        values = [(key, struct.pack("<d", value).hex() if type(value) is float else value)
                  for key, value in sorted(row.items())]
        digest.update(json.dumps(values, separators=(",", ":"), allow_nan=False).encode())
        digest.update(b"\n")
    return digest.hexdigest()
