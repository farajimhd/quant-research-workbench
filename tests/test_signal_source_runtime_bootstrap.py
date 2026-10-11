import asyncio
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from threading import Event

import pytest

from src.backend.signal_stream_runtime_service import SignalStreamRuntime
from src.backend.signal_stream_typed_catalog import build_signal_field_catalog
from src.backend.signal_stream_typed_publication import SourceCatalog, TypedSignalPublicationQueue
from src.backend.signal_stream_typed_readback import ColdOccurrenceAuthority
from tests.test_signal_stream_typed_catalog import _source
from tests.test_signal_stream_typed_publication import FakeStorage

AT = datetime(2026, 9, 24, 14, tzinfo=UTC)


def source(route=False):
    stream, columns, row = _source()
    stream.update(name="Program watchlist stream", enabled=True, revision=1,
        source_type="core_scan", inclusion_rule_sets=["up"], inclusion_operator="all",
        rearm_policy="after_cooldown", cooldown_ms=0, watchlist_routes=[])
    if route:
        stream["watchlist_routes"] = [dict(watchlist_id="eligible", membership_expiry="time_to_live", membership_ttl_ms=2000)]
    rule = dict(rule_set_id="up", enabled=True, operator="all", conditions=[dict(
        condition_id="positive-volume", enabled=True, left_source_kind="data_field",
        left_source_id="market.rolling_share_volume", comparator="greater_than", value=0)])
    configuration = dict(market_discovery=dict(signal_streams=[stream], rule_sets=[rule],
        column_catalog=list(columns.values()), data_fields=[], watchlists=[], core_scan={}))
    catalog = build_signal_field_catalog(stream, columns, configuration_revision="configuration-1")
    scope = dict(session_key="2026-09-24", configuration_revision="configuration-1",
        source_revision="source-1", catalogs={"stream-1": SourceCatalog(catalog, stream, columns)})
    row.update(last_price=10.5, rolling_share_volume=10.)
    return configuration, scope, row


@pytest.mark.parametrize("route", [False, True])
def test_cold_bootstrap_publication_and_recovery_use_the_same_prefix(route):
    configuration, scope, row = source(route)
    class ColdStorage(FakeStorage):
        def read_exact_prior_occurrence(self, event_id):
            return ColdOccurrenceAuthority(self, scope["catalogs"]).read_exact(event_id)
    storage = ColdStorage()
    publisher = TypedSignalPublicationQueue(storage, capacity=1)
    runtime = SignalStreamRuntime()
    try:
        head = asyncio.run(publisher.bootstrap_runtime_session_async(runtime, **scope))
        assert head.sequence == 0 and runtime.typed_source_cursor() == (0, "0" * 64, None)
        stage = runtime.stage_resolve(configuration, [row], as_of=AT, data_fields_projected=True)
        assert len(stage.occurrences) == 1
        pending = runtime.submit_staged(stage, publisher, batch_sequence=1,
            previous_commit_hash=head.content_hash, configuration_revision=scope["configuration_revision"],
            source_revision=scope["source_revision"], catalogs=scope["catalogs"])
        asyncio.run(pending.wait_async())
        assert len(runtime.promote_staged(pending)) == 1
        first = runtime.typed_source_cursor()
        assert first[:2] == (1, pending.result()) and first[2] == AT
        if route:
            assert runtime._admissions["eligible"]["ABC"]["event_time"] == AT.isoformat(timespec="microseconds")
    finally:
        publisher.close()
    recovered = SignalStreamRuntime()
    publisher = TypedSignalPublicationQueue(storage, capacity=1)
    try:
        head = publisher.bootstrap_runtime_session(recovered, **scope)
        assert recovered.typed_source_cursor() == first
        assert recovered.typed_source_commit_hashes() == (first[1],)
        assert recovered._admissions == runtime._admissions
        with pytest.raises(ValueError, match="advance"):
            recovered.stage_resolve(configuration, [row], as_of=AT, data_fields_projected=True)
        stage = recovered.stage_resolve(configuration, [row], as_of=AT + timedelta(seconds=1),
            data_fields_projected=True)
        assert len(stage.occurrences) == 1
        with pytest.raises(ValueError, match="cold source cursor"):
            recovered.submit_staged(stage, publisher, batch_sequence=1,
                previous_commit_hash="0" * 64, configuration_revision=scope["configuration_revision"],
                source_revision=scope["source_revision"], catalogs=scope["catalogs"])
        pending = recovered.submit_staged(stage, publisher, batch_sequence=2,
            previous_commit_hash=head.content_hash, configuration_revision=scope["configuration_revision"],
            source_revision=scope["source_revision"], catalogs=scope["catalogs"])
        asyncio.run(pending.wait_async())
        assert len(recovered.promote_staged(pending)) == 1
        assert recovered.typed_source_cursor()[0] == 2
        assert recovered.typed_source_commit_hashes() == (first[1], pending.result())
    finally:
        publisher.close()


def test_cancellation_drains_owned_cold_read_without_blocking_loop():
    _, scope, _ = source()
    entered, release, finished = Event(), Event(), Event()
    class Storage(FakeStorage):
        def list_cursor_commits(self, **kwargs):
            entered.set()
            assert release.wait(2)
            finished.set()
            return super().list_cursor_commits(**kwargs)
    publisher = TypedSignalPublicationQueue(Storage(), capacity=1)
    runtime = SignalStreamRuntime()
    async def run():
        task = asyncio.create_task(publisher.bootstrap_runtime_session_async(runtime, **scope))
        assert await asyncio.to_thread(entered.wait, 1)
        task.cancel()
        await asyncio.sleep(.01)
        assert not task.done() and not finished.is_set()
        release.set()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert finished.is_set() and runtime.typed_source_cursor()[0] == 0
    try:
        asyncio.run(run())
    finally:
        release.set()
        publisher.close()


def test_corrupt_cold_prefix_and_already_hydrated_runtime_are_rejected():
    _, scope, _ = source()
    publisher = TypedSignalPublicationQueue(FakeStorage(), capacity=1)
    runtime = SignalStreamRuntime()
    try:
        head = publisher.bootstrap_runtime_session(runtime, **scope)
        with pytest.raises(RuntimeError, match="fresh"):
            publisher.bootstrap_runtime_session(runtime, **scope)
        for change in (dict(sequence=1), dict(content_hash="f" * 64), dict(occurrence_count=1)):
            with pytest.raises(ValueError, match="cursor"):
                SignalStreamRuntime()._install_committed_source(replace(head, **change),
                    configuration_revision=scope["configuration_revision"], source_revision=scope["source_revision"])
    finally:
        publisher.close()


def test_publication_wait_drains_without_promoting_or_dispatching_on_cancellation():
    configuration, scope, row = source()
    storage = FakeStorage()
    storage.block = Event()
    publisher = TypedSignalPublicationQueue(storage, capacity=1)
    runtime = SignalStreamRuntime()
    publisher.bootstrap_runtime_session(runtime, **scope)
    stage = runtime.stage_resolve(configuration, [row], as_of=AT, data_fields_projected=True)
    pending = runtime.submit_staged(stage, publisher, batch_sequence=1,
        previous_commit_hash="0" * 64, configuration_revision=scope["configuration_revision"],
        source_revision=scope["source_revision"], catalogs=scope["catalogs"])
    async def run():
        task = asyncio.create_task(pending.wait_async())
        assert await asyncio.to_thread(storage.entered.wait, 1)
        task.cancel()
        await asyncio.sleep(.01)
        assert not task.done() and runtime.typed_source_cursor()[0] == 0
        storage.block.set()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert pending.done and runtime.typed_source_cursor()[0] == 0
        assert len(runtime.promote_staged(pending)) == 1
        assert runtime.typed_source_cursor()[0] == 1
    try:
        asyncio.run(run())
    finally:
        storage.block.set()
        publisher.close()
