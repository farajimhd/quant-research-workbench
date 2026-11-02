"""Keep cyclic CUDA graph destruction outside stream capture."""
import gc
from contextlib import contextmanager
from threading import RLock

# Pinned-host allocation calls the CUDA runtime even when its tensor is on CPU.
# It may overlap replay, but must not overlap a process-global graph capture.
cuda_host_allocation = RLock()


@contextmanager
def defer_cyclic_collection():
    # Destroying an unreachable CUDAGraph calls CUDA APIs forbidden during
    # capture. Tensor/Python allocations can otherwise trigger cyclic GC in
    # the middle of capturing a new liquidation shape.
    with cuda_host_allocation:
        enabled=gc.isenabled()
        gc.disable()
        try:
            yield
        finally:
            if enabled:gc.enable()
