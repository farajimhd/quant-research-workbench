import gc
from concurrent.futures import ThreadPoolExecutor
from threading import Event


def test_capture_excludes_pinned_allocation_and_restores_gc_after_error():
    from research.vectorized_backtest.v62.capture_lifecycle import defer_cyclic_collection,cuda_host_allocation
    entered=Event();acquired=Event();original=gc.isenabled()
    def allocate():
        entered.set()
        with cuda_host_allocation:acquired.set()
    with ThreadPoolExecutor(max_workers=1) as pool:
        try:
            with defer_cyclic_collection():
                assert not gc.isenabled()
                pending=pool.submit(allocate)
                assert entered.wait(5)
                assert not acquired.wait(.05)
                with defer_cyclic_collection():assert not gc.isenabled()
                raise RuntimeError('capture failed')
        except RuntimeError:pass
        pending.result(timeout=5)
    assert acquired.is_set()
    assert gc.isenabled()==original
