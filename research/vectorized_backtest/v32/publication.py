"""Compact atomic machine artifacts; logical JSON and digest semantics unchanged."""
import json
from hashlib import sha256
from pathlib import Path
from time import sleep
from uuid import uuid4

from research.vectorized_backtest.v6.torch_backtest.runtime import require_runtime


def write_json(path, value):
    # Validate/encode before creating a temporary artifact. Keep the standard
    # encoder's floats, arbitrary-size integers and non-finite rejection.
    raw = json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False)
    _write_text(path, raw)


def write_checkpoint(path, contract, state):
    # State is the large component. Encode it once for both its exact canonical
    # digest and the enclosing checkpoint, rather than walking it twice.
    raw = json.dumps(state, sort_keys=True, separators=(',', ':'), allow_nan=False)
    state_hash = sha256(raw.encode()).hexdigest()
    header = json.dumps(contract, sort_keys=True, separators=(',', ':'), allow_nan=False)
    _write_text(path, '{"contract":' + header + ',"state":' + raw + ',"state_sha256":"' + state_hash + '"}')


def _write_text(path, raw):
    path = Path(path)
    require_runtime(path.parent)
    temporary = path.with_name(path.name + '.' + uuid4().hex + '.tmp')
    try:
        temporary.write_text(raw, encoding='utf-8')
        for attempt in range(12):
            try:
                temporary.replace(path)
                return
            except PermissionError as error:
                if getattr(error, 'winerror', None) not in (5, 32) or attempt == 11:
                    raise
                sleep(min(.02 * 2**attempt, .2))
    finally:
        temporary.unlink(missing_ok=True)
