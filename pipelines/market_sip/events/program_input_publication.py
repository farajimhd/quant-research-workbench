"""Managed role composition for existing shared input publication pipelines."""
import asyncio
from datetime import datetime
from types import MappingProxyType

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

    async def publish_session_histories(self, release, opening, feature_sources, volume_sources, *,
            ownership, code_hash, first_ordinal, stop_ordinal, evaluation_block,
            cohort_windows, as_of, maximum_retained_feature_bytes=None, on_progress=None):
        """Publish the full eligible population and return the app's native request.

        No top10 truncation occurs at publication. Ranking remains in the shared
        scanner. Successful receipts come from coverage-last native publication,
        and the app cold verifies them again before opening its journal lane.
        """
        from .columnar_history_producer import CertifiedHistoryInput
        from src.backend.qmd_opening_reference import CertifiedOpeningReference
        from src.backend.qmd_columnar_history import HistoryPinRequest
        from src.backend.backtest_program_input_plan import ProgramPublishedInputRequest
        from src.backend.backtest_program_inputs import required_program_history_fields
        from src.trading_runtime.columnar_strategy_release import ProgramRelease
        from src.market_engine.columnar_history_product import require_hash
        from src.backend.backtest_program_journal_startup import _settle_owned_task
        require_hash(code_hash)
        if (type(release) is not ProgramRelease or type(opening) is not CertifiedOpeningReference
                or type(feature_sources) is not tuple or type(volume_sources) is not tuple
                or type(first_ordinal) is not int or type(stop_ordinal) is not int
                or not 0 <= first_ordinal < stop_ordinal
                or type(evaluation_block) is not str or not evaluation_block
                or not isinstance(as_of, datetime) or as_of.tzinfo is None or as_of.utcoffset() is None
                or type(cohort_windows) is not tuple or not cohort_windows
                or len(cohort_windows) > 57600
                or (on_progress is not None and not asyncio.iscoroutinefunction(on_progress))
                or (maximum_retained_feature_bytes is not None and
                    (type(maximum_retained_feature_bytes) is not int or maximum_retained_feature_bytes < 1))):
            raise ValueError('Session publication lacks explicit native source and compute bounds')
        eligible = {row['listing_id']: row for row in opening.resolved_rows()
            if release.universe.minimum_prior_close <= row['adjusted_prior_close'] <= release.universe.maximum_prior_close}
        if not eligible:
            raise ValueError('Session publication has no price-eligible population')
        required = set(required_program_history_fields(release))
        origin = None
        for packet, fields in ((feature_sources, required), (volume_sources, set())):
            if (len(packet) != len(eligible) or any(type(s) is not CertifiedHistoryInput for s in packet)
                    or {s.listing_id for s in packet} != set(eligible)):
                raise ValueError('Session publication requires the complete eligible population')
            for source in packet:
                scope = source.scope('00000000-0000-0000-0000-000000000001', code_hash)
                if (source.opening_reference != opening or source.ticker != eligible[source.listing_id]['ticker']
                        or {field.name for field in source.fields} != fields
                        or source.volume_window_seconds != release.universe.window_seconds
                        or stop_ordinal > scope.clock_count):
                    raise ValueError('Session publication differs from its release and opening lineage')
                bounds = source.first_bucket, source.stop_bucket
                if origin is None:
                    origin = bounds
                if bounds != origin:
                    raise ValueError('Session publication mixes feature and volume origins')
                if fields and any(field.revision != scope.product_revision for field in release.fields.values()):
                    raise ValueError('Session publication differs from the frozen programme feature revision')
        previous = None
        for window in cohort_windows:
            if (type(window) is not tuple or len(window) != 2 or any(type(v) is not int for v in window)
                    or not origin[0] <= window[0] < window[1] <= origin[1]
                    or window[1] - window[0] > 256 or (previous is not None and window[0] != previous)):
                raise ValueError('Session publication cohort windows are not a contiguous bounded origin')
            previous = window[1]
        sources = feature_sources + volume_sources
        if self.active:
            raise ValueError('Session publication requires an idle operator lane')
        iterator = iter(enumerate(sources))
        receipts = [None] * len(sources)
        errors, stop = [], False
        counts = dict(total=len(sources), queued=len(sources), active=0,
            completed=0, skipped=0, failed=0)
        async def report():
            if on_progress is not None:
                await on_progress(MappingProxyType(dict(phase='session_histories', **counts)))
        await report()
        async def worker():
            nonlocal stop
            while not stop:
                unit = next(iterator, None)
                if unit is None:
                    return
                index, source = unit
                counts['queued'] -= 1
                counts['active'] += 1
                try:
                    await report()
                    receipt = await self.publish_history(source, ownership=ownership, code_hash=code_hash)
                    receipts[index] = HistoryPinRequest(receipt.scope, source.fields,
                        source.parents(code_hash), receipt.content_hash)
                    counts['skipped' if receipt.status == 'skipped_verified' else 'completed'] += 1
                except BaseException as error:
                    counts['failed'] += 1
                    errors.append(error)
                    stop = True
                finally:
                    counts['active'] -= 1
                try:
                    await report()
                except BaseException as error:
                    errors.append(error)
                    stop = True
        joined = asyncio.gather(*(worker() for _ in range(min(self.capacity, len(sources)))), return_exceptions=True)
        try:
            await asyncio.shield(joined)
        except asyncio.CancelledError:
            stop = True
            await _settle_owned_task(joined)
            raise
        errors.extend(result for result in joined.result() if isinstance(result, BaseException))
        if errors:
            raise BaseExceptionGroup('Session history publication failed', errors)
        if any(receipt is None for receipt in receipts):
            raise RuntimeError('Session publication lacks complete coverage receipts')
        count = len(feature_sources)
        return ProgramPublishedInputRequest(opening, tuple(receipts[:count]), tuple(receipts[count:]),
            first_ordinal, stop_ordinal, evaluation_block, cohort_windows, as_of, self.capacity,
            maximum_retained_feature_bytes)
