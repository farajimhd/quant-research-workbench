from __future__ import annotations

import shlex
import sys

from research.packed_market_model.v1.run_profile_full_modality_loader import main


DEFAULT_ARGS = {
    "--months": "2019-02",
    "--max-blocks": "4",
    "--max-plans": "24",
    "--block-sampling": "round-robin",
    "--target-origin-count-per-block": "65536",
    "--event-context-rows": "1024",
    "--future-event-guard-rows": "262144",
    "--context-workers": "8",
    "--max-threads-per-query": "4",
    "--max-memory-usage": "32G",
    "--scanner-sidecar": "",
    "--scanner-window-seconds": "900",
    "--scanner-fetch-lookback-seconds": "300",
    "--scanner-baseline-et": "09:30:00",
    "--output-root": r"D:\TradingML\runtimes\packed_market_model\v1\profiles",
    "--progress-layout": "rich",
}


def _default_argv() -> list[str]:
    argv: list[str] = []
    for key, value in DEFAULT_ARGS.items():
        if value == "":
            argv.append(key)
        else:
            argv.extend([key, str(value)])
    return argv


if __name__ == "__main__":
    args = _default_argv() + sys.argv[1:]
    print("COMMAND python -m research.packed_market_model.v1.run_profile_full_modality_loader " + " ".join(shlex.quote(item) for item in args), flush=True)
    raise SystemExit(main(args))
