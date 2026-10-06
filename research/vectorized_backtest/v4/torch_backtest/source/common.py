"""Deterministic source identity primitives, independent of RL packages."""
import hashlib
import json
from zoneinfo import ZoneInfo

NY = ZoneInfo("America/New_York")


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"),
                                    allow_nan=False).encode()).hexdigest()
