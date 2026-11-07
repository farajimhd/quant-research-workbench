"""Launch the frozen-plan ordinal/coarse campaign without writing bytecode."""

import os
from pathlib import Path
import sys

os.environ["PYTHONDONTWRITEBYTECODE"] = "1"
os.environ.setdefault("POLARS_MAX_THREADS", "2")
sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from research.bar_gpt.v5.shards.balanced_build import main

if __name__ == "__main__":
    print(
        "Equivalent: python -B -u -m research.bar_gpt.v5.shards.balanced_build "
        + " ".join(sys.argv[1:]),
        flush=True,
    )
    raise SystemExit(main())
