"""Run-owned certified volume origins, sliced into bounded scanner cohorts."""
import asyncio

from .qmd_columnar_history_transport import window_request, read_history_window_async, _owned_worker
from .qmd_columnar_population import (
    _preflight_population_window, _population_listing_frame, _population_window)


class ProgramPopulationCache:
    def __init__(self, *, maximum_retained_bytes):
        if type(maximum_retained_bytes) is not int or maximum_retained_bytes < 1:
            raise ValueError('Population cache requires an explicit retained-byte budget')
        self._maximum_retained_bytes = maximum_retained_bytes
        self.retained_bytes = 0
        self._key = self._frames = None
        self._lock = asyncio.Lock()

    @property
    def maximum_retained_bytes(self):
        return self._maximum_retained_bytes

    async def load(self, pin, *, first_bucket, stop_bucket, as_of, concurrency, history_post=None):
        # Hits retain the same causal/request validation as uncached windows.
        _preflight_population_window(pin, first_bucket=first_bucket, stop_bucket=stop_bucket,
            as_of=as_of, concurrency=concurrency)
        key = pin.source_revision
        async with self._lock:
            if key != self._key:
                self._key = self._frames = None
                self.retained_bytes = 0
                packets = {}
                # Full-origin prefetch must itself be completed as of the run.
                # Every transport request remains at most 4096 clocks.
                for listing, history in sorted(pin.histories.items()):
                    packets[listing] = tuple((first, min(first + 4096, history.scope.clock_count))
                        for first in range(0, history.scope.clock_count, 4096))
                    for first, stop in packets[listing]:
                        window_request(history, first_ordinal=first, stop_ordinal=stop, as_of=as_of)
                frames, size = {}, 0
                pending = iter(sorted(pin.histories.items()))
                async def worker():
                    nonlocal size
                    while True:
                        item = next(pending, None)
                        if item is None:
                            return
                        listing, history = item
                        parts = []
                        for first, stop in packets[listing]:
                            parts.append(await read_history_window_async(history,
                                first_ordinal=first, stop_ordinal=stop, evaluation_block='scanner-population',
                                as_of=as_of, history_post=history_post))
                        def combine():
                            import polars as pl
                            return _population_listing_frame(pin, listing, pl.concat(parts))
                        frame = await _owned_worker(combine)
                        size += frame.estimated_size()
                        if size > self.maximum_retained_bytes:
                            raise ValueError('Population preparation exceeds its retained-byte budget')
                        frames[listing] = frame
                async with asyncio.TaskGroup() as group:
                    for _ in range(min(concurrency, len(pin.histories))):
                        group.create_task(worker())
                self._key, self._frames, self.retained_bytes = key, frames, size
            def project():
                frames = {}
                for listing, history in pin.histories.items():
                    frames[listing] = self._frames[listing].slice(
                        first_bucket - history.scope.first_bucket, stop_bucket - first_bucket)
                return _population_window(pin, frames)
            return await _owned_worker(project)
