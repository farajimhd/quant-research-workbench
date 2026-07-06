from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

if __name__ == "__main__":
    raise SystemExit(
        "Market AI smoke tests are disabled until the final trained model and "
        "runtime contract are selected."
    )
