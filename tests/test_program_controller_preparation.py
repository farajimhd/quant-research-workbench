"""Main run-service preparation uses the installed central programme reader."""
import asyncio
from dataclasses import replace
from hashlib import sha256

import pytest

from tests.test_program_strategy_registry import installed
from tests.test_columnar_strategy_release import Reader
from tests.test_program_controller_runtime import definition_and_actor
from src.backend.replay_run_service import ReplayRunService
from src.trading_runtime.journal_contract import canonical_json


@pytest.mark.parametrize("resolution", [100, 1000])
def test_service_prepares_unstarted_programme_with_explicit_liquidity(monkeypatch, tmp_path, resolution):
    registration, rows, _ = installed(monkeypatch)
    definition, original = definition_and_actor()
    payload = {**definition.configuration_revision["payload"], "strategy": {
        "strategy_id": registration.strategy_id, "revision": registration.revision, "parameters": {}}}
    definition = replace(definition, execution_interval={100: "100ms", 1000: "1s"}[resolution],
        market_data_plan={**definition.market_data_plan, "execution_interval": {"milliseconds": resolution}},
        configuration_revision={**definition.configuration_revision, "payload": payload,
            "content_hash": sha256(canonical_json(payload).encode()).hexdigest()})
    selected = replace(original.assignments()[0], strategy_id=registration.strategy_id,
        strategy_revision=registration.revision)
    service = ReplayRunService(runtime_root=tmp_path)
    reader = Reader(rows)
    controller = asyncio.run(service.prepare_program_controller(definition, assignments=(selected,),
        listing_by_assignment={selected.assignment_id: "listing"}, liquidity_resolution_ms=resolution,
        release_client_factory=lambda: reader))
    assert controller.definition is definition
    assert controller._program_strategy.program_release.header["content_hash"] == registration.expected_content_hash
    assert controller._program_liquidity_resolution_ms == resolution
    assert controller.status == "created" and controller._runtime is None
    assert controller._journal is None and controller._journal_writer is None
    assert not service._runs and not list(tmp_path.iterdir()) and reader.closed
    with pytest.raises(RuntimeError, match="prepared typed launch handoff"):
        asyncio.run(service.launch_prepared_program_controller(controller))
    assert not service._runs and controller._task is None
    foreign = Reader(rows)
    with pytest.raises(ValueError, match="configured Backtest accounts"):
        asyncio.run(service.prepare_program_controller(definition,
            assignments=(replace(selected, account_id="foreign-account"),),
            listing_by_assignment={selected.assignment_id: "listing"}, liquidity_resolution_ms=resolution,
            release_client_factory=lambda: foreign))
    assert foreign.closed and not service._runs
    with pytest.raises(ValueError, match="explicit Backtest liquidity"):
        asyncio.run(service.prepare_program_controller(definition, assignments=(selected,),
            listing_by_assignment={selected.assignment_id: "listing"},
            liquidity_resolution_ms=1000 if resolution == 100 else 100,
            release_client_factory=lambda: pytest.fail("unexpected database read")))
    class ChangedReader(Reader):
        def execute(self, query):
            result = super().execute(query)
            payload["strategy"]["parameters"]["changed_during_read"] = True
            return result
    changed = ChangedReader(rows)
    with pytest.raises(ValueError, match="immutable payload hash"):
        asyncio.run(service.prepare_program_controller(definition, assignments=(selected,),
            listing_by_assignment={selected.assignment_id: "listing"}, liquidity_resolution_ms=resolution,
            release_client_factory=lambda: changed))
    assert changed.closed and not service._runs and not list(tmp_path.iterdir())
