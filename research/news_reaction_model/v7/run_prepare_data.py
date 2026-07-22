from __future__ import annotations

import shlex
import sys

from research.news_reaction_model.v7.prepare_data import main

DEFAULT_ARGS = [
    "--start", "2019-01-01",
    "--end-exclusive", "2027-01-01",
    "--workers", "2",
    "--max-threads-per-query", "4",
    "--max-memory-usage", "16G",
]

if __name__ == "__main__":
    args = DEFAULT_ARGS + sys.argv[1:]
    print("COMMAND python -m research.news_reaction_model.v7.prepare_data " + " ".join(shlex.quote(value) for value in args), flush=True)
    raise SystemExit(main(args))

