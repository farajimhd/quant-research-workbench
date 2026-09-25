"""Compatibility launcher for research/rl_trading/v1/build_phase3.py."""
import os
os.environ['PYTHONDONTWRITEBYTECODE'] = '1'
import sys
from pathlib import Path
sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import importlib
_implementation = importlib.import_module('research.rl_trading.v1.build_phase3')

if __name__ == '__main__':
    raise SystemExit(_implementation.main())
sys.modules[__name__] = _implementation
