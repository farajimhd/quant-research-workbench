"""Load the provisioned live V4 journal credential into workstation memory.

This never creates a credential, writes a file, or exposes secret values in a
response. The live service remains gated by its separate recovery contract.
"""
from __future__ import annotations

import os
from pathlib import Path
import platform
from typing import MutableMapping


SECRET_PATH = Path(r"D:\TradingML\secrets\strategy_one_live_v4_runner.env")
_KEYS = frozenset({
    "STRATEGY_ONE_LIVE_V4_CLICKHOUSE_URL",
    "STRATEGY_ONE_LIVE_V4_CLICKHOUSE_USER",
    "STRATEGY_ONE_LIVE_V4_CLICKHOUSE_PASSWORD",
})


def load_managed_live_v4_credentials(
    *, environment: MutableMapping[str, str] | None = None,
    workstation: str | None = None, secret_path: Path = SECRET_PATH,
) -> bool:
    """Load one complete private file atomically, without overriding an env secret."""
    if (workstation or platform.node()).upper() != "DESKTOP-SAAI85T":
        return False
    if not secret_path.is_file():
        return False
    values: dict[str, str] = {}
    for line in secret_path.read_text(encoding="utf-8").splitlines():
        if not line or line.startswith("#"):
            continue
        key, separator, value = line.partition("=")
        if not separator or key not in _KEYS or key in values or not value:
            raise ValueError("Managed live V4 credential contract is invalid")
        values[key] = value
    if set(values) != _KEYS:
        raise ValueError("Managed live V4 credential is incomplete")
    env = os.environ if environment is None else environment
    if any(key in env and env[key] != value for key, value in values.items()):
        raise ValueError("Managed live V4 credential conflicts with process environment")
    env.update(values)
    return True
