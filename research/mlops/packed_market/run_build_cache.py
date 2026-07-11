from __future__ import annotations

import shlex
import sys

from research.mlops.packed_market.builder import main


DEFAULT_ARGS = {
    "--source-cache-root": r"D:\market-data\prepared\daily_index_streaming_cache\events_daily_index_2019-02",
    "--output-root": r"D:\market-data\prepared\packed_market_block_cache",
    "--cache-id": "packed_events_daily_index_2019-02",
    "--months": "2019-02",
    "--workers": "16",
}


def _default_argv() -> list[str]:
    argv: list[str] = []
    for key, value in DEFAULT_ARGS.items():
        argv.extend([key, str(value)])
    return argv


if __name__ == "__main__":
    args = _default_argv() + sys.argv[1:]
    print("COMMAND python -m research.mlops.packed_market.builder " + " ".join(shlex.quote(item) for item in args), flush=True)
    raise SystemExit(main(args))
