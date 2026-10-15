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
        self._prior_close_reader = reader
        self._query_limits = dict(query_limits)
        self._liquidity_writer = program_app_writer_factory('liquidity_publisher', query_limits=query_limits)
        self.capacity = maximum_concurrent_units
        self.active = 0
        self._loop = None
        self._liquidity_active, self._liquidity_failed = set(), set()
        self._prior_close_active, self._prior_close_failed = set(), set()

    async def prepare_prior_close(self, market, *, canonical_storage_policy, canonical_disk,
            canonical_reader_factory, ownership):
        """Resolve the normal consumer identity before publishing shared history."""
        from .regular_close_history_producer import certified_prior_close_input
        from src.backend.qmd_columnar_history_transport import _owned_worker
        async def prepare():
            def read():
                ownership.assert_owner()
                reader = self._prior_close_reader()
                try:
                    source = certified_prior_close_input(reader, market,
                        canonical_storage_policy=canonical_storage_policy, canonical_disk=canonical_disk)
                    ownership.assert_owner()
                    return source
                finally:
                    reader.close()
            return await _owned_worker(read)
        source = await self._execute(prepare)
        return await self.publish_prior_close(source,
            canonical_reader_factory=canonical_reader_factory, ownership=ownership)

    async def publish_prior_close(self, source, *, canonical_reader_factory, ownership):
        from .regular_close_history_producer import PriorCloseInput, publish_prior_close_async
        if type(source) is not PriorCloseInput or not callable(canonical_reader_factory):
            raise TypeError('Prior-close publication requires its certified consumer and owned canonical reader')
        key = (source.market.build_id, source.market.sessions[0])
        if key in self._prior_close_active or key in self._prior_close_failed:
            raise ValueError('Prior-close unit is active or requires operator reconciliation')
        if self.active >= self.capacity:
            raise ValueError('Programme publication capacity is full; caller must bound its work pool')
        self._prior_close_active.add(key)
        try:
            writer = program_app_writer_factory('opening_reference_publisher', query_limits=self._query_limits)
            return await self._execute(publish_prior_close_async, self._prior_close_reader,
                canonical_reader_factory, writer, source, ownership=ownership)
        except BaseException:
            self._prior_close_failed.add(key)
            raise
        finally:
            self._prior_close_active.remove(key)

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
            ownership=ownership, code_hash=code_hash,
            listing_basis='shared-reference-routing-tradability-v1')

    async def publish_history(self, source, *, ownership, code_hash):
        return await self._execute(self.history.publish, source,
            ownership=ownership, code_hash=code_hash)

    async def prepare_features(self, request, *, ownership, progress=None, stop_requested=None):
        """Prepare the shared catalog once, then seal normalized source lineage.

        The result is producer-owned and can feed multiple listing publications.
        This never runs inside Backtest and accepts no research-shard inputs.
        """
        from .qmd_feature_preparation import prepare_feature_day
        from .feature_source_publication import publish_feature_source_plan
        from src.backend.qmd_columnar_history_transport import _owned_worker
        async def prepare():
            def work():
                ownership.assert_owner()
                reader = self.history.reader_factory()
                try:
                    prepared = prepare_feature_day(reader, request, ownership=ownership,
                        progress=progress,stop_requested=stop_requested)
                    writer = self.history.writer_factory()
                    try:
                        publish_feature_source_plan(reader, writer, prepared.plan,
                            ownership=ownership,
                            verify_sources=lambda active: prepared.verify_sources(active, ownership=ownership))
                    finally:
                        writer.close()
                    return prepared
                finally:
                    reader.close()
            return await _owned_worker(work)
        return await self._execute(prepare)

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

    async def publish_scanner_volume(self, sources, *, ownership, code_hash,
            maximum_retained_bytes=256 * 1024 * 1024, progress=None,
            stop_requested=None, reconcile_requests=()):
        """Publish a complete native volume batch off-loop using managed clients.

        The caller durably records projected attempt/hash progress before INSERT.
        Interrupted transport is drained by the shared owned-worker primitive.
        """
        from .qmd_volume_producer import publish_volume_batch
        from src.backend.qmd_columnar_history_transport import _owned_worker
        async def publish():
            def work():
                reader, writer = self.history.reader_factory(), None
                try:
                    writer = self.history.writer_factory()
                    return publish_volume_batch(reader, writer, sources, ownership=ownership,
                        code_hash=code_hash, maximum_retained_bytes=maximum_retained_bytes,
                        progress=progress, stop_requested=stop_requested,
                        reconcile_requests=reconcile_requests)
                finally:
                    if writer is not None:
                        writer.close()
                    reader.close()
            return await _owned_worker(work)
        return await self._execute(publish)

    async def publish_session_histories(self, release, opening, feature_sources, volume_sources, *,
            ownership, code_hash, first_ordinal, stop_ordinal, evaluation_block,
            cohort_windows, as_of, maximum_retained_feature_bytes=None, on_progress=None,
            maximum_retained_population_bytes=None):
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
        from src.backend.qmd_volume_reader import VolumePinRequest
        from src.trading_runtime.columnar_program_input_contract import portable_revision
        from src.market_engine.columnar_history_product import BY_NAME
        require_hash(code_hash)
        compact_volume = bool(volume_sources) and all(type(v) is VolumePinRequest for v in volume_sources)
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
                    (type(maximum_retained_feature_bytes) is not int or maximum_retained_feature_bytes < 1))
                or (maximum_retained_population_bytes is not None and
                    (type(maximum_retained_population_bytes) is not int or maximum_retained_population_bytes < 1))):
            raise ValueError('Session publication lacks explicit native source and compute bounds')
        eligible = {row['listing_id']: row for row in opening.resolved_rows()
            if release.universe.minimum_prior_close <= row['adjusted_prior_close'] <= release.universe.maximum_prior_close}
        if not eligible:
            raise ValueError('Session publication has no price-eligible population')
        required = set(required_program_history_fields(release))
        origin = None
        for index, (packet, fields) in enumerate(((feature_sources, required), (volume_sources, set()))):
            source_type = VolumePinRequest if compact_volume and index == 1 else CertifiedHistoryInput
            identities = {source.scope.listing_id if source_type is VolumePinRequest else source.listing_id
                for source in packet if type(source) is source_type}
            if (not packet or any(type(source) is not source_type for source in packet)
                    or len(identities) != len(packet) or not identities <= set(eligible)
                    or ((index == 1 or not compact_volume) and identities != set(eligible))):
                raise ValueError('Session publication requires the complete eligible volume population')
            for source in packet:
                if source_type is VolumePinRequest:
                    scope = source.scope
                    if (source.source.opening_reference != opening
                            or source.parents.volume_window_seconds != release.universe.window_seconds):
                        raise ValueError('Volume request differs from its exact opening and release')
                else:
                    scope = source.scope('00000000-0000-0000-0000-000000000001', code_hash)
                    if (source.opening_reference != opening
                            or source.ticker != eligible[source.listing_id]['ticker']
                            or {field.name for field in source.fields} != fields
                            or source.volume_window_seconds != release.universe.window_seconds):
                        raise ValueError('Session publication differs from release and opening lineage')
                    selected = tuple(sorted((BY_NAME[name] for name in release.fields), key=lambda f: f.field_id))
                    portable = portable_revision(selected, code_hash,
                        volume_window_seconds=source.volume_window_seconds)
                    if fields and any(field.revision not in (scope.product_revision, portable)
                                      for field in release.fields.values()):
                        raise ValueError('Session publication differs from frozen feature revision')
                bounds = scope.first_bucket, scope.stop_bucket
                if stop_ordinal > scope.clock_count or origin is not None and bounds != origin:
                    raise ValueError('Session publication mixes feature and volume origins')
                origin = bounds
        previous = None
        for window in cohort_windows:
            if (type(window) is not tuple or len(window) != 2 or any(type(v) is not int for v in window)
                    or not origin[0] <= window[0] < window[1] <= origin[1]
                    or window[1] - window[0] > 256 or (previous is not None and window[0] != previous)):
                raise ValueError('Session publication cohort windows are not a contiguous bounded origin')
            previous = window[1]
        sources = feature_sources + (() if compact_volume else volume_sources)
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
        return ProgramPublishedInputRequest(opening, tuple(receipts[:count]), (volume_sources if compact_volume else tuple(receipts[count:])),
            first_ordinal, stop_ordinal, evaluation_block, cohort_windows, as_of, self.capacity,
            maximum_retained_feature_bytes, maximum_retained_population_bytes)
