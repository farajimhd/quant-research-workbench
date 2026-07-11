from __future__ import annotations

import shlex
import sys

from research.packed_market_model.v1.run_profile_model import main


DEFAULT_ARGS = {
    "--months": "2019-02",
    "--max-plans": "24",
    "--ticker-workers": "24",
    "--ready-queue-blocks": "16",
    "--target-origin-count-per-block": "65536",
    "--event-context-rows": "1024",
    "--future-event-guard-rows": "262144",
    "--max-threads-per-query": "4",
    "--max-memory-usage": "32G",
    "--worker-memory-limit-mib": "12288",
    "--scanner-sidecar": "",
    "--scanner-window-seconds": "900",
    "--scanner-fetch-lookback-seconds": "300",
    "--scanner-warmup-seconds": "5",
    "--scanner-background-chunk-seconds": "60",
    "--warmup-blocks": "1",
    "--profile-blocks": "4",
    "--d-model": "384",
    "--event-layers": "8",
    "--event-kernel-size": "9",
    "--head-hidden-dim": "512",
    "--learning-rate": "1e-3",
    "--amp": "",
    "--amp-dtype": "bf16",
    "--no-compile-model": "",
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
    print("COMMAND python -m research.packed_market_model.v1.run_profile_model " + " ".join(shlex.quote(item) for item in args), flush=True)
    raise SystemExit(main(args))
