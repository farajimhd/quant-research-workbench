import pytest

from src.trading_runtime.arte_assignment_state_event_admission import (
    PREAPPEND_EMITTER_FIELDS, MODELED_STATE_KEYS,
    validate_preappend_assignment_state_payload,
)
from src.trading_runtime.journal import TradingJournal
from src.trading_runtime.strategy_engine import (
    AssignmentStatus, StrategyAssignment, StrategyPermissions,
)
from tests.test_arte_assignment_state_composite import KEY, _state


def _emitted(state=None):
    return {
        "event": "assignment_state_saved",
        "assignment_id": KEY["assignment_id"],
        "strategy_id": "early-squeeze-momentum",
        "strategy_revision": 47,
        "ticker": "AAPL",
        "status": "watching",
        "state": _state() if state is None else state,
    }


def test_exact_emitter_shape_and_modeled_state_roundtrip():
    assignment = StrategyAssignment(
        assignment_id=KEY["assignment_id"], strategy_id="early-squeeze-momentum",
        strategy_revision=47, account_id="DU123", ticker="AAPL", conid=265598,
        status=AssignmentStatus.WATCHING, permissions=StrategyPermissions(),
        parameters={}, state=_state(),
    )
    source = dict(event="assignment_state_saved",
                  assignment_id=assignment.assignment_id,
                  strategy_id=assignment.strategy_id,
                  strategy_revision=assignment.strategy_revision,
                  ticker=assignment.ticker, status=assignment.status.value,
                  state=assignment.state)
    assert set(source) == PREAPPEND_EMITTER_FIELDS
    assert "vwap_ladder_market" in MODELED_STATE_KEYS
    validated = validate_preappend_assignment_state_payload(source, **KEY)
    assert validated == source
    validated["state"]["squeeze_entry"]["successor_added_levels"].append("new")
    assert source["state"] != validated["state"]


@pytest.mark.parametrize("change", [
    {"state": {"v5_breakout_state": {"levels": []}}},
    {"state": {"v5_entry_selection": {"references": []}}},
    {"state": {"confirmed_episode_macd": {"gap_bps": 10.0}}},
    {"state": {"vwap_ladder_entry": {"unexpected": 1}}},
    {"status": "invented"},
    {"strategy_revision": True},
    {"event": "assignment_state_deleted"},
    {"assignment_id": "other"},
    {"extra": 1},
])
def test_unmodeled_or_invalid_payload_fails_closed(change):
    source = _emitted()
    source.update(change)
    with pytest.raises(ValueError):
        validate_preappend_assignment_state_payload(source, **KEY)


def test_missing_event_field_and_non_mapping_state_fail_closed():
    source = _emitted()
    del source["ticker"]
    with pytest.raises(ValueError):
        validate_preappend_assignment_state_payload(source, **KEY)
    source = _emitted()
    source["state"] = []
    with pytest.raises(ValueError):
        validate_preappend_assignment_state_payload(source, **KEY)


def test_real_journal_append_injects_lineage_after_preappend_boundary(tmp_path):
    from datetime import datetime, timezone

    source = _emitted()
    assert validate_preappend_assignment_state_payload(source, **KEY) == source
    journal = TradingJournal(tmp_path / "assignment-state.sqlite3")
    try:
        record = journal.append_many([dict(
            run_id=KEY["run_id"], category="strategy",
            entity_type="strategy_assignment_state",
            entity_id=KEY["assignment_id"], account_id="DU123",
            event_time=datetime(2026, 9, 24, tzinfo=timezone.utc),
            payload=source,
        )])[0]
        assert set(record.payload) == PREAPPEND_EMITTER_FIELDS | {
            "correlation_id", "causation_id"}
        assert record.payload["correlation_id"]
        assert record.payload["causation_id"]
        with pytest.raises(ValueError, match="emitter contract"):
            validate_preappend_assignment_state_payload(record.payload, **KEY)
    finally:
        journal.close()
