"""Compatibility launcher for research/rl_trading/v1/build_phase1.py."""
import os
os.environ['PYTHONDONTWRITEBYTECODE'] = '1'
import sys
from pathlib import Path
sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import importlib
_implementation = importlib.import_module('research.rl_trading.v1.build_phase1')

if __name__ == '__main__':
    try:
        raise SystemExit(_implementation.main())
    except (ValueError, OSError) as exc:
        print('Hindsight build failed: ' + str(exc), file=sys.stderr)
        raise SystemExit(2)
sys.modules[__name__] = _implementation
