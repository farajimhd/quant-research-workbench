from __future__ import annotations

import shlex
import sys

from research.packed_market_model.v1.train import main


DEFAULT_ARGS = {
    "--cache-root": r"D:\market-data\prepared\packed_market_block_cache\packed_events_daily_index_2019-02",
    "--months": "2019-02",
    "--max-samples": "2000000",
    "--d-model": "384",
    "--event-layers": "8",
    "--learning-rate": "1e-3",
    "--scheduler": "cosine",
    "--scheduler-eta-min": "1e-6",
    "--scheduler-cycle-samples": "1024000",
}


def _default_argv() -> list[str]:
    argv: list[str] = []
    for key, value in DEFAULT_ARGS.items():
        argv.extend([key, str(value)])
    return argv


if __name__ == "__main__":
    args = _default_argv() + sys.argv[1:]
    print("COMMAND python -m research.packed_market_model.v1.train " + " ".join(shlex.quote(item) for item in args), flush=True)
    raise SystemExit(main(args))
