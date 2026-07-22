from __future__ import annotations

import shlex
import sys

from research.news_reaction_model.v2.compare_evaluation import main

DEFAULT_ARGS = [
    "--v1-checkpoint", r"D:\TradingML\runtimes\news-reaction-model\v1\train\news-v1-d384-l4-b2048\checkpoints\checkpoint_best_val.pt",
    "--v2-predictions", r"D:\TradingML\runtimes\news-reaction-model\v2\train\news-v2-regression-d384-l4-b2048\evaluation\evaluation_predictions.jsonl.gz",
    "--output", r"D:\TradingML\runtimes\news-reaction-model\v2\train\news-v2-regression-d384-l4-b2048\evaluation\model_comparison_one_share.json",
    "--start", "2026-01-01", "--end-exclusive", "2027-01-01", "--v2-flat-z", "0.25",
]

if __name__ == "__main__":
    args = DEFAULT_ARGS + sys.argv[1:]
    print("COMMAND python -m research.news_reaction_model.v2.compare_evaluation " + " ".join(shlex.quote(value) for value in args), flush=True)
    raise SystemExit(main(args))
