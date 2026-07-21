from __future__ import annotations

import shlex
import sys

from research.news_reaction_model.v1.train import main

DEFAULT_ARGS = [
    "--train-start", "2019-01-01", "--train-end-exclusive", "2026-01-01",
    "--validation-start", "2026-01-01", "--validation-end-exclusive", "2027-01-01",
    "--batch-size", "512", "--loader-workers", "2", "--prefetch-batches", "4",
    "--d-model", "256", "--hidden-dim", "256", "--layers", "2",
]

if __name__ == "__main__":
    args = DEFAULT_ARGS + sys.argv[1:]
    print("COMMAND python -m research.news_reaction_model.v1.train " + " ".join(shlex.quote(value) for value in args), flush=True)
    raise SystemExit(main(args))
