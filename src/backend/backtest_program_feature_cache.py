"""One controller-owned feature block, keyed by immutable source and origin."""
import asyncio

from .backtest_program_journal_startup import _settle_owned_task


class ProgramFeatureBlockCache:
    def __init__(self):
        self._lock = asyncio.Lock()
        self._key = self._block = None

    async def load(self, release, pins, *, first_ordinal, stop_ordinal, evaluation_block,
                   as_of, concurrency, history_post=None):
        from .backtest_program_inputs import prepare_program_block, preflight_program_history_packets
        from .qmd_columnar_history import CertifiedHistoryPin
        from src.trading_runtime.columnar_strategy_release import ProgramRelease
        pins = dict(pins)
        if (type(release) is not ProgramRelease or not pins
                or type(first_ordinal) is not int or type(stop_ordinal) is not int
                or not 0 <= first_ordinal < stop_ordinal
                or len(pins) * (stop_ordinal - first_ordinal) > 262144
                or type(concurrency) is not int or not 1 <= concurrency <= 32
                or type(evaluation_block) is not str or not 1 <= len(evaluation_block) <= 128
                or any(type(pin) is not CertifiedHistoryPin or listing != pin.scope.listing_id
                       for listing, pin in pins.items())):
            raise ValueError("Programme feature cache requires its bounded source/block scope")
        # Reuse never bypasses the current request's availability checks.
        for pin in pins.values():
            preflight_program_history_packets(release, pin, first_ordinal=first_ordinal,
                                              stop_ordinal=stop_ordinal, as_of=as_of)
        key = (release.header["content_hash"], first_ordinal, stop_ordinal, evaluation_block,
               tuple(sorted((listing, pin.header["content_hash"]) for listing, pin in pins.items())))
        async with self._lock:
            if self._key == key:
                return self._block
            # Retain at most one block; the original preparation row bound applies.
            self._key = self._block = None
            task = asyncio.create_task(prepare_program_block(release, pins,
                first_ordinal=first_ordinal, stop_ordinal=stop_ordinal, evaluation_block=evaluation_block,
                as_of=as_of, concurrency=concurrency, history_post=history_post))
            try:
                block = await asyncio.shield(task)
            except asyncio.CancelledError:
                await _settle_owned_task(task)
                raise
            self._key, self._block = key, block
            return block
