from __future__ import annotations

import sys

from research.news_reaction_model.v18.evaluate import main


if __name__ == "__main__":
    print(
        "COMMAND python -m research.news_reaction_model.v18.evaluate "
        + " ".join(sys.argv[1:]),
        flush=True,
    )
    raise SystemExit(main(sys.argv[1:]))
