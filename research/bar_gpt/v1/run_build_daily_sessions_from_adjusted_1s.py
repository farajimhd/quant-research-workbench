from __future__ import annotations

import os
import shlex
import sys

from research.bar_gpt.v1.build_daily_sessions_from_adjusted_1s import main


DEFAULT_ARGS = {
    "--start-date": "2019-01-01",
    "--end-date": "auto",
    "--chunk-days": "31",
    "--max-threads": "16",
    "--max-memory-usage": "64G",
    "--max-bytes-before-external-group-by": "16G",
    "--progress-layout": "auto",
}


if __name__ == "__main__":
    os.environ["PYTHONDONTWRITEBYTECODE"] = "1"
    args = [item for key, value in DEFAULT_ARGS.items() for item in (key, value)] + sys.argv[1:]
    command = [sys.executable, "-B", "-m", "research.bar_gpt.v1.build_daily_sessions_from_adjusted_1s", *args]
    print("Equivalent command: " + " ".join(shlex.quote(item) for item in command), flush=True)
    raise SystemExit(main(args))
