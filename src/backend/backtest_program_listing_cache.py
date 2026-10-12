"""Complete listing origins with bounded native evaluation and retained bytes."""
import asyncio
from types import MappingProxyType

from .backtest_program_feature_cache import ProgramFeatureBlockCache


class ProgramListingFeatureCache:
    def __init__(self, *, maximum_retained_bytes):
        if type(maximum_retained_bytes) is not int or maximum_retained_bytes < 1:
            raise ValueError("Listing preparation requires an explicit retained-byte budget")
        self.maximum_retained_bytes = maximum_retained_bytes
        self._lock = asyncio.Lock()
        self._key = self._blocks = None
        self.retained_bytes = 0

    async def load(self, release, pins, *, first_ordinal, stop_ordinal, evaluation_block,
                   as_of, concurrency, history_post=None):
        from .backtest_program_inputs import preflight_program_history_packets
        from .qmd_columnar_history import CertifiedHistoryPin
        from src.trading_runtime.columnar_strategy_release import ProgramRelease
        owned = dict(pins)
        if (type(release) is not ProgramRelease or not owned
                or type(concurrency) is not int or not 1 <= concurrency <= 32
                or type(evaluation_block) is not str or not 1 <= len(evaluation_block) <= 128
                or history_post is not None and not callable(history_post)
                or any(type(pin) is not CertifiedHistoryPin or listing != pin.scope.listing_id
                       for listing, pin in owned.items())):
            raise ValueError("Listing preparation lacks its bounded certified scope")
        # Validate every complete origin before the first read, including hits.
        for pin in owned.values():
            preflight_program_history_packets(release, pin, first_ordinal=first_ordinal,
                stop_ordinal=stop_ordinal, as_of=as_of)
        key = (release.header["content_hash"], first_ordinal, stop_ordinal, evaluation_block,
               tuple(sorted((listing, pin.header["content_hash"]) for listing, pin in owned.items())))
        async with self._lock:
            if key == self._key:
                return self._blocks
            self._key = self._blocks = None
            self.retained_bytes = 0
            blocks, size = {}, 0
            # One listing is in flight; its transport pool shares the existing
            # concurrency bound. Complete temporal origins are never chunked.
            for listing, pin in sorted(owned.items()):
                cache = ProgramFeatureBlockCache()
                block = await cache.load(release, {listing: pin}, first_ordinal=first_ordinal,
                    stop_ordinal=stop_ordinal, evaluation_block=evaluation_block,
                    as_of=as_of, concurrency=concurrency, history_post=history_post)
                size += block.market.estimated_size() + block.masks.estimated_size()
                if size > self.maximum_retained_bytes:
                    raise ValueError("Listing preparation exceeds its retained-byte budget")
                blocks[listing] = block
            self._blocks = MappingProxyType(blocks)
            self._key, self.retained_bytes = key, size
            return self._blocks
