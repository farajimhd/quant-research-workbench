from __future__ import annotations

import shlex
import sys

from research.news_reaction_model.v2.evaluate import main

DEFAULT_ARGS = [
    "--checkpoint", r"D:\TradingML\runtimes\news-reaction-model\v2\train\news-v2-regression-d384-l4-b2048\checkpoints\checkpoint_best_val.pt",
    "--start", "2026-01-01", "--end-exclusive", "2027-01-01",
    "--flat-z", "0.25,0.5,1.0", "--cost-bps", "0,2,5,10",
    "--notional", "10000",
]

if __name__ == "__main__":
    args = DEFAULT_ARGS + sys.argv[1:]
    print("COMMAND python -m research.news_reaction_model.v2.evaluate " + " ".join(shlex.quote(value) for value in args), flush=True)
    raise SystemExit(main(args))
