from __future__ import annotations

import os
import shlex
import sys

from research.bar_gpt.v3.audit_contract import main


DEFAULT_ARGS: tuple[str, ...] = (
    "--start-date", "2025-10-01",
    "--end-date", "2026-01-01",
    "--tickers", "MSFT,AMD,INTC,JPM",
    "--batches", "16",
    "--progress-layout", "auto",
)


if __name__ == "__main__":
    os.environ["PYTHONDONTWRITEBYTECODE"] = "1"
    args = [*DEFAULT_ARGS, *sys.argv[1:]]
    command = [sys.executable, "-B", "-m", "research.bar_gpt.v3.audit_contract", *args]
    print("Equivalent command: " + " ".join(shlex.quote(item) for item in command), flush=True)
    raise SystemExit(main(args))
