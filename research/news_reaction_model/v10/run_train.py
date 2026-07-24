from __future__ import annotations

import shlex
import sys

from research.news_reaction_model.v10.train import main

DEFAULT_ARGS = [
    "--train-start", "2019-01-01", "--train-end-exclusive", "2026-01-01",
    "--validation-start", "2026-01-01", "--validation-end-exclusive", "2027-01-01",
    "--batch-size", "2048", "--loader-workers", "2", "--prefetch-batches", "4",
    "--shuffle-buffer-articles", "32768",
    "--d-model", "384", "--hidden-dim", "384", "--layers", "4",
    "--epochs", "50", "--learning-rate", "3e-4",
    "--scheduler", "cosine", "--scheduler-restarts", "49",
    "--scheduler-cycle-decay", "0.98", "--scheduler-eta-min", "1e-6",
    "--run-name",
    "news-v10-opportunity-openai-stock-state-time-balanced-d384-l4-b2048-e50-cosine-r49-gamma098",
]

if __name__ == "__main__":
    args = DEFAULT_ARGS + sys.argv[1:]
    print("COMMAND python -m research.news_reaction_model.v10.train " + " ".join(shlex.quote(value) for value in args), flush=True)
    raise SystemExit(main(args))
