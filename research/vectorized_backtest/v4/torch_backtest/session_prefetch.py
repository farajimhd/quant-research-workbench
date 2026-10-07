"""Ordered one-session CPU lookahead; no CUDA work or validation access."""
from concurrent.futures import ThreadPoolExecutor
from time import perf_counter


class SessionPrefetch:
    def __init__(self, sessions, loader):
        self.sessions = tuple(sessions)
        self.loader = loader
        self.pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix='v4-input')
        self.future = None
        self.index = 0

    def _load(self, session):
        started = perf_counter()
        return self.loader(session), perf_counter() - started

    def __enter__(self):
        if self.sessions:
            self.future = self.pool.submit(self._load, self.sessions[0])
        return self

    def take(self, index):
        if index != self.index or self.future is None:
            raise ValueError('Prefetch requires sequential session consumption')
        started = perf_counter()
        loaded, seconds = self.future.result()
        wait = perf_counter() - started
        self.index += 1
        self.future = (self.pool.submit(self._load, self.sessions[self.index])
                       if self.index < len(self.sessions) else None)
        return loaded, seconds, wait

    def __exit__(self, *exception):
        if self.future is not None:
            self.future.cancel()
        self.pool.shutdown(wait=True, cancel_futures=True)
