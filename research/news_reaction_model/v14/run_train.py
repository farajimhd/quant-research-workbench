from __future__ import annotations

import shlex
import sys

from research.news_reaction_model.v14.train import main

DEFAULT_ARGS = [
    "--train-start", "2019-01-01", "--train-end-exclusive", "2026-01-01",
    "--validation-start", "2026-01-01", "--validation-end-exclusive", "2027-01-01",
    "--batch-size", "2048", "--loader-workers", "2", "--prefetch-batches", "4",
    "--shuffle-buffer-articles", "32768",
    "--max-word-tokens", "256", "--max-char-tokens", "512", "--max-numeric-tokens", "64",
    "--d-model", "384", "--hidden-dim", "384", "--layers", "4", "--attention-heads", "6",
    "--epochs", "50", "--learning-rate", "3e-4",
    "--scheduler", "cosine", "--scheduler-restarts", "49",
    "--scheduler-cycle-decay", "0.98", "--scheduler-eta-min", "1e-6",
    "--run-name",
    "news-v14-opportunity-tfidf-token-transformer-d384-a6-l4-w256-c512-n64-b2048-e50-cosine-r49-gamma098",
]

if __name__ == "__main__":
    args = DEFAULT_ARGS + sys.argv[1:]
    print("COMMAND python -m research.news_reaction_model.v14.train " + " ".join(shlex.quote(value) for value in args), flush=True)
    raise SystemExit(main(args))
