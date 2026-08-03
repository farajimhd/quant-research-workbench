from __future__ import annotations

import sys

from .run_record_fresh_acceptance import main


if __name__ == "__main__":
    default_root = (
        "D:/TradingML/runtimes/text_intelligence/semantic_calibration_v1/"
        "news_acceptance_100_v2"
    )
    args = sys.argv[1:]
    if "--runtime-root" not in args:
        args = ["--runtime-root", default_root, *args]
    raise SystemExit(main(args))
