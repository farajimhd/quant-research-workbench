"""Run the certified-source unified-clock research approximation (default 1s).

The faithful saved-run audit remains available through audit_strategy_one.
Outputs and compiler caches belong under the operational runtime root.
"""

import os
import sys

os.environ["PYTHONDONTWRITEBYTECODE"] = "1"
sys.dont_write_bytecode = True
root = "D:/TradingML/runtimes/vectorized_backtest"
for name, suffix in (
    ("TORCHINDUCTOR_CACHE_DIR", "torch_inductor"),
    ("TRITON_CACHE_DIR", "torch_triton"),
    ("TORCH_EXTENSIONS_DIR", "torch_extensions"),
):
    os.environ.setdefault(name, root + "/" + suffix)

from .audit_strategy_one import MARKET_CERTIFICATE_KEEPER_POOL, main

if __name__ == "__main__":
    try:
        raise SystemExit(main(sys.argv[1:], unified=True))
    finally:
        MARKET_CERTIFICATE_KEEPER_POOL.close()
