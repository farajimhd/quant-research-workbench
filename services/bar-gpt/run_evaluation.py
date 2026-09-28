"""Standalone, CPU-first BarGPT evaluation entry point."""
from __future__ import annotations

import os
import sys
from pathlib import Path

os.environ["PYTHONDONTWRITEBYTECODE"] = "1"
sys.dont_write_bytecode = True
ROOT = Path(__file__).resolve().parent
sys.path[:0] = [str(ROOT / "src"), str(ROOT.parents[1])]

if __name__ == "__main__":
    from bar_gpt_service.evaluation import main

    main()
