from datetime import datetime, timezone

import pytest

from src.backend.strategy_assignment_command_transition import prepare_assignment_command
from src.trading_runtime.arte_assignment_command_projection import COMMANDS, STATUS_COMMANDS


AT = datetime(2026, 8, 17, 15, 0, tzinfo=timezone.utc)


@pytest.mark.parametrize("command", sorted(COMMANDS))
def test_prepare_matches_route_effects_without_mutating_prior(command: str) -> None:
    prior_state = {"campaign_id": "campaign-1"}
    prior = {
        "assignment_id": "assignment-1", "account_id": "account-1",
        "strategy_id": "early-squeeze", "strategy_revision": 24,
        "ticker": "ABC", "status": "managing", "state": prior_state,
        "parameters": {"threshold": 1},
    }
    saved, payload = prepare_assignment_command(prior, command.upper(), updated_at=AT)
    expected_state = dict(prior_state)
    if command == "disable_after_exit":
        expected_state["disable_after_exit"] = True
    elif command == "request_entry":
        expected_state["manual_entry_requested"] = True
    elif command == "force_entry":
        expected_state["force_entry_requested"] = True
    elif command in {"request_exit", "exit_and_stop", "exit_keep_watching"}:
        expected_state["manual_exit_requested"] = True
        expected_state["disable_after_exit"] = command == "exit_and_stop"
    assert saved == {
        **prior, "state": expected_state,
        "status": STATUS_COMMANDS.get(command, "managing"),
        "updated_at": "2026-08-17T15:00:00+00:00",
    }
    assert payload == {
        "event": "assignment_command", "command": command,
        "assignment_id": "assignment-1", "strategy_id": "early-squeeze",
        "strategy_revision": 24, "ticker": "ABC",
        "status": saved["status"], "detail": {},
    }
    assert prior_state == {"campaign_id": "campaign-1"}


def test_prepare_rejects_unmodeled_detail_and_naive_time() -> None:
    prior = {
        "assignment_id": "a", "account_id": "b", "strategy_id": "s",
        "strategy_revision": 1, "ticker": "ABC", "status": "watching", "state": {},
    }
    with pytest.raises(ValueError, match="detail is not yet typed"):
        prepare_assignment_command(prior, "pause", detail={"reason": "operator"}, updated_at=AT)
    with pytest.raises(ValueError, match="timezone-aware"):
        prepare_assignment_command(prior, "pause", updated_at=AT.replace(tzinfo=None))
