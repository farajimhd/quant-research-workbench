from __future__ import annotations

import sys
from pathlib import Path


REPO_ROOT = next((parent for parent in Path(__file__).resolve().parents if (parent / "research").exists()), Path(__file__).resolve().parents[3])
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from research.masked_event_model.v21.train_model_size_sweep import main


if __name__ == "__main__":
    main()
