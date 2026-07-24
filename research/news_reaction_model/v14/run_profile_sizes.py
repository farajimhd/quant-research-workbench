from __future__ import annotations

import shlex
import sys

from research.news_reaction_model.v14.profile_sizes import main

DEFAULT_ARGS = [
    "--model-sizes", "96,192,384,576",
    "--batch-sizes", "128,256,512,1024,2048,4096",
    "--layers", "1,2,4",
    "--attention-heads", "6",
    "--max-word-tokens", "256",
    "--max-char-tokens", "512",
    "--max-numeric-tokens", "64",
    "--data-start", "2019-01-01",
    "--data-end-exclusive", "2027-01-01",
]

if __name__ == "__main__":
    args = DEFAULT_ARGS + sys.argv[1:]
    print("COMMAND python -m research.news_reaction_model.v14.profile_sizes " + " ".join(shlex.quote(value) for value in args), flush=True)
    raise SystemExit(main(args))
