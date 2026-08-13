from __future__ import annotations

import os
import shlex
import sys

from pipelines.market_sip.events.clickhouse_build_intraday_base_bars import main
from research.bar_gpt.v2.cohort import BAR_GPT_MATERIALIZED_TICKERS_2TB


DEFAULT_ARGS: dict[str, str] = {
    "--artifact-mode": "conditions-only",
    "--start-date": "2019-01-01",
    "--end-date": "2026-08-01",
    "--resolutions": "1s",
    "--tickers": ",".join(BAR_GPT_MATERIALIZED_TICKERS_2TB),
    "--chunk-mode": "month",
    "--ticker-batch-max-events": "40000000",
    "--ticker-batch-max-tickers": "256",
    "--max-threads": "8",
    "--max-memory-usage": "48G",
    "--output-root": r"D:\TradingML\runtimes\bar_gpt\v2\build_conditions_1s",
    "--progress-layout": "auto",
}

DEFAULT_FLAGS: tuple[str, ...] = ("--replace-existing",)


def default_argv() -> list[str]:
    return [item for pair in DEFAULT_ARGS.items() for item in pair] + list(DEFAULT_FLAGS)


if __name__ == "__main__":
    os.environ["PYTHONDONTWRITEBYTECODE"] = "1"
    args = default_argv() + sys.argv[1:]
    command = [sys.executable, "-B", "-m", "pipelines.market_sip.events.clickhouse_build_intraday_base_bars", *args]
    print("Equivalent command: " + " ".join(shlex.quote(item) for item in command), flush=True)
    raise SystemExit(main(args))
