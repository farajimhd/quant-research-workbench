from __future__ import annotations

import sys

from research.news_reaction_model.v17.train import main


DEFAULTS = [
    "--epochs", "50",
    "--batch-size", "2048",
    "--learning-rate", "0.0003",
]


if __name__ == "__main__":
    args = [*DEFAULTS, *sys.argv[1:]]
    print(
        "COMMAND python -m research.news_reaction_model.v17.train "
        + " ".join(args),
        flush=True,
    )
    raise SystemExit(main(args))
