from __future__ import annotations

import shlex
import sys

from research.news_reaction_model.v12.fit_diagnostic import main


DEFAULT_ARGS = [
    "--checkpoint",
    (
        r"D:\TradingML\runtimes\news-reaction-model\v12\train"
        r"\news-v12-opportunity-openai-stock-state-d384-l4-b2048"
        r"\checkpoints\checkpoint_best_val.pt"
    ),
    "--train-start",
    "2019-01-01",
    "--train-end-exclusive",
    "2026-01-01",
    "--validation-start",
    "2026-01-01",
    "--validation-end-exclusive",
    "2027-01-01",
]


if __name__ == "__main__":
    args = DEFAULT_ARGS + sys.argv[1:]
    print(
        "COMMAND python -m research.news_reaction_model.v12.fit_diagnostic "
        + " ".join(shlex.quote(value) for value in args),
        flush=True,
    )
    raise SystemExit(main(args))
