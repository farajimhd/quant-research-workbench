from __future__ import annotations

import os
import shlex
import sys

from pipelines.market_sip.events.clickhouse_build_daily_session_bars import main
from pipelines.market_sip.events.session_bar_contract import (
    DEFAULT_DAILY_SESSION_BARS_TABLE,
    DEFAULT_DAILY_SESSION_MANIFEST_TABLE,
)


DEFAULT_ARGS: dict[str, str] = {
    "--start-date": "2019-01-01",
    "--end-date": "auto",
    "--target-table": DEFAULT_DAILY_SESSION_BARS_TABLE,
    "--manifest-table": DEFAULT_DAILY_SESSION_MANIFEST_TABLE,
    "--chunk-days": "7",
    "--max-threads": "16",
    "--max-memory-usage": "96G",
    "--max-bytes-before-external-group-by": "24G",
    "--progress-layout": "auto",
}


def default_argv() -> list[str]:
    values: list[str] = []
    for key, value in DEFAULT_ARGS.items():
        values.extend((key, value))
    return values


if __name__ == "__main__":
    os.environ["PYTHONDONTWRITEBYTECODE"] = "1"
    args = default_argv() + sys.argv[1:]
    command = [sys.executable, "-B", "-m", "pipelines.market_sip.events.clickhouse_build_daily_session_bars", *args]
    print("Equivalent command: " + " ".join(shlex.quote(item) for item in command), flush=True)
    raise SystemExit(main(args))
