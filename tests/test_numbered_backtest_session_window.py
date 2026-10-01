"""Do not accidentally turn extended-hours research into regular-hours trades."""
from datetime import time

import pytest

from src.backend.replay_run_service import _require_numbered_session_window


@pytest.mark.parametrize("number", list(range(2, 34)))
@pytest.mark.parametrize("start,end", [
    (time(4), time(9, 30)), (time(16), time(20)),
    (time(5), time(8)), (time(17), time(19, 55)),
])
def test_extended_release_accepts_one_extended_window(number, start, end):
    _require_numbered_session_window({"strategy_number": number}, start, end)


@pytest.mark.parametrize("number", list(range(2, 34)))
@pytest.mark.parametrize("start,end", [
    (time(4), time(20)), (time(9, 30), time(16)),
    (time(9, 29), time(9, 31)), (time(15, 59), time(20)),
    (time(16), time(16)), (time(19), time(18)),
])
def test_extended_release_rejects_regular_hours_or_invalid_window(number, start, end):
    with pytest.raises(ValueError, match="regular hours are warm-up only"):
        _require_numbered_session_window({"strategy_number": number}, start, end)


def test_strategy_one_historical_window_contract_is_unchanged():
    _require_numbered_session_window({"strategy_number": 1}, time(4), time(20))


@pytest.mark.parametrize("number", list(range(2, 34)))
def test_new_strategy_public_resume_stays_closed_before_acceptance(number, tmp_path, monkeypatch):
    import asyncio
    from types import SimpleNamespace
    from unittest.mock import AsyncMock
    from src.backend.replay_run_service import ReplayRunService

    service = ReplayRunService(runtime_root=tmp_path, allow_typed_backtest_resume=True)
    definition = SimpleNamespace(configuration_revision={"payload": {"strategy": {"strategy_number": number}}})
    monkeypatch.setattr(service, "_load_typed_backtest_resume_definition", lambda run_id: definition)
    prepare = AsyncMock()
    monkeypatch.setattr(service, "_prepare_typed_v4_resume", prepare)
    with pytest.raises(RuntimeError, match="interrupted-run equivalence"):
        asyncio.run(service.resume("00000000-0000-0000-0000-000000000002"))
    prepare.assert_not_awaited()
