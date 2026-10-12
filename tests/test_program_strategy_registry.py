"""Shared registry programme pins use the real typed central release reader."""
import asyncio
from dataclasses import replace
from threading import Event
from types import SimpleNamespace

import pytest

from tests.test_columnar_strategy_release import bundle, Reader
from tests.test_fixed_numbered_registry import assignment
from src.trading_runtime.columnar_strategy_release import project_release
from src.trading_runtime.columnar_strategy_schema import RELEASE
from src.trading_runtime import strategy_registry as registry


def installed(monkeypatch):
    spec, fields, options = bundle()
    rows = project_release(spec, fields, **options)
    header = rows[RELEASE.name][0]
    registration = registry.ProgramStrategyExecutorRegistration(header["strategy_id"], header["strategy_number"],
        header["content_hash"], header["code_hash"], header["source_winner_hash"])
    monkeypatch.setattr(registry, "_PROGRAM_REGISTRY", {})
    registry.register_program_strategy_executor(registration)
    selected = replace(assignment(), strategy_id=registration.strategy_id, strategy_revision=registration.revision)
    return registration, rows, selected


def test_programme_registry_is_immutable_and_builds_from_central_rows(monkeypatch):
    registration, rows, selected = installed(monkeypatch)
    registry.register_program_strategy_executor(registration)
    assert registry.program_strategy_executor(*registration.key) == registration
    with pytest.raises(ValueError, match="cannot be replaced"):
        registry.register_program_strategy_executor(replace(registration, expected_content_hash="f" * 64))
    fixed = registry.FixedStrategyExecutorRegistration(*registration.key, "1000ms",
        lambda: SimpleNamespace(strategy_id=registration.strategy_id, strategy_number=registration.revision,
            execution_interval="1000ms"), lambda rows: None)
    with pytest.raises(ValueError, match="programme executor cannot be replaced"):
        registry.register_fixed_strategy_executor(fixed)
    legacy = registry.StrategyExecutorRegistration(*registration.key, "synthetic",
        *(lambda *args: None for _ in range(7)))
    with pytest.raises(ValueError, match="programme executor cannot be replaced"):
        registry.register_strategy_executor(legacy, replace=True)
    reader = Reader(rows)
    actor = asyncio.run(registration.build((selected,), {selected.assignment_id: "listing"},
        mode="backtest", client_factory=lambda: reader))
    assert actor.assignments() == (selected,)
    assert actor.program_release.header["entry_cash"] == 1000
    assert actor.program_release.header["add_cash"] == 200
    assert reader.closed and reader.queries
    for mode in ("live", "replay"):
        with pytest.raises(ValueError, match="Backtest-only"):
            asyncio.run(registration.build((selected,), {selected.assignment_id: "listing"},
                mode=mode, client_factory=lambda: pytest.fail("unexpected read")))
    with pytest.raises(ValueError, match="unique listing"):
        asyncio.run(registration.build((selected,), {}, mode="backtest",
            client_factory=lambda: pytest.fail("unexpected read")))


def test_programme_numbered_release_uses_same_shared_identity(monkeypatch):
    registration, _, _ = installed(monkeypatch)
    monkeypatch.setattr(registry, "_NUMBERED_RELEASES", dict(registry._NUMBERED_RELEASES))
    release = registry.NumberedStrategyRelease(registration.revision, registration.strategy_id,
        registration.revision, "1000ms", ("certified-columnar-history",), ("typed-programmes",),
        "Synthetic programme registration contract", "")
    release = replace(release, approved_digest=release.digest())
    registry.register_numbered_strategy(release)
    assert registry.numbered_strategy(registration.revision) == release
    changed = replace(release, evaluation_interval="100ms", approved_digest="")
    changed = replace(changed, approved_digest=changed.digest())
    with pytest.raises(ValueError, match="programme identity or clock"):
        registry.register_numbered_strategy(changed)


@pytest.mark.parametrize("worker_failure", [False, True])
def test_cancelled_programme_read_settles_worker_and_closes_reader(monkeypatch, worker_failure):
    registration, rows, selected = installed(monkeypatch)
    entered, release = Event(), Event()
    class HeldReader(Reader):
        def execute(self, query):
            if not entered.is_set():
                entered.set()
                assert release.wait(timeout=5)
                if worker_failure:
                    raise RuntimeError("synthetic read failure")
            return super().execute(query)
    reader = HeldReader(rows)
    async def run():
        job = asyncio.create_task(registration.build((selected,), {selected.assignment_id: "listing"},
            mode="backtest", client_factory=lambda: reader))
        try:
            assert await asyncio.to_thread(entered.wait, 2)
            job.cancel()
            await asyncio.sleep(0)
            job.cancel()
            await asyncio.sleep(0)
            assert not job.done() and not reader.closed
        finally:
            release.set()
        with pytest.raises(asyncio.CancelledError) as failure:
            await job
        assert reader.closed
        if worker_failure:
            assert isinstance(failure.value.__cause__, RuntimeError)
    asyncio.run(run())
