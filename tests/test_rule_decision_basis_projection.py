from datetime import datetime, UTC, date
from uuid import uuid4
from dataclasses import replace

import pytest

from src.trading_runtime.rule_decision_basis_projection import project_rule_decision_basis, verify_rule_decision_basis
from src.trading_runtime.rule_decision_basis_projection import verify_rule_decision_intent_records
from src.trading_runtime.arte_intent_projection import RecoveredIntent
from src.trading_runtime.signals import StrategyIntent


def values(status="selected"):
    return dict(run_id="run", event_month=date(2026, 8, 1), batch_id=str(uuid4()), record_id=str(uuid4()),
        decision_id="a" * 64, account_id="account", assignment_id="assignment", listing_id="listing",
        release_id=str(uuid4()), release_content_hash="b" * 64, source_session_date=date(2026, 8, 18),
        source_batch_sequence=1, source_commit_hash="c" * 64, history_input_hash="d" * 64,
        evaluation_block="full-session", observed_ordinal=10, boundary_at=datetime(2026, 8, 18, 12, tzinfo=UTC),
        basis_sequence=3, decision_sequence=5, terminal=0, selection_status=status)


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


@pytest.mark.parametrize("change", [dict(basis_sequence=5), dict(source_batch_sequence=0),
    dict(terminal=2), dict(selection_status="already_consumed"), dict(payload="opaque")])
def test_invalid_scope_or_unmodeled_data_is_rejected(change):
    with pytest.raises(ValueError):
        project_rule_decision_basis({**values(), **change}, [dict(stage="entry", strategy_intent_id="intent")])


def test_intent_extra_columns_are_not_silently_dropped():
    with pytest.raises(ValueError, match="unmodeled columns"):
        project_rule_decision_basis(values(), [dict(stage="entry", strategy_intent_id="intent", payload="opaque")])


def intent_record():
    return RecoveredIntent(4, "account", str(uuid4()), str(uuid4()),
        StrategyIntent("intent", "ABC", values()["boundary_at"], "buy", 1, 10))


def test_decision_joins_actual_semantic_intent_record():
    parent, children = project_rule_decision_basis(values(),
        [dict(stage="entry", strategy_intent_id="intent")])
    record = intent_record()
    assert verify_rule_decision_intent_records(parent, children, [record]) == (record,)
    wait, empty = project_rule_decision_basis(values("wait"), [])
    assert verify_rule_decision_intent_records(wait, empty, []) == ()


@pytest.mark.parametrize("change", [dict(sequence=3), dict(sequence=5), dict(account_id="other")])
def test_decision_rejects_intent_outside_financial_interval(change):
    parent, children = project_rule_decision_basis(values(),
        [dict(stage="entry", strategy_intent_id="intent")])
    with pytest.raises(ValueError, match="causal interval"):
        verify_rule_decision_intent_records(parent, children, [replace(intent_record(), **change)])


@pytest.mark.parametrize("change", [dict(intent_id="other"),
    dict(event_time=datetime(2026, 8, 18, 13, tzinfo=UTC)), dict(metadata={"opaque": True})])
def test_decision_rejects_foreign_or_unmodeled_intent(change):
    parent, children = project_rule_decision_basis(values(),
        [dict(stage="entry", strategy_intent_id="intent")])
    record = intent_record()
    with pytest.raises(ValueError, match="causal interval"):
        verify_rule_decision_intent_records(parent, children,
            [replace(record, intent=replace(record.intent, **change))])


def test_decision_rejects_missing_duplicate_or_overfull_intent_records():
    parent, children = project_rule_decision_basis(values(),
        [dict(stage="entry", strategy_intent_id="intent")])
    record = intent_record()
    for records in ([], [record, record], [object()]):
        with pytest.raises(ValueError, match="missing or duplicate"):
            verify_rule_decision_intent_records(parent, children, records)
    with pytest.raises(ValueError, match="sequence"):
        project_rule_decision_basis({**values(), "decision_sequence": 4},
            [dict(stage="entry", strategy_intent_id="intent")])


def test_committed_decision_page_is_bounded_and_rejects_unfenced_or_corrupt_rows(monkeypatch):
    from src.trading_runtime import arte_journal_writer as writer
    from src.trading_runtime.rule_decision_basis_projection import load_committed_rule_decision_page
    parent, children = project_rule_decision_basis(values(),
        [dict(stage="entry", strategy_intent_id="intent")])
    prefix = writer.CommittedPrefix("run", 5, parent["batch_id"], "source", "running", (parent["batch_id"],))
    queries = []
    rows = [[parent], list(children)]
    def read(client, sql):
        queries.append(sql)
        return rows.pop(0)
    monkeypatch.setattr(writer, "_rows", read)
    recovered = load_committed_rule_decision_page(object(), prefix, limit=1)
    assert recovered[0] == (parent, children)
    assert all("batch_id IN" in sql and "LIMIT" in sql for sql in queries)
    for bad_parent in ({**parent, "source_commit_hash": "e" * 64},):
        rows[:] = [[bad_parent]]
        with pytest.raises(ValueError, match="integrity"):
            load_committed_rule_decision_page(object(), prefix, limit=1)
    with pytest.raises(ValueError, match="verified prefix"):
        load_committed_rule_decision_page(object(), object())
    with pytest.raises(ValueError, match="bounded page"):
        load_committed_rule_decision_page(object(), prefix, limit=501)


def test_pending_journal_retains_basis_until_fence_and_blocks_unintegrated_publication():
    from src.backend.backtest_journal_memory import BacktestMemoryJournal
    from src.backend.backtest_typed_projection import project_pending_backtest_v4_prefix
    journal = BacktestMemoryJournal(run_id="run", max_pending_records=1)
    record = journal.append(run_id="run", category="strategy_decision", entity_type="signal",
        entity_id="a" * 64, account_id="account", event_time=values()["boundary_at"],
        payload=dict(signal_type="program_boundary_completed", causation_id="c" * 64,
            metadata=dict(assignment_id="assignment", reason_detail="wait")))
    parent, children = project_rule_decision_basis({**values("wait"),
        "record_id": record.record_id, "basis_sequence": 0, "decision_sequence": 1}, [])
    retained = journal.retain_rule_decision_basis(record, parent, children)
    assert journal.rule_decision_basis_for_record(record.record_id) is retained
    with pytest.raises(RuntimeError, match="integrated fenced basis"):
        project_pending_backtest_v4_prefix(journal, attempt_id=str(uuid4()),
            run_month=date(2026, 8, 1), prior_sequence=0, through_sequence=1)
    assert journal.pending_record_count == 1
    journal.mark_fenced(1)
    assert journal.rule_decision_basis_for_record(record.record_id) is None
    with pytest.raises(ValueError, match="pending journal record"):
        journal.retain_rule_decision_basis(record, parent, children)
    journal.close()


def test_rekey_changes_only_transport_identity_and_dependent_hashes():
    from src.trading_runtime.rule_decision_basis_projection import rekey_rule_decision_basis
    parent, children = project_rule_decision_basis(values(),
        [dict(stage="entry", strategy_intent_id="intent")])
    new_id = str(uuid4())
    updated, updated_children = rekey_rule_decision_basis(parent, children, batch_id=new_id)
    assert updated["batch_id"] == updated_children[0]["batch_id"] == new_id
    for key in set(parent) - {"batch_id", "content_hash", "intent_hash"}:
        assert updated[key] == parent[key]
    for key in set(children[0]) - {"batch_id", "content_hash"}:
        assert updated_children[0][key] == children[0][key]
    assert updated["content_hash"] != parent["content_hash"]
    assert updated["intent_hash"] != parent["intent_hash"]
    assert rekey_rule_decision_basis(updated, updated_children, batch_id=parent["batch_id"]) == (parent, children)
    from src.trading_runtime.rule_decision_basis_projection import rule_decision_semantic_hash
    assert rule_decision_semantic_hash(parent, children) == rule_decision_semantic_hash(updated, updated_children)
    changed_values = {key: value for key, value in parent.items()
        if key not in {"content_hash", "intent_count", "intent_hash"}}
    changed, changed_children = project_rule_decision_basis({**changed_values, "history_input_hash": "e" * 64},
        [dict(stage="entry", strategy_intent_id="intent")])
    assert rule_decision_semantic_hash(changed, changed_children) != rule_decision_semantic_hash(parent, children)
    with pytest.raises(ValueError):
        rekey_rule_decision_basis(parent, children, batch_id="invalid")


def test_multiple_intents_have_distinct_stable_child_ids_accepted_by_shared_commit_seal():
    from src.trading_runtime.arte_journal_commit_v4 import prepare_commit_v4, verify_commit_v4
    inputs = {**values(), "decision_sequence": 6}
    parent, children = project_rule_decision_basis(inputs,
        [dict(stage="trail", strategy_intent_id="stop"), dict(stage="add", strategy_intent_id="add")])
    assert len({child["record_id"] for child in children}) == 2
    assert all(child["parent_record_id"] == parent["record_id"] for child in children)
    assert parent["record_id"] not in {child["record_id"] for child in children}
    assert project_rule_decision_basis(inputs,
        [dict(stage="trail", strategy_intent_id="stop"), dict(stage="add", strategy_intent_id="add")]) == (parent, children)
    families = (("trading_event_v1", ({"record_id": parent["record_id"], "run_id": "run",
        "batch_id": parent["batch_id"], "content_hash": "f" * 64},)),
        ("trading_rule_decision_basis_v1", (parent,)), ("trading_rule_decision_intent_v1", children))
    commit, seals = prepare_commit_v4(run_id="run", run_month=date(2026, 8, 1),
        attempt_id=str(uuid4()), batch_id=parent["batch_id"], prior_batch_id=str(uuid4()),
        first_sequence=6, last_sequence=6, source_cursor="source", status="running",
        sealed_families=families, committed_at=inputs["boundary_at"])
    verify_commit_v4(commit, seals, detail_identities={name: tuple(
        (row["record_id"], row["content_hash"]) for row in rows) for name, rows in families})


def test_staged_scalar_hashes_match_shared_journal_typed_codec():
    from src.trading_runtime import arte_journal_writer as writer
    from src.trading_runtime.rule_decision_basis_schema import BASIS, INTENT
    parent, children = project_rule_decision_basis(values(), [dict(stage="entry", strategy_intent_id="intent")])
    for table, rows in ((BASIS, (parent,)), (INTENT, children)):
        assert writer._CONTRACTS[table.name] == table
        for row in rows:
            projected = writer.typed_row(table.name, {key: value for key, value in row.items() if key != "content_hash"})
            assert projected["content_hash"] == row["content_hash"]
