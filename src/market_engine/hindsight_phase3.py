"""Compatibility import; implementation lives in research.rl_trading.v1.phase3_search."""
import sys
from research.rl_trading.v1.phase3_search import *
from research.rl_trading.v1.phase3_search import __name__ as _target_name
sys.modules[__name__] = sys.modules[_target_name]
