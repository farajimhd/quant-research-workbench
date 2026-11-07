"""Direct launcher for the independently reviewable shard engineering package."""

import os
from pathlib import Path
import sys
import subprocess

os.environ["PYTHONDONTWRITEBYTECODE"] = "1"
os.environ.setdefault("POLARS_MAX_THREADS", "2")
sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from research.bar_gpt.v5.run_balanced_shards import main

if __name__ == "__main__":
    print('Equivalent: '+subprocess.list2cmdline([sys.executable,'-B','-u',str(Path(__file__).resolve()),*sys.argv[1:]]),flush=True)
    raise SystemExit(main())
