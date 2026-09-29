"""Strategy 1 resistance adds have a scalar, cold-verifiable V4 child."""
from dataclasses import replace
from datetime import date, datetime, timezone
from uuid import uuid4

import pytest

from src.trading_runtime.arte_intent_projection import strategy_intent_batch
from src.trading_runtime.arte_journal_commit_v4 import (
    _publish_typed_batch_v4, _sealed_strategy_one_add_rows,
    load_verified_commit_v4, publish_base_typed_batch_v4,
)
from src.trading_runtime.arte_journal_compound_v4 import (
    coalesce_v4_units, prepare_compound_v4_families,
)
from src.trading_runtime.arte_journal_writer import (
    _CONTRACTS, _sealed_families, V4StrategyOneEntryBatch,
)
from src.trading_runtime.arte_strategy_one_add_journal import (
    project_strategy_one_add_evidence, seal_strategy_one_add_evidence,
)
from src.trading_runtime.arte_strategy_one_entry_schema import ADD_EVIDENCE
from src.trading_runtime.strategy_one_add import StrategyOneAddProposal
from src.trading_runtime.strategy_one_intent import strategy_one_add_intent
from tests.test_arte_journal_commit_v4 import attached_v4_client


def _source():
    proposal = StrategyOneAddProposal(
        "DU1", "assignment-1", "AAA", 31_000, "B1", 10.,
        10.01, 9.89, 12., 2)
    session = date(2026, 8, 18)
    return proposal, strategy_one_add_intent(proposal, session_date=session), session


def test_add_evidence_is_scalar_and_has_exact_parent_identity():
    proposal, intent, session = _source()
    row = project_strategy_one_add_evidence(
        proposal, intent, session_date=session, run_id="run-1",
        batch_id=str(uuid4()), parent_record_id=str(uuid4()))
    assert row["resistance_id"] == "B1"
    assert row["purchase_ordinal"] == 2
    assert set(row) == {column for column, _ in ADD_EVIDENCE.columns} - {
        "content_hash"}
    assert not {"account_id", "ticker", "json", "payload"} & set(row)
    assert "live_market_ssd" in ADD_EVIDENCE.ddl()
    assert ADD_EVIDENCE.partition == "toYYYYMM(event_month)"
    assert _CONTRACTS[ADD_EVIDENCE.name] is ADD_EVIDENCE
    assert len(seal_strategy_one_add_evidence(row)["content_hash"]) == 64
    with pytest.raises(ValueError, match="differs"):
        project_strategy_one_add_evidence(
            proposal, replace(intent, reference_price=10.02),
            session_date=session, run_id="run-1",
            batch_id=str(uuid4()), parent_record_id=str(uuid4()))


def test_add_evidence_fences_and_cold_verifies_with_typed_intent():
    proposal, intent, session = _source()
    item = strategy_intent_batch(
        intent, run_id="run-1", run_month=date(2026, 8, 1),
        account_id="DU1", attempt_id=str(uuid4()), batch_id=str(uuid4()),
        prior_batch_id="00000000-0000-0000-0000-000000000000",
        sequence=1, source_cursor="boundary-31000", run_status="running",
        recorded_at=datetime(2026, 8, 18, 8, 0, 31,
                             tzinfo=timezone.utc))
    source = project_strategy_one_add_evidence(
        proposal, intent, session_date=session, run_id=item.run_id,
        batch_id=item.batch_id, parent_record_id=item.intents[0]["record_id"])
    assert len(_sealed_strategy_one_add_rows(
        item, _sealed_families(item), (source,))) == 1
    with pytest.raises(ValueError, match="lacks exact normalized evidence"):
        _sealed_strategy_one_add_rows(item, _sealed_families(item), ())
    with pytest.raises(ValueError, match="differs from its typed parent"):
        _sealed_strategy_one_add_rows(
            item, _sealed_families(item), ({**source, "boundary_ms": 32_000},))
    missing = attached_v4_client()
    with pytest.raises(ValueError, match="lacks exact normalized evidence"):
        publish_base_typed_batch_v4(missing, item)
    assert not missing.inserts
    client = attached_v4_client()
    _publish_typed_batch_v4(client, item, strategy_one_add_rows=(source,))
    commit, families = load_verified_commit_v4(
        client, run_id=item.run_id, batch_id=item.batch_id)
    assert commit["family_count"] == len(families)
    assert ADD_EVIDENCE.name in {row["family_name"] for row in families}
    assert len(client.tables[ADD_EVIDENCE.name]) == 1
    unit = V4StrategyOneEntryBatch(item, (), (source,))
    assert unit.add_evidence[0]["resistance_id"] == "B1"


def test_add_evidence_survives_compound_rekey_and_parent_seal():
    proposal, intent, session = _source()
    run_id, attempt_id = "run-add-compound", str(uuid4())
    first_id, second_id = str(uuid4()), str(uuid4())
    first = strategy_intent_batch(
        intent, run_id=run_id, run_month=date(2026, 8, 1),
        account_id="DU1", attempt_id=attempt_id, batch_id=first_id,
        prior_batch_id="00000000-0000-0000-0000-000000000000",
        sequence=1, source_cursor="boundary-31000", run_status="running",
        recorded_at=datetime(2026, 8, 18, 8, 0, 31, tzinfo=timezone.utc))
    second_proposal = replace(proposal, boundary_ms=32_000,
                              resistance_id="B2", purchase_ordinal=3)
    second_intent = strategy_one_add_intent(second_proposal,
                                             session_date=session)
    second = strategy_intent_batch(
        second_intent, run_id=run_id, run_month=date(2026, 8, 1),
        account_id="DU1", attempt_id=attempt_id, batch_id=second_id,
        prior_batch_id=first_id, sequence=2,
        source_cursor="boundary-32000", run_status="running",
        recorded_at=datetime(2026, 8, 18, 8, 0, 32, tzinfo=timezone.utc))
    units = tuple(V4StrategyOneEntryBatch(item, (), (
        project_strategy_one_add_evidence(
            source_proposal, source_intent, session_date=session,
            run_id=run_id, batch_id=item.batch_id,
            parent_record_id=item.intents[0]["record_id"]),))
        for item, source_proposal, source_intent in (
            (first, proposal, intent),
            (second, second_proposal, second_intent)))
    compound = coalesce_v4_units(units)
    client = attached_v4_client()
    _, families = prepare_compound_v4_families(client, compound)
    add_rows = dict(families)[ADD_EVIDENCE.name]
    assert len(add_rows) == 2
    assert {row["purchase_ordinal"] for row in add_rows} == {2, 3}
    assert {row["batch_id"] for row in add_rows} == {second_id}
    assert not client.inserts

    altered = {key: tuple(dict(row) for row in rows)
               for key, rows in compound.children.items()}
    altered["add_evidence"][0]["resistance_id"] = "forged"
    with pytest.raises(ValueError, match="lost normalized"):
        prepare_compound_v4_families(
            client, replace(compound, children=altered))
