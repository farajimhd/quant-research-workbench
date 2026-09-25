"""Compatibility import; implementation lives in research.rl_trading.v1.phase2_values."""
import sys
from research.rl_trading.v1.phase2_values import *
from research.rl_trading.v1.phase2_values import __name__ as _target_name
sys.modules[__name__] = sys.modules[_target_name]
