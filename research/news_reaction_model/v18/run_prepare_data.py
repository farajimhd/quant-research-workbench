from __future__ import annotations

import sys

from research.news_reaction_model.v18.prepare_data import main


DEFAULTS = ["--workers", "16", "--tickers-per-query", "64"]


if __name__ == "__main__":
    args = [*DEFAULTS, *sys.argv[1:]]
    print(
        "COMMAND python -m research.news_reaction_model.v18.prepare_data "
        + " ".join(args),
        flush=True,
    )
    raise SystemExit(main(args))
