from __future__ import annotations

import shlex
import sys

from research.news_reaction_model.v8.evaluate import main

DEFAULT_ARGS = [
    "--checkpoint",
    r"D:\TradingML\runtimes\news-reaction-model\v8\train\news-v8-openai-stock-state-d384-l4-b2048\checkpoints\checkpoint_best_val.pt",
    "--start", "2026-01-01",
    "--end-exclusive", "2027-01-01",
]

if __name__ == "__main__":
    args = DEFAULT_ARGS + sys.argv[1:]
    print(
        "COMMAND python -m research.news_reaction_model.v8.evaluate "
        + " ".join(shlex.quote(value) for value in args),
        flush=True,
    )
    raise SystemExit(main(args))

