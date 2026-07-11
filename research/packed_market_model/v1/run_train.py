from __future__ import annotations

import shlex
import sys

from research.packed_market_model.v1.train import main


DEFAULT_ARGS = {
    "--data-source": "clickhouse",
    "--months": "2019-02",
    "--max-samples": "2000000",
    "--ticker-workers": "24",
    "--ready-queue-blocks": "8",
    "--target-origin-count-per-block": "65536",
    "--scanner-sidecar": "",
    "--scanner-background-chunk-seconds": "60",
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
        argv.append(key)
        if value != "":
            argv.append(str(value))
    return argv


if __name__ == "__main__":
    args = _default_argv() + sys.argv[1:]
    print("COMMAND python -m research.packed_market_model.v1.train " + " ".join(shlex.quote(item) for item in args), flush=True)
    raise SystemExit(main(args))
