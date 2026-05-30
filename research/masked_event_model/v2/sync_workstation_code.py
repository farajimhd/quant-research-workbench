from __future__ import annotations

import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[3]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from research.mlops.sync import sync_version_code


if __name__ == "__main__":
    destination = sync_version_code(repo_root=REPO_ROOT, model_family="masked_event_model", version="v2")
    print(f"Synced masked_event_model/v2 code to {destination}")
