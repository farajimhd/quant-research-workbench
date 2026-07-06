from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
SRC = ROOT / "src"
REPO_ROOT = ROOT.parents[1]
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

if __name__ == "__main__":
    raise SystemExit(
        "Market AI Service is intentionally disabled. It is TBD until the final "
        "trained ML model, multimodal cache contract, and prediction publishing "
        "contract are selected."
    )
