from __future__ import annotations

import os

from research.bar_gpt.v1.benchmark_offline_loader import main


if __name__ == "__main__":
    os.environ["PYTHONDONTWRITEBYTECODE"] = "1"
    raise SystemExit(main())
