"""Real OMS records cross V3 projection, writer readback and cold seal."""
from datetime import datetime, timezone

import pytest

from src.backend.backtest_journal_memory import BacktestMemoryJournal
from src.backend.backtest_typed_projection import project_pending_backtest_v3_prefix
from src.backend.backtest_squeeze_episode_schema import BROKER_OMS_TABLES
from src.backend.backtest_squeeze_episode_v3 import load_verified_squeeze_v3_prefix
from src.trading_runtime import arte_journal_writer as writer
from tests.test_arte_journal_writer import ATTEMPT, RUN
from tests.test_backtest_portfolio_control_v3 import _V3Client
from tests.test_backtest_squeeze_episode_v3 import _FakeColdClient


def _shortability():
    import asyncio
    from types import SimpleNamespace
    from tests.test_backtest_broker_shortability_v3 import _intent, _manager
    manager, journal = _manager(None)
    with pytest.raises(ValueError):
        asyncio.run(manager._require_shortability(
            _intent(), SimpleNamespace(orders=())))
    return journal.unfenced_records()[0]


def _policy():
    import asyncio
    from tests.test_backtest_broker_policy_v3 import _manager
    from src.trading_runtime.order_management import BrokerCommunicationPolicy
    from src.trading_runtime.simulated_broker import SimulatedBrokerAdapter
    manager, journal = _manager(SimulatedBrokerAdapter(["DU1"]),
                                BrokerCommunicationPolicy(
                                    suppressed_message_ids=("o163", "o164")))
    asyncio.run(manager.configure_broker_session())
    return journal.unfenced_records()[0]


def _deferred():
    from tests.test_backtest_entry_reprice_deferred_v3 import _emitted_record
    return _emitted_record()


@pytest.mark.parametrize("source,table_index,count_field", [
    (_shortability, 0, "broker_short_order_skip_count"),
    (_policy, 1, "broker_reply_policy_event_count"),
    (_deferred, 3, "entry_reprice_deferred_count"),
])
def test_real_emitter_writer_cold_and_tamper(monkeypatch, source, table_index,
                                              count_field):
    import src.backend.backtest_squeeze_episode_v3 as cold_module

    record = source()
    journal = BacktestMemoryJournal(run_id=RUN)
    journal.append(run_id=RUN, category=record.category,
                   entity_type=record.entity_type, entity_id=record.entity_id,
                   account_id=record.account_id, event_time=record.event_time,
                   payload=record.payload)
    unit, = project_pending_backtest_v3_prefix(
        journal, attempt_id=ATTEMPT,
        run_month=datetime(2026, 8, 1, tzinfo=timezone.utc).date(),
        prior_sequence=0, expected_market_plan_token="a" * 64,
        expected_query_sha256="b" * 64)
    monkeypatch.setattr(writer, "_v3_preflight", lambda client: None)
    monkeypatch.setattr(writer, "_verify_run_identity",
                        lambda client, run: {"mode": "backtest"})
    client = _V3Client()
    writer.publish_typed_squeeze_batch_v3(client, unit)
    table = BROKER_OMS_TABLES[table_index].name
    assert client.inserts[-1] == "trading_commit_v3"
    assert table in client.inserts
    assert client.tables["trading_commit_v3"][0][count_field] == 1

    monkeypatch.setattr(cold_module, "storage_preflight",
                        lambda client, *, tables: None)
    monkeypatch.setattr(cold_module, "_verify_recovery_chunk",
                        lambda client, commits, *, journal_profile: None)
    cold = _FakeColdClient(client.tables["trading_commit_v3"][0], [], [])
    cold.broker_policy_events = (client.tables["trading_event_v1"]
                                 if record.category == "broker_policy" else [])
    cold.reprice_events = (client.tables["trading_event_v1"]
                           if record.entity_type == "entry_reprice_deferred" else [])
    cold.broker_oms_rows = {contract.name: client.tables.get(contract.name, [])
                            for contract in BROKER_OMS_TABLES}
    assert load_verified_squeeze_v3_prefix(
        cold, RUN, expected_market_plan_token="a" * 64,
        expected_query_sha256="b" * 64) is not None
    cold.broker_oms_rows[table] = []
    with pytest.raises((RuntimeError, ValueError)):
        load_verified_squeeze_v3_prefix(
            cold, RUN, expected_market_plan_token="a" * 64,
            expected_query_sha256="b" * 64)
