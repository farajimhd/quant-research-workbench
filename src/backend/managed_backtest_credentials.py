"""Workstation-only bootstrap of provisioned Strategy 1 credentials.

This reads private operator files into process memory. It never copies secret
values into source, logs, artifacts, responses, or run metadata.
"""
from __future__ import annotations

import os
from pathlib import Path
import platform
from typing import MutableMapping


SECRET_ROOT = Path(r"D:\TradingML\secrets")
_FILES = {
    "trading_journal.env": (
        "TRADING_JOURNAL_CLICKHOUSE_URL",
        "TRADING_JOURNAL_CLICKHOUSE_USER",
        "TRADING_JOURNAL_CLICKHOUSE_PASSWORD",
    ),
    "backtest_v4_runner.env": (
        "BACKTEST_V4_RUNNER_CLICKHOUSE_URL",
        "BACKTEST_V4_RUNNER_CLICKHOUSE_USER",
        "BACKTEST_V4_RUNNER_CLICKHOUSE_PASSWORD",
    ),
}


def _private_values(path: Path, allowed: tuple[str, ...]) -> dict[str, str]:
    values: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line or line.startswith("#"):
            continue
        key, separator, value = line.partition("=")
        if not separator or key not in allowed or key in values or not value:
            raise ValueError(f"Managed Backtest credential contract is invalid: {path.name}")
        values[key] = value
    if set(values) != set(allowed):
        raise ValueError(f"Managed Backtest credential is incomplete: {path.name}")
    return values


def load_managed_backtest_credentials(
    *, environment: MutableMapping[str, str] | None = None,
    workstation: str | None = None, secret_root: Path = SECRET_ROOT,
) -> bool:
    """Load both journal principals atomically; leave other hosts unchanged."""
    if (workstation or platform.node()).upper() != "DESKTOP-SAAI85T":
        return False
    env = os.environ if environment is None else environment
    paths = {name: secret_root / name for name in _FILES}
    if not all(path.is_file() for path in paths.values()):
        return False
    collected: dict[str, str] = {}
    for name, allowed in _FILES.items():
        collected.update(_private_values(paths[name], allowed))
    for key, value in collected.items():
        if key in env and env[key] != value:
            raise ValueError("Managed Backtest credential conflicts with process environment")
    env.update(collected)
    reader = secret_root / "backtest_v3_read.env"
    if reader.is_file():
        selected = str(reader)
        if (env.get("BACKTEST_V3_READ_CREDENTIAL_FILE")
                and env["BACKTEST_V3_READ_CREDENTIAL_FILE"] != selected):
            raise ValueError("Managed Backtest read credential path conflicts")
        env["BACKTEST_V3_READ_CREDENTIAL_FILE"] = selected
    return True
