from __future__ import annotations

import base64
from pathlib import Path

from research.mlops.paths import MLOpsPathConfig

from .fresh_acceptance_v5_manual_labels import COMPACT_ROWS
from .run_record_fresh_acceptance import main as record_main


def main() -> int:
    runtime_root = (
        MLOpsPathConfig.from_env().runtimes_root
        / "text_intelligence"
        / "semantic_calibration_v1"
        / "news_acceptance_500_v5"
    )
    payload = base64.b64encode(COMPACT_ROWS.encode("utf-8")).decode("ascii")
    return record_main([
        "--runtime-root",
        str(runtime_root),
        "--input-base64",
        payload,
    ])


if __name__ == "__main__":
    raise SystemExit(main())
