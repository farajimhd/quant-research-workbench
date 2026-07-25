from __future__ import annotations

import shlex
import sys

from research.news_reaction_model.v16.prepare_data import main


DEFAULT_ARGS = [
    "--prepared-root",
    r"D:\market-data\prepared\news_reaction_model\v16\market_attention_v1",
    "--query-batch-articles",
    "2048",
    "--max-threads-per-query",
    "4",
    "--max-memory-usage",
    "16G",
    "--market-max-threads",
    "16",
    "--market-max-memory-usage",
    "64G",
]


if __name__ == "__main__":
    args = DEFAULT_ARGS + sys.argv[1:]
    print(
        "COMMAND python -m research.news_reaction_model.v16.prepare_data "
        + " ".join(shlex.quote(value) for value in args),
        flush=True,
    )
    raise SystemExit(main(args))
