from datetime import datetime, UTC, date
from uuid import uuid4

import pytest

from src.trading_runtime.rule_decision_basis_projection import project_rule_decision_basis, verify_rule_decision_basis


def values(status="selected"):
    return dict(run_id="run", event_month=date(2026, 8, 1), batch_id=str(uuid4()), record_id=str(uuid4()),
        decision_id="a" * 64, account_id="account", assignment_id="assignment", listing_id="listing",
        release_id=str(uuid4()), release_content_hash="b" * 64, source_session_date=date(2026, 8, 18),
        source_batch_sequence=1, source_commit_hash="c" * 64, history_input_hash="d" * 64,
        evaluation_block="full-session", observed_ordinal=10, boundary_at=datetime(2026, 8, 18, 12, tzinfo=UTC),
        basis_sequence=3, decision_sequence=4, terminal=0, selection_status=status)


@pytest.mark.parametrize("selected", [False, True])
def test_normalized_decision_projection_roundtrip_and_corrupt_links(selected):
    intents = [dict(stage="entry", strategy_intent_id="intent")] if selected else []
    parent, children = project_rule_decision_basis(values("selected" if selected else "wait"), intents)
    assert verify_rule_decision_basis(parent, children) == (parent, children)
    with pytest.raises(ValueError, match="parent integrity"):
        verify_rule_decision_basis({**parent, "source_commit_hash": "e" * 64}, children)
    if selected:
        with pytest.raises(ValueError, match="coverage"):
            verify_rule_decision_basis(parent, ())


@pytest.mark.parametrize("change", [dict(basis_sequence=4), dict(source_batch_sequence=0),
    dict(terminal=2), dict(selection_status="already_consumed"), dict(payload="opaque")])
def test_invalid_scope_or_unmodeled_data_is_rejected(change):
    with pytest.raises(ValueError):
        project_rule_decision_basis({**values(), **change}, [dict(stage="entry", strategy_intent_id="intent")])


def test_intent_extra_columns_are_not_silently_dropped():
    with pytest.raises(ValueError, match="unmodeled columns"):
        project_rule_decision_basis(values(), [dict(stage="entry", strategy_intent_id="intent", payload="opaque")])
