"""Launch a bounded, restartable GPU strategy search with runtime-only caches."""

import os
import sys

os.environ["PYTHONDONTWRITEBYTECODE"] = "1"
sys.dont_write_bytecode = True
os.environ.setdefault("POLARS_MAX_THREADS", "1")
root = "D:/TradingML/runtimes/vectorized_backtest"
for variable, suffix in (
    ("TORCHINDUCTOR_CACHE_DIR", "torch_inductor"),
    ("TRITON_CACHE_DIR", "torch_triton"),
    ("TORCH_EXTENSIONS_DIR", "torch_extensions"),
):
    os.environ.setdefault(variable, root + "/" + suffix)

from src.backend.backtest_market_keeper_pool import MARKET_CERTIFICATE_KEEPER_POOL

from .optimize_strategy import main

if __name__ == "__main__":
    import torch

    # CPU work here is small schema witnesses and graph repair, not a large
    # matrix workload. Many intra-op threads make each witness much slower.
    # CUDA compilation/capture and independent GPU lanes remain unchanged.
    torch.set_num_threads(1)
    try:
        raise SystemExit(main())
    finally:
        MARKET_CERTIFICATE_KEEPER_POOL.close()
