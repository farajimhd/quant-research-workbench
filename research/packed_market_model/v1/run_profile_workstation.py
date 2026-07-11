from __future__ import annotations

import shlex
import sys

from research.packed_market_model.v1.run_profile_ticker_stream_loader import main


DEFAULT_ARGS = {
    "--months": "2019-02",
    "--max-blocks": "20",
    "--ticker-workers": "24",
    "--ready-queue-blocks": "8",
    "--target-origin-count-per-block": "65536",
    "--event-context-rows": "1024",
    "--future-event-guard-rows": "262144",
    "--max-threads-per-query": "4",
    "--max-memory-usage": "32G",
    "--worker-memory-limit-mib": "12288",
    "--with-model-step": "",
    "--d-model": "256",
    "--event-layers": "4",
    "--head-hidden-dim": "256",
    "--output-root": r"D:\TradingML\runtimes\packed_market_model\v1\profiles",
    "--progress-layout": "rich",
}


def _default_argv() -> list[str]:
    argv: list[str] = []
    for key, value in DEFAULT_ARGS.items():
        argv.append(key)
        if value != "":
            argv.append(str(value))
    return argv


if __name__ == "__main__":
    args = _default_argv() + sys.argv[1:]
    print("COMMAND python -m research.packed_market_model.v1.run_profile_ticker_stream_loader " + " ".join(shlex.quote(item) for item in args), flush=True)
    raise SystemExit(main(args))
