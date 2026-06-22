from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from market_ai.service import run_synthetic_smoke


if __name__ == "__main__":
    result = run_synthetic_smoke()
    print(
        "market-ai smoke ok "
        f"encoder_batches={result['encoder_batches']} "
        f"temporal_batches={result['temporal_batches']} "
        f"temporal_samples={result['temporal_samples']}",
        flush=True,
    )
