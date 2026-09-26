"""Bounded process admission; at most one result-sized task per worker in flight."""
from concurrent.futures import ProcessPoolExecutor, wait, FIRST_COMPLETED
from multiprocessing import get_context


def results(jobs, function, *, workers, stopped):
    if workers == 1:
        for job in jobs:
            if stopped():
                return
            yield job,function(job)
        return
    iterator = iter(jobs)
    with ProcessPoolExecutor(max_workers=workers,mp_context=get_context('spawn')) as pool:
        pending = {}
        def submit():
            job = next(iterator,None)
            if job is not None:
                pending[pool.submit(function,job)] = job
        try:
            for _ in range(workers):
                if not stopped():
                    submit()
            while pending:
                if stopped():
                    return
                done,_ = wait(pending,timeout=.5,return_when=FIRST_COMPLETED)
                for future in done:
                    job = pending.pop(future)
                    yield job,future.result()
                    if not stopped():
                        submit()
        finally:
            for future in pending:
                future.cancel()
            # The context joins running tasks before returning, including on failure.
