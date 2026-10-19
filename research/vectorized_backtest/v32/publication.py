"""Compact atomic machine artifacts; logical JSON and digest semantics unchanged."""
import json
from pathlib import Path
from time import sleep
from uuid import uuid4

from research.vectorized_backtest.v6.torch_backtest.runtime import require_runtime


def write_json(path, value):
    path = Path(path)
    require_runtime(path.parent)
    # Validate/encode before creating a temporary artifact. Keep the standard
    # encoder's floats, arbitrary-size integers and non-finite rejection.
    raw = json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False)
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
