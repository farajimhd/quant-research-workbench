from __future__ import annotations

import sys

from research.news_reaction_model.v17.prepare_targets import main


DEFAULTS = ["--workers", "4"]


if __name__ == "__main__":
    args = [*DEFAULTS, *sys.argv[1:]]
    print(
        "COMMAND python -m research.news_reaction_model.v17.prepare_targets "
        + " ".join(args),
        flush=True,
    )
    raise SystemExit(main(args))
