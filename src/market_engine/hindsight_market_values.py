"""Compatibility import; implementation lives in research.rl_trading.v1.market_values."""
import sys
from research.rl_trading.v1.market_values import *
from research.rl_trading.v1.market_values import __name__ as _target_name
sys.modules[__name__] = sys.modules[_target_name]
