import asyncio
from copy import deepcopy
from dataclasses import replace
from datetime import date

import pytest

from src.backend.signal_dispatch_recovery import recover_dispatch_backlog, recover_dispatch_backlog_async
from src.backend.signal_dispatch_typed_cursor import INTENT, ACK, ACK_COMMIT, project_dispatch_ack, project_dispatch_intents
from src.backend.signal_dispatch_registered_writer import RegisteredDispatchCursorWriter
from src.backend.signal_dispatch_registered_publication import publish_registered_intents, publish_registered_ack
from src.trading_runtime.keeper_ownership import KeeperUnavailable
from tests.test_signal_dispatch_registered_publication import _MemoryClient, _dispatch, RUN
from tests.test_signal_dispatch_typed_cursor import _input, _intents, Authority, ACTIVATION_HASH, SOURCE_HASH
from tests.test_program_controller_runtime import definition_and_actor
from tests.test_signal_source_runtime_bootstrap import source, AT
from tests.test_signal_stream_typed_publication import FakeStorage
from src.backend.signal_stream_typed_publication import TypedSignalPublicationQueue
from src.backend.replay_run_service import ReplayRunController
from src.backend.backtest_journal_memory import BacktestMemoryJournal
from src.backend.signal_dispatch_insert_dispatch import SignalDispatchInsertDispatch, dispatch_run_id
from test_keeper_ownership import _Client, _Store


class Storage(_MemoryClient):
    def has_any_dispatch_rows(self, *, session_key):
        return self.has_dispatch_rows_after(session_key=session_key, source_batch_sequence=0)

    def has_dispatch_rows_after(self, *, session_key, source_batch_sequence):
        return any(row["session_key"] == session_key and row["source_batch_sequence"] > source_batch_sequence
            for family in self.rows.values() for row in family)


def scope(dispatch, hashes=(SOURCE_HASH, "d" * 64)):
    return dict(session_key="2026-09-24", source_commit_hashes=hashes,
        configuration_revision_id="approved-revision-1", registered_dispatch=dispatch)


def completed(storage, dispatch):
    _, delivery = _input()
    intents = _intents([delivery])
    acks = project_dispatch_ack(intents, [dict(delivery_id=delivery["delivery_id"],
        ack_kind="activation_durable", activation_receipt_hash=ACTIVATION_HASH)],
        acknowledged_at="2026-09-24T14:00:01+00:00")
    publish_registered_intents(storage, dispatch, run_id=RUN, projected=intents)
    publish_registered_ack(storage, dispatch, run_id=RUN, intents=intents, projected=acks)
    dispatch.close_for_cold(RUN)
    return intents, acks


def test_clean_source_backlog_remains_pending_after_verified_dispatch_prefix():
    storage, dispatch = Storage(), _dispatch()
    prefix = completed(storage, dispatch)
    recovery = asyncio.run(recover_dispatch_backlog_async(storage, **scope(dispatch)))
    assert recovery.completed == (prefix,)
    assert recovery.pending_source_sequences == (2,)
    assert recover_dispatch_backlog(storage, **scope(dispatch, (SOURCE_HASH,))).pending_source_sequences == ()


def test_empty_dispatch_and_committed_source_are_replayable_only_under_cold_gate():
    storage, dispatch = Storage(), _dispatch()
    with pytest.raises(KeeperUnavailable, match="cold-fenced"):
        recover_dispatch_backlog(storage, **scope(dispatch))
    dispatch.close_for_cold(RUN)
    recovery = recover_dispatch_backlog(storage, **scope(dispatch))
    assert recovery.completed == () and recovery.pending_source_sequences == (1, 2)


@pytest.mark.parametrize("table", [INTENT, ACK])
def test_orphan_dispatch_tail_is_not_treated_as_replayable_work(table):
    storage, dispatch = Storage(), _dispatch()
    completed(storage, dispatch)
    orphan = deepcopy(storage.rows[table.name][0])
    orphan["source_batch_sequence"] = 2
    storage.rows[table.name].append(orphan)
    with pytest.raises(ValueError, match="uncommitted rows"):
        recover_dispatch_backlog(storage, **scope(dispatch))


def test_changed_source_or_gapped_ack_cannot_resume():
    storage, dispatch = Storage(), _dispatch()
    completed(storage, dispatch)
    with pytest.raises(ValueError, match="verified source"):
        recover_dispatch_backlog(storage, **scope(dispatch, ("f" * 64,)))
    storage.rows[ACK_COMMIT.name].append(dict(storage.rows[ACK_COMMIT.name][0]))
    with pytest.raises(ValueError, match="duplicate, gapped"):
        recover_dispatch_backlog(storage, **scope(dispatch))


def test_registered_writer_resumes_next_source_batch_without_rewriting_completed_facts():
    storage, dispatch = Storage(), _dispatch()
    first = completed(storage, dispatch)
    recovery = recover_dispatch_backlog(storage, **scope(dispatch))
    counts = {name: len(rows) for name, rows in storage.rows.items()}
    verified = []
    def verifier(projected):
        commit = projected["commit"]
        assert commit["source_batch_sequence"] == 2
        assert commit["source_cursor_commit_hash"] == "d" * 64
        verified.append(commit["content_hash"])
    writer = RegisteredDispatchCursorWriter(storage, dispatch,
        session_key="2026-09-24", configuration_revision_id="approved-revision-1",
        source_commit_verifier=verifier, preflight=lambda _: None, recovered_prefix=recovery)
    occurrence, _ = _input()
    intents = project_dispatch_intents([], session_key="2026-09-24", source_batch_sequence=2,
        source_cursor_commit_hash="d" * 64, configuration_revision_id="approved-revision-1",
        occurrence_authority=Authority(occurrence))
    acks = project_dispatch_ack(intents, [], acknowledged_at="2026-09-24T14:00:02+00:00")
    try:
        assert writer.submit_intents(intents).result(timeout=5) == intents["commit"]["content_hash"]
        assert writer.submit_ack(acks).result(timeout=5) == acks["commit"]["content_hash"]
    finally:
        writer.close(timeout_seconds=5)
    dispatch.close_for_cold(RUN)
    recovered = recover_dispatch_backlog(storage, **scope(dispatch))
    assert recovered.completed == (first, (intents, acks)) and not recovered.pending_source_sequences
    assert verified == [intents["commit"]["content_hash"]]
    assert len(storage.rows[INTENT.name]) == counts[INTENT.name]
    assert len(storage.rows[ACK.name]) == counts[ACK.name]


def test_registered_writer_cannot_drop_completed_receipts_to_restart_from_one():
    storage, dispatch = Storage(), _dispatch()
    completed(storage, dispatch)
    recovery = recover_dispatch_backlog(storage, **scope(dispatch))
    with pytest.raises(KeeperUnavailable, match="prefix is incomplete"):
        RegisteredDispatchCursorWriter(storage, dispatch,
            session_key="2026-09-24", configuration_revision_id="approved-revision-1",
            source_commit_verifier=lambda _: None, preflight=lambda _: None,
            recovered_prefix=replace(recovery, completed=()))
    assert recover_dispatch_backlog(storage, **scope(dispatch)).completed == recovery.completed


def test_main_controller_recovers_its_committed_source_backlog(tmp_path):
    definition, actor = definition_and_actor()
    configuration, source_scope, row = source()
    revision = deepcopy(definition.configuration_revision)
    revision["revision_id"] = source_scope["configuration_revision"]
    revision["payload"].update(configuration)
    definition = replace(definition, session_date=date.fromisoformat(source_scope["session_key"]),
        configuration_revision=revision)
    controller = ReplayRunController(definition, runtime_root=tmp_path, program_strategy=actor,
        program_liquidity_resolution_ms=1000)
    controller._journal = BacktestMemoryJournal(run_id=controller.run_id)
    publisher = TypedSignalPublicationQueue(FakeStorage(), capacity=1)
    dispatch = SignalDispatchInsertDispatch(_Client(_Store(), 11))
    run = dispatch_run_id(source_scope["session_key"], source_scope["configuration_revision"])
    dispatch.initialize_new_session(run, has_ch_rows=False)
    dispatch.close_for_cold(run)
    async def check():
        await controller._initialize_runtime(record_configuration=False, record_lifecycle=False)
        try:
            head = await controller.bootstrap_program_signals(publisher, **source_scope)
            runtime = controller._program_signal_runtime
            stage = runtime.stage_resolve(revision["payload"], [row], as_of=AT, data_fields_projected=True)
            pending = runtime.submit_staged(stage, publisher, batch_sequence=1,
                previous_commit_hash=head.content_hash,
                configuration_revision=source_scope["configuration_revision"],
                source_revision=source_scope["source_revision"], catalogs=source_scope["catalogs"])
            await pending.wait_async()
            assert len(runtime.promote_staged(pending)) == 1
            recovered = await controller.recover_program_dispatch(Storage(), registered_dispatch=dispatch)
            assert recovered.completed == () and recovered.pending_source_sequences == (1,)
            assert runtime.typed_source_commit_hashes() == (pending.result(),)
            assert not actor.execution_state.positions
        finally:
            await controller._runtime.finish()
    try:
        asyncio.run(check())
    finally:
        publisher.close()
