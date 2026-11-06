"""Direct launcher: bounded profiling by default; training requires --mode train."""

import os
from pathlib import Path
import sys

os.environ["PYTHONDONTWRITEBYTECODE"] = "1"
os.environ.setdefault("OMP_NUM_THREADS", "1")
os.environ.setdefault("MKL_NUM_THREADS", "1")
sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

if __name__ == "__main__":
    from research.bar_gpt.v5.run_v1 import main
    print("Equivalent: python -B -u -m research.bar_gpt.v5.run_v1 " + " ".join(sys.argv[1:]), flush=True)
    raise SystemExit(main())
