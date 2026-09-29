"""Direct workstation/laptop entry point for the V6 session compiler."""
from __future__ import annotations

import os
from pathlib import Path
import sys


os.environ['PYTHONDONTWRITEBYTECODE'] = '1'
sys.dont_write_bytecode = True
os.environ['POLARS_MAX_THREADS'] = '1'
os.environ.setdefault('QW_RUNTIME_ROOT', r'D:\TradingML\runtimes')
repo = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(repo))

from research.rl_trading.v6.build import main  # noqa: E402


if __name__ == '__main__':
    raise SystemExit(main())
