from datetime import UTC, datetime
from dataclasses import replace
import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from src.backend.backtest_journal_memory import BacktestMemoryJournal
from src.backend.backtest_trade_proposal_v3 import (
    TABLES, project_trade_proposal_v3, seal_trade_proposals_v3,
)
from src.backend.backtest_typed_projection import project_pending_backtest_v3_prefix
from src.backend.backtest_squeeze_episode_v3 import load_verified_squeeze_v3_prefix
from src.trading_runtime import arte_journal_writer as writer_module
from src.trading_runtime.arte_journal_writer import publish_typed_squeeze_batch_v3
from tests.test_backtest_portfolio_control_v3 import _V3Client
from tests.test_backtest_squeeze_episode_v3 import _FakeColdClient
from src.trading_runtime.signals import StrategyIntent
from src.trading_runtime.runtime import TradingRuntime
from tests.test_arte_trade_proposal_children import full_result


AT = datetime(2026, 8, 18, 14, tzinfo=UTC)
RUN = "proposal-v3-test"
BATCH = str(uuid4())
ATTEMPT = str(uuid4())


def _confirmation():
    journal = BacktestMemoryJournal(run_id=RUN)
    intent = StrategyIntent(
        intent_id="proposal:p-1", ticker="AAPL", event_time=AT,
        action="enter_long", quantity=10, reference_price=100,
        metadata={"origin": "canvas_trade_proposal", "proposal_id": "p-1",
                  "proposal_authority": "manual", "action_id": "enter_long",
                  "identity_revision": "r1", "bid": 99.99, "ask": 100.01,
                  "tick_size": .01, "quote_observed_at": AT,
                  "market_snapshot": {"observed_at": AT.isoformat(),
                                      "reference_price": 100, "bid": 99.99,
                                      "ask": 100.01, "tick_size": .01,
                                      "freshness": "ready", "source_sequence": "bar-42"}})
    return journal.append(run_id=RUN, category="trade_proposal",
                          entity_type="trade_proposal_confirmed", entity_id="p-1",
                          account_id="SIM", event_time=AT,
                          payload={"proposal_id": "p-1", "authority": "manual",
                                   "status": "confirmed", "intent": intent.payload()})


def test_exact_typed_confirmation_and_aggregate_seal() -> None:
    projected = project_trade_proposal_v3(_confirmation(), attempt_id=ATTEMPT,
                                          batch_id=BATCH)
    assert [name for name, _ in projected.rows] == [
        TABLES[0].name, TABLES[1].name, TABLES[3].name]
    assert all("storage_policy = 'live_market_ssd'" in table.ddl() for table in TABLES)
    seal = seal_trade_proposals_v3(projected.rows, [projected.event],
                                   run_id=RUN, batch_id=BATCH)
    assert seal["trade_proposal_child_count"] == 3
    with pytest.raises(ValueError, match="cardinality"):
        seal_trade_proposals_v3(projected.rows[:-1], [projected.event],
                                run_id=RUN, batch_id=BATCH)
    with pytest.raises(ValueError, match="Duplicate"):
        seal_trade_proposals_v3(projected.rows + projected.rows[-1:], [projected.event],
                                run_id=RUN, batch_id=BATCH)
    with pytest.raises(ValueError, match="Orphan"):
        seal_trade_proposals_v3(projected.rows + ((TABLES[3].name, {
            **projected.rows[-1][1], "record_id": str(uuid4())}),),
            [projected.event], run_id=RUN, batch_id=BATCH)


def test_unmodeled_market_variant_stays_closed() -> None:
    source = _confirmation()
    source.payload["intent"]["metadata"]["market_snapshot"]["provider_extra"] = "x"
    with pytest.raises(ValueError):
        project_trade_proposal_v3(source, attempt_id=ATTEMPT, batch_id=BATCH)


def test_naive_envelope_clocks_and_partial_result_children_fail_closed() -> None:
    source = _confirmation()
    source = replace(source, event_time=AT.replace(tzinfo=None))
    with pytest.raises(ValueError, match="timezone authority"):
        project_trade_proposal_v3(source, attempt_id=ATTEMPT, batch_id=BATCH)
    result = full_result()
    result.payload["decision"].pop("metrics_before")
    result = BacktestMemoryJournal(run_id=RUN).append(
        run_id=RUN, category="trade_proposal", entity_type="trade_proposal_result",
        entity_id=result.payload["proposal_id"], account_id="SIM",
        event_time=result.event_time, payload=result.payload)
    with pytest.raises(ValueError):
        project_trade_proposal_v3(result, attempt_id=ATTEMPT, batch_id=BATCH)


def test_real_runtime_emits_confirmed_and_full_result_for_v3() -> None:
    journal = BacktestMemoryJournal(run_id=RUN)
    runtime = TradingRuntime.__new__(TradingRuntime)
    runtime.config = SimpleNamespace(account_ids=("DU1",))
    runtime.run_id = RUN
    runtime.journal = journal
    runtime.last_event_time = AT
    expected = full_result().payload
    runtime._execute_intents = AsyncMock(return_value=[{
        "decision": expected["decision"], "order_group": expected["order_group"],
    }])
    intent = StrategyIntent(**_confirmation().payload["intent"])
    asyncio.run(runtime.submit_external_intent(
        intent, account_id="DU1", proposal_id="p-1", proposal_authority="manual"))
    emitted = journal.records(RUN)
    assert [record.entity_type for record in emitted] == [
        "trade_proposal_confirmed", "trade_proposal_result"]
    projections = [project_trade_proposal_v3(record, attempt_id=ATTEMPT,
                                             batch_id=BATCH) for record in emitted]
    rows = tuple(item for projection in projections for item in projection.rows)
    assert seal_trade_proposals_v3(rows, [p.event for p in projections],
                                   run_id=RUN, batch_id=BATCH)["trade_proposal_child_count"] == len(rows)


def test_fake_v3_writer_reads_back_exact_proposal_before_commit(monkeypatch):
    journal = BacktestMemoryJournal(run_id=RUN)
    source = _confirmation()
    journal.append(run_id=RUN, category="trade_proposal",
                   entity_type="trade_proposal_confirmed", entity_id="p-1",
                   account_id="SIM", event_time=AT, payload=source.payload)
    unit, = project_pending_backtest_v3_prefix(
        journal, attempt_id=ATTEMPT, run_month=AT.date().replace(day=1),
        prior_sequence=0, expected_market_plan_token="a" * 64,
        expected_query_sha256="b" * 64)
    monkeypatch.setattr(writer_module, "_v3_preflight", lambda client: None)
    monkeypatch.setattr(writer_module, "_verify_run_identity", lambda client, run: {
        "mode": "backtest"})
    client = _V3Client()
    publish_typed_squeeze_batch_v3(client, unit)
    assert client.inserts == ["trading_event_v1", TABLES[0].name,
                              TABLES[1].name, TABLES[3].name, "trading_commit_v3"]
    assert client.tables["trading_commit_v3"][0]["trade_proposal_child_count"] == 3
    import src.backend.backtest_squeeze_episode_v3 as cold_module
    monkeypatch.setattr(cold_module, "storage_preflight", lambda client, *, tables: None)
    monkeypatch.setattr(cold_module, "_verify_recovery_chunk",
                        lambda client, commits, *, journal_profile: None)
    cold = _FakeColdClient(client.tables["trading_commit_v3"][0], [], [])
    cold.proposal_events = client.tables["trading_event_v1"]
    cold.proposal_rows = {table.name: client.tables.get(table.name, []) for table in TABLES}
    assert load_verified_squeeze_v3_prefix(
        cold, RUN, expected_market_plan_token="a" * 64,
        expected_query_sha256="b" * 64) is not None
    client.tables[TABLES[3].name][0]["replay_source_sequence"] = "tampered"
    with pytest.raises((RuntimeError, ValueError)):
        publish_typed_squeeze_batch_v3(client, unit)
    with pytest.raises((RuntimeError, ValueError)):
        load_verified_squeeze_v3_prefix(
            cold, RUN, expected_market_plan_token="a" * 64,
            expected_query_sha256="b" * 64)
