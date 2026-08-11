from __future__ import annotations

import os

from research.bar_gpt.v1.summarize_offline_dataset import main


if __name__ == "__main__":
    os.environ["PYTHONDONTWRITEBYTECODE"] = "1"
    raise SystemExit(main())
