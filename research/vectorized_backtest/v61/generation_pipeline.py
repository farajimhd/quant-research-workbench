"""One bounded CPU lane overlaps display-only replay, with no result feedback."""
from concurrent.futures import ThreadPoolExecutor
from time import perf_counter


def overlap(reproduce,replay,concurrent,has_validation=True):
    started=perf_counter();timing=dict(concurrent=concurrent)
    if concurrent:
        with ThreadPoolExecutor(max_workers=1,thread_name_prefix='training-reproduction') as pool:
            future=pool.submit(reproduce)
            timing['validation_dispatch_seconds']=perf_counter()-started
            validation=replay()
            timing['validation_finished_seconds']=perf_counter()-started
            result=future.result()
    else:
        result=reproduce();timing['validation_dispatch_seconds']=perf_counter()-started
        validation=replay() if has_validation else None
        timing['validation_finished_seconds']=perf_counter()-started
    timing['total_seconds']=perf_counter()-started
    return result,validation,timing
