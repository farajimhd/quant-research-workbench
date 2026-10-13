"""Managed role composition for existing shared input publication pipelines."""
import asyncio

from .opening_price_reference_producer import OpeningReferencePipeline
from .columnar_history_producer import HistoryProductPipeline
from .liquidity_resolution_producer import CertifiedLiquidityInput, publish_unit_async
from src.backend.program_app_credentials import program_app_reader_factory, program_app_writer_factory


class ProgramInputPublication:
    """One explicit bounded operator lane, with no alternative data engine.

    Callers provide existing certified source contracts and producer ownership.
    Each publication retains native source, storage, grant and coverage-last
    checks. Constructing this object opens no connection and starts no work.
    """
    def __init__(self, *, query_limits, maximum_concurrent_units):
        if type(maximum_concurrent_units) is not int or not 1 <= maximum_concurrent_units <= 6:
            raise ValueError('Programme publication requires explicit bounded capacity')
        reader = program_app_reader_factory('market_input_reader', query_limits=query_limits)
        self.opening = OpeningReferencePipeline(reader,
            program_app_writer_factory('opening_reference_publisher', query_limits=query_limits),
            maximum_concurrent_units=maximum_concurrent_units)
        self.history = HistoryProductPipeline(reader,
            program_app_writer_factory('history_publisher', query_limits=query_limits),
            maximum_concurrent_units=maximum_concurrent_units)
        self._liquidity_reader = reader
        self._liquidity_writer = program_app_writer_factory('liquidity_publisher', query_limits=query_limits)
        self.capacity = maximum_concurrent_units
        self.active = 0
        self._loop = None
        self._liquidity_active, self._liquidity_failed = set(), set()

    async def _execute(self, operation, *args, **kwargs):
        loop = asyncio.get_running_loop()
        if self._loop is not None and self._loop is not loop:
            raise ValueError('Programme publication cannot cross event-loop ownership')
        self._loop = loop
        if self.active >= self.capacity:
            raise ValueError('Programme publication capacity is full; caller must bound its work pool')
        self.active += 1
        try:
            return await operation(*args, **kwargs)
        finally:
            self.active -= 1

    async def publish_opening(self, market, prior_market, *, ownership, code_hash):
        return await self._execute(self.opening.publish, market, prior_market,
            ownership=ownership, code_hash=code_hash)

    async def publish_history(self, source, *, ownership, code_hash):
        return await self._execute(self.history.publish, source,
            ownership=ownership, code_hash=code_hash)

    async def publish_liquidity(self, source, *, ownership, code_hash):
        if type(source) is not CertifiedLiquidityInput:
            raise TypeError('Programme liquidity requires a native certified source')
        unit = source.unit
        key = (unit.build_id, unit.session_date, unit.ticker)
        if key in self._liquidity_active or key in self._liquidity_failed:
            raise ValueError('Duplicate or failed liquidity unit requires operator reconciliation')
        # Admission failure is not a failed publication; no clients have opened.
        if self.active >= self.capacity:
            raise ValueError('Programme publication capacity is full; caller must bound its work pool')
        self._liquidity_active.add(key)
        try:
            return await self._execute(publish_unit_async, self._liquidity_reader,
                self._liquidity_writer, source, ownership=ownership, code_hash=code_hash)
        except Exception:
            self._liquidity_failed.add(key)
            raise
        finally:
            self._liquidity_active.remove(key)
