"""Continuation scheduling seams; no database or operational restart is used."""
import asyncio
from dataclasses import replace
from datetime import timedelta, time
from inspect import signature, Parameter
from types import SimpleNamespace

import pytest

from src.backend.backtest_program_session import PreparedProgramCohortWork, _continue_fenced_session_work
from src.backend.backtest_program_cohort import run_prepared_program_cohort
from src.backend.backtest_typed_publisher import BacktestTypedJournalPublisher
from src.backend.backtest_journal_memory import BacktestMemoryJournal
from src.backend.backtest_v4_keeper_lease import BacktestV4KeeperLease
from src.backend.signal_stream_runtime_service import SignalStreamRuntime
from src.backend.program_policy_recovery import (RecoveredProgramPolicy, _RECOVERY_PROOF,
    _ProgramPolicyContinuationSeal, program_policy_fingerprint)
from tests.test_backtest_v4_keeper_lease import _session, RUN_ID
from tests.test_program_controller_runtime import definition_and_actor


@pytest.mark.parametrize("seconds", [1, 2, 3, 4])
@pytest.mark.parametrize("failure", [None, "packet", "pending", "source", "lease", "cursor", "during"])
def test_continuation_retains_feature_origin_and_checks_fenced_owner(seconds, failure, monkeypatch, tmp_path):
    definition, actor = definition_and_actor()
    definition = replace(definition, start_time=time(9, 30), end_time=time(9, 30, 4))
    at = definition.requested_start + timedelta(seconds=seconds)
    session = _session()
    session.client.remove_listener = lambda _listener: None
    session.client.stop = session.client.close = lambda: None
    lease = BacktestV4KeeperLease.acquire(session, run_id=RUN_ID, owner_id="continuation-test")
    journal = BacktestMemoryJournal(run_id=RUN_ID, initial_sequence=10)
    source = SignalStreamRuntime()
    # The verified source read seam is explicit; exercise the native index and
    # fingerprint/lease gates, without presenting this as cold admission.
    source._typed_cursor = (1, "a" * 64, at)
    source._typed_prefix = ["a" * 64]
    source._typed_cutoffs = [at]
    digest = program_policy_fingerprint(actor)
    seal = _ProgramPolicyContinuationSeal(RUN_ID, 10, 0, 0, (), ("a" * 64,), digest, _RECOVERY_PROOF)
    packet = RecoveredProgramPolicy(actor, 10, 0, 0, {}, RUN_ID, ("a" * 64,), digest,
        _RECOVERY_PROOF, seal)
    publisher = BacktestTypedJournalPublisher.__new__(BacktestTypedJournalPublisher)
    publisher.journal, publisher._sequence = journal, 10
    publisher._error = publisher._terminal_task = publisher._task = None
    publisher.exact_oms_prices = True
    publisher.writer = SimpleNamespace(_client=SimpleNamespace(backtest_v4_lease=lease))
    controller = SimpleNamespace(definition=definition, run_id=RUN_ID, current_time=at,
        _runtime=SimpleNamespace(strategy=actor, journal=journal, last_event_time=at),
        _program_strategy=actor, _program_signal_runtime=source, _journal=journal,
        _journal_publisher=publisher, _fixed_v4_lease=lease,
        _source_cursor=dict(session_date=definition.session_date.isoformat(), boundary_ms=19800000 + seconds * 1000))
    scope = {name: object() for name, parameter in signature(run_prepared_program_cohort).parameters.items()
        if parameter.kind is Parameter.KEYWORD_ONLY and parameter.default is Parameter.empty
        and name not in {"first_bucket", "stop_bucket"}}
    block = object()
    work = tuple(PreparedProgramCohortWork(34200 + 2 * i, 34202 + 2 * i,
        {"assignment": block}, object(), object(), scope) for i in range(2))
    if failure == "packet": packet = replace(packet, journal_sequence=11)
    if failure == "pending":
        journal.append(run_id=RUN_ID, category="checkpoint", entity_type="market_boundary",
            entity_id="pending", event_time=at, payload={})
    if failure == "source": source._typed_prefix = ["b" * 64]
    if failure == "lease": lease.release()
    if failure == "cursor": controller._source_cursor["boundary_ms"] -= 1000
    if failure == "during":
        original = BacktestV4KeeperLease.assert_current
        def changed(owned):
            original(owned)
            publisher._error = RuntimeError("publication failed during owner check")
        monkeypatch.setattr(BacktestV4KeeperLease, "assert_current", changed)
    try:
        if failure:
            with pytest.raises((ValueError, RuntimeError), match="fence|prefix|lease lost|market cursor|changed during"):
                asyncio.run(_continue_fenced_session_work(controller, work, packet))
        else:
            remaining = asyncio.run(_continue_fenced_session_work(controller, work, packet))
            if seconds == 4:
                assert remaining == ()
                from src.backend.replay_run_service import ReplayRunController
                import src.backend.backtest_program_session as module
                owner = ReplayRunController(definition, run_id=RUN_ID, runtime_root=tmp_path,
                    program_strategy=actor, program_liquidity_resolution_ms=100)
                for name, value in vars(controller).items():
                    setattr(owner, name, value)
                async def forbidden(*args, **kwargs):
                    raise AssertionError('Terminal continuation repeated a broker cohort')
                monkeypatch.setattr(ReplayRunController, 'run_fenced_program_cohort', forbidden)
                monkeypatch.setattr(module, 'validate_prepared_program_session', lambda *_: (work, definition, actor))
                result = asyncio.run(module.run_prepared_program_session(owner, work, initial_packet=packet))
                assert result.status == 'evaluated'
                assert result.completed_cohorts == result.planned_cohorts == 0
                assert result.liquidity_boundaries == result.source_clocks == result.qualified_decisions == 0
                assert owner._program_evaluated_terminal == (definition, owner._runtime, actor, at, 10)
                assert owner._program_session_active is False and owner._program_session_task is None
                return
            assert remaining[0].first_bucket == 34200 + seconds
            assert remaining[-1].stop_bucket == 34204
            assert all(unit.blocks["assignment"] is block for unit in remaining)
            assert work[0].first_bucket == 34200 and work[1].first_bucket == 34202
            assert len(remaining) == (2 if seconds == 1 else 1)
    finally:
        lease.release()
        journal.close()
        session.close()
