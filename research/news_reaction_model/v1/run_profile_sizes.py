from __future__ import annotations

import shlex
import sys

from research.news_reaction_model.v1.profile_sizes import main

DEFAULT_ARGS = ["--model-sizes", "128,192,256,384", "--batch-sizes", "128,256,512,1024", "--layers", "1,2,4"]

if __name__ == "__main__":
    args = DEFAULT_ARGS + sys.argv[1:]
    print("COMMAND python -m research.news_reaction_model.v1.profile_sizes " + " ".join(shlex.quote(value) for value in args), flush=True)
    raise SystemExit(main(args))
