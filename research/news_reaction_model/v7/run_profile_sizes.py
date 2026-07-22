from __future__ import annotations

import shlex
import sys

from research.news_reaction_model.v7.profile_sizes import main

DEFAULT_ARGS = [
    "--model-sizes", "128,192,256,384",
    "--batch-sizes", "512,1024,2048,4096,8192,16384,32768",
    "--layers", "1,2,4",
    "--data-start", "2019-01-01",
    "--data-end-exclusive", "2027-01-01",
]

if __name__ == "__main__":
    args = DEFAULT_ARGS + sys.argv[1:]
    print("COMMAND python -m research.news_reaction_model.v7.profile_sizes " + " ".join(shlex.quote(value) for value in args), flush=True)
    raise SystemExit(main(args))

