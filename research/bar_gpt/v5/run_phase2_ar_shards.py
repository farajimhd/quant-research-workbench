"""Direct Python launcher for the new monthly4096-origin extractor."""

import os
from pathlib import Path
import subprocess
import sys

os.environ["PYTHONDONTWRITEBYTECODE"] = "1"
os.environ.setdefault("POLARS_MAX_THREADS", "2")
os.environ.setdefault("OMP_NUM_THREADS", "1")
if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from research.bar_gpt.v5.phase2_ar_shards.run import main

if __name__ == "__main__":
    print(
        subprocess.list2cmdline(
            [
                sys.executable,
                "-B",
                "-m",
                "research.bar_gpt.v5.phase2_ar_shards.run",
                *sys.argv[1:],
            ]
        ),
        flush=True,
    )
    raise SystemExit(main())
