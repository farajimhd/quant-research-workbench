from copy import deepcopy

import pytest

from src.trading_runtime.arte_campaign_control_projection import (
    TABLES, project_campaign_control_state, restore_campaign_control_state,
)


SNAPSHOT = "4a2c2393-d13d-4ecb-9fae-b5e26ea9ff6f"
KEY = dict(assignment_id="assignment-1", revision=47, snapshot_id=SNAPSHOT,
           session="2026-09-24")


def state() -> dict:
    return {
        "campaign_id": "campaign-1", "campaign_deployment_id": "deploy-1",
        "campaign_profile_id": "profile-1", "campaign_book_id": "default",
        "campaign_universe_id": "universe-1", "campaign_side": "long",
        "manual_entry_requested": True, "disable_after_exit": False,
        "campaign_policy": {
            "initial_entry_authority": "confirm", "reentry_authority": "disabled",
            "exit_authority": "manual", "protective_exit_authority": "automatic",
            "session_end_behavior": "keep_watching", "maximum_reentries": 0,
            "reentry_cooldown_ms": 500,
        },
    }


def test_campaign_control_typed_roundtrip_with_optional_presence() -> None:
    source = state()
    rows = project_campaign_control_state(source, **KEY)
    assert restore_campaign_control_state(rows) == source
    assert rows["control"][0]["manual_exit_requested"] is None
    assert rows["policy"][0]["maximum_initial_watch_ms"] is None
    without_policy = {key: value for key, value in source.items() if key != "campaign_policy"}
    assert restore_campaign_control_state(project_campaign_control_state(without_policy, **KEY)) == without_policy
    partial = {**without_policy, "campaign_policy": {"exit_authority": "manual"}}
    assert restore_campaign_control_state(project_campaign_control_state(partial, **KEY)) == partial
    assert all("storage_policy = 'live_market_ssd'" in table.ddl() for table in TABLES)
    assert all("JSON" not in kind and "Array" not in kind and "Map" not in kind
               for table in TABLES for _, kind in table.columns)


@pytest.mark.parametrize("change, match", [
    ({"unknown_state": 1}, "unmodeled"),
    ({"manual_entry_requested": 1}, "boolean"),
    ({"campaign_policy": {"operator": "unknown"}}, "unmodeled"),
    ({"campaign_side": "both"}, "identity values"),
])
def test_campaign_control_rejects_unmodeled_or_untyped(change: dict, match: str) -> None:
    with pytest.raises(ValueError, match=match):
        project_campaign_control_state({**state(), **change}, **KEY)


def test_campaign_control_cold_readback_detects_corruption_or_fence_change() -> None:
    rows = project_campaign_control_state(state(), **KEY)
    altered = deepcopy(rows)
    altered["control"][0]["manual_entry_requested"] = 0
    with pytest.raises(ValueError, match="hash mismatch"):
        restore_campaign_control_state(altered)
    altered = deepcopy(rows)
    altered["policy"][0]["snapshot_id"] = "5a2c2393-d13d-4ecb-9fae-b5e26ea9ff6f"
    with pytest.raises(ValueError, match="hash mismatch"):
        restore_campaign_control_state(altered)
