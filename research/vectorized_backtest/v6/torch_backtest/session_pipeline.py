"""Bounded, ordered CPU preparation while the GPU replays earlier sessions.

Workers only produce immutable, certified host tapes. The controller consumes
them in calendar order. Completion order never changes candidate ordering,
fitness, account resets or the all-session selection boundary.
"""

from concurrent.futures import ThreadPoolExecutor, TimeoutError
import math
from threading import RLock
from time import perf_counter


class PreparedSessions:
    def __init__(self, count, loader, *, workers=2, lookahead=2,
                 maximum_host_gib=320, maximum_tape_gib=12, emit=None):
        if (any(type(v) is not int for v in (count, workers, lookahead))
                or not 1 <= workers <= 4 or not 0 <= lookahead <= 4
                or workers + lookahead > 6 or count < 1):
            raise ValueError("Require 1..4 workers and a bounded lookahead window")
        if not all(math.isfinite(v) and v > 0 for v in (maximum_host_gib, maximum_tape_gib)):
            raise ValueError("Positive explicit host/tape budgets required")
        self.count, self.loader = count, loader
        self.workers, self.lookahead = workers, lookahead
        self.maximum_bytes = int(maximum_host_gib * 1024**3)
        # Reserve temporary arrays as well as the published tape. Fail before
        # entering a loader rather than relying on a post-allocation check.
        self.reservation = int(2 * maximum_tape_gib * 1024**3)
        self.emit = emit or (lambda event: None)
        self.lock = RLock()
        self.executor = ThreadPoolExecutor(workers, thread_name_prefix="v3-prepare")
        self.futures, self.ready, self.active, self.failed = {}, {}, set(), {}
        self.consumed = set()
        self.bytes, self.reserved, self.wait_seconds = 0, 0, 0.0
        self.external_bytes = 0
        self.waiting = None
        self.seconds = [None] * count
        self.closed = False

    def __len__(self):
        return self.count

    def state(self):
        with self.lock:
            return dict(total=self.count, ready=len(self.ready), active=len(self.active),
                        queued=sum(not f.running() and not f.done() for f in self.futures.values()),
                        failed=len(self.failed), consumed=len(self.consumed),
                        cancelled=sum(f.cancelled() for f in self.futures.values()),
                        workers=self.workers, lookahead=self.lookahead,
                        host_gib=self.bytes / 1024**3,
                        reserved_gib=self.reserved / 1024**3,
                        pinned_staging_gib=self.external_bytes / 1024**3,
                        waiting_session=self.waiting,
                        data_wait_seconds=self.wait_seconds)

    def reserve_staging(self, size):
        with self.lock:
            if self.bytes + self.reserved + self.external_bytes + size > self.maximum_bytes:
                return False
            self.external_bytes += size
            return True

    def release_staging(self, size):
        with self.lock:
            self.external_bytes -= size
            if self.external_bytes < 0:
                raise RuntimeError("Unbalanced staging memory reservation")

    def _publish(self, **extra):
        with self.lock:
            self.emit(dict(pipeline=self.state(), **extra))

    def _load(self, index):
        started = perf_counter()
        acquired = False
        try:
            with self.lock:
                if self.bytes + self.reserved + self.external_bytes + self.reservation > self.maximum_bytes:
                    raise MemoryError("Prepared sessions and in-flight arrays exceed host envelope")
                self.reserved += self.reservation
                self.active.add(index)
                acquired = True
            self._publish()
            value = self.loader(index).validate()
            with self.lock:
                if value.bytes > self.reservation // 2:
                    raise MemoryError("Loader exceeded its declared tape reservation")
                if self.bytes + value.bytes + self.reserved + self.external_bytes - self.reservation > self.maximum_bytes:
                    raise MemoryError("Published tapes exceed host envelope")
                self.ready[index] = value
                self.bytes += value.bytes
                self.seconds[index] = perf_counter() - started
            return value
        except BaseException as error:
            with self.lock:
                self.failed[index] = type(error).__name__
            raise
        finally:
            with self.lock:
                if acquired:
                    self.reserved -= self.reservation
                    self.active.discard(index)
            self._publish()

    def prefetch(self, index):
        with self.lock:
            if self.closed:
                raise RuntimeError("Preparation pipeline closed")
            # Active producers plus buffered lookahead. Advance on consumption
            # so a ready day does not leave the second producer idle during GPU
            # replay; never enqueue the entire campaign at startup.
            for i in range(index, min(self.count, index + self.workers + self.lookahead)):
                if i not in self.futures:
                    self.futures[i] = self.executor.submit(self._load, i)
        self._publish()

    def peek(self, index):
        """Nonblocking read of a successfully published tape only."""
        with self.lock:
            return self.ready.get(index)

    def get(self, index):
        if not 0 <= index < self.count:
            raise IndexError(index)
        self.prefetch(index)
        started = perf_counter()
        with self.lock:
            future = self.futures[index]
            self.waiting = index if not future.done() else None
        try:
            while True:
                try:
                    value = future.result(timeout=1)
                    break
                except TimeoutError:
                    self._publish()
        finally:
            with self.lock:
                self.wait_seconds += perf_counter() - started
                self.waiting = None
        with self.lock:
            self.consumed.add(index)
        self.prefetch(index + 1)
        self._publish()
        return value

    def close(self):
        if self.closed:
            return
        self.closed = True
        # Pending units are cancelled; already running loaders finish and retain
        # their atomic snapshots. Never leave thread/process children behind.
        self.executor.shutdown(wait=True, cancel_futures=True)
        self._publish()

    def __enter__(self):
        return self

    def __exit__(self, *error):
        self.close()
