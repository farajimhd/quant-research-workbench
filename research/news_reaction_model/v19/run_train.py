from __future__ import annotations

import sys

from research.news_reaction_model.v19.train import main


if __name__ == "__main__":
    print(
        "COMMAND python -m research.news_reaction_model.v19.train "
        + " ".join(sys.argv[1:]),
        flush=True,
    )
    raise SystemExit(main(sys.argv[1:]))
