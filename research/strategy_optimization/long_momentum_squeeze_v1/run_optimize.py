from __future__ import annotations

import os

os.environ.setdefault("PYTHONDONTWRITEBYTECODE", "1")

from research.strategy_optimization.long_momentum_squeeze_v1.optimize import main


if __name__ == "__main__":
    main()
