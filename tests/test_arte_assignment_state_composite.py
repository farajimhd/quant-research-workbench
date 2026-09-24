from copy import deepcopy

import pytest

from src.trading_runtime.arte_assignment_state_composite import (
    project_modeled_assignment_state, restore_modeled_assignment_state,
)
from tests.test_arte_campaign_control_projection import state as campaign_state
from tests.test_arte_grouped_resistance_projection import observed_state
from tests.test_arte_long_momentum_squeeze_purchase_state import _sample as purchase_state
from tests.test_arte_long_momentum_squeeze_v7_evidence import _level


KEY = dict(run_id="run-1", assignment_id="assignment-1", revision=47,
           snapshot_id="4a2c2393-d13d-4ecb-9fae-b5e26ea9ff6f",
           session="2026-09-24")


def _state():
    return {**campaign_state(), "vwap_ladder_market": observed_state(),
            **purchase_state()}


def test_exact_fake_cold_readback() -> None:
    source = _state()
    fake_rows = deepcopy(project_modeled_assignment_state(source, **KEY))
    assert restore_modeled_assignment_state(fake_rows, **KEY) == source
    assert restore_modeled_assignment_state(
        project_modeled_assignment_state(campaign_state(), **KEY), **KEY
    ) == campaign_state()


def test_all_modeled_squeeze_slices_roundtrip_and_order() -> None:
    source = _state()
    source["squeeze_entry"].update(
        anchor=_level(), broken_levels=["r2", "r1"], target_multiplier=8)
    source["squeeze_breakout"].update(
        session="2026-09-24", activated_at=100.0,
        macd_1s=dict(open=True, episode_id=100.0, observed_at=101.0,
                     line=0.4, signal=0.3),
        session_targets=dict(broken_levels=["r2", "r1"], target_multiplier=8),
        latest_broken_resistance=_level(),
        frozen_gap={"levels": [_level(), {**_level(), "unified_level_id": "v7-2"}]},
    )
    rows = deepcopy(project_modeled_assignment_state(source, **KEY))
    assert restore_modeled_assignment_state(rows, **KEY) == source
    assert rows["squeeze_v7_evidence"]["squeeze_breakout.frozen_gap.levels"]["rows"][0]["ordinal"] == 0
    broken = deepcopy(rows)
    broken["squeeze_v7_evidence"]["squeeze_breakout.frozen_gap.levels"]["rows"].reverse()
    with pytest.raises(ValueError):
        restore_modeled_assignment_state(broken, **KEY)


@pytest.mark.parametrize("change", [
    {"unknown": 1},
    {"squeeze_entry": {"successor_added_levels": [], "stop": 1.0}},
    {"squeeze_breakout": {"momentum_requests": {}, "unknown_macd": {}}},
    {"squeeze_breakout": {"frozen_gap": {"levels": [], "average": 1.0}}},
])
def test_unmodeled_state_fails_closed(change) -> None:
    with pytest.raises(ValueError, match="unmodeled"):
        project_modeled_assignment_state({**_state(), **change}, **KEY)


def test_fake_readback_rejects_missing_family_and_mixed_identity() -> None:
    rows = project_modeled_assignment_state(_state(), **KEY)
    with pytest.raises(ValueError, match="incomplete"):
        restore_modeled_assignment_state({key: value for key, value in rows.items()
                                          if key != "squeeze_purchase"}, **KEY)
    with pytest.raises(ValueError, match="identity|readback"):
        restore_modeled_assignment_state(rows, **{**KEY, "run_id": "other"})
    altered = deepcopy(rows)
    altered["squeeze_clock"]["assignment_id"] = "another-assignment"
    with pytest.raises(ValueError):
        restore_modeled_assignment_state(altered, **KEY)
