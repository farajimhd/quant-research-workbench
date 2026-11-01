"""Keep cyclic CUDA graph destruction outside stream capture."""
import gc
from contextlib import contextmanager


@contextmanager
def defer_cyclic_collection():
    # Destroying an unreachable CUDAGraph calls CUDA APIs forbidden during
    # capture. Tensor/Python allocations can otherwise trigger cyclic GC in
    # the middle of capturing a new liquidation shape.
    enabled=gc.isenabled()
    gc.disable()
    try:
        yield
    finally:
        if enabled:gc.enable()
