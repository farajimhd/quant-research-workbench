from datetime import datetime, timezone
from hashlib import sha256
import json
import re

import src.backend.backtest_journal_reader as reader_module
from src.backend.backtest_journal_reader import BacktestJournalReader, _consequential
from src.trading_runtime.journal_contract import JournalRecord
from src.trading_runtime.journal_evidence import REFERENCE


RUN = "00000000-0000-0000-0000-000000000010"
BATCH = "00000000-0000-0000-0000-000000000020"
STAGED = "00000000-0000-0000-0000-000000000030"
AT = datetime(2026, 8, 18, 8, 5, tzinfo=timezone.utc)


def _row(sequence, batch_id, action):
    return {
        "record_id": f"00000000-0000-0000-0000-{sequence:012x}",
        "run_id": RUN, "batch_id": batch_id, "sequence": sequence,
        "event_time": "2026-08-18 08:05:00.000000000",
        "recorded_at": "2026-08-18 08:05:00.000000",
        "category": "strategy_decision", "entity_type": "signal",
        "entity_id": f"decision-{sequence}", "account_id": "paper",
        "payload_json": json.dumps({"ticker": "AAPL", "action": action}),
    }


class Client:
    def __init__(self):
        self.queries = []
        self.rows = [_row(2, BATCH, "buy"), _row(1, BATCH, "wait"),
                     _row(3, STAGED, "sell")]

    def execute(self, sql):
        self.queries.append(sql)
        if "FROM arte.bt_event_v1" not in sql:
            raise AssertionError(sql)
        assert "toUUID('" + BATCH + "')" in sql
        assert "toUUID('" + STAGED + "')" not in sql
        maximum = int(re.search(r"sequence<=([0-9]+)", sql).group(1))
        limit = int(re.search(r"LIMIT ([0-9]+)", sql).group(1))
        offset = int(re.search(r"OFFSET ([0-9]+)", sql).group(1))
        rows = [row for row in self.rows if row["batch_id"] == BATCH
                and row["sequence"] <= maximum]
        rows.sort(key=lambda row: row["sequence"], reverse=True)
        return "\n".join(json.dumps(row) for row in rows[offset:offset + limit])


def test_reader_excludes_unfenced_tail_and_pages_consequential_activity(monkeypatch):
    monkeypatch.setattr(reader_module, "load_fenced_checkpoint", lambda _client, _run: {
        "sequence": 2, "batch_ids": (BATCH,),
    })
    client = Client()
    reader = BacktestJournalReader(client, RUN)
    latest = reader.strategy_activity_records(run_id=RUN, limit=1, through_sequence=3)
    assert [record.sequence for record in latest] == [2]
    older = reader.strategy_activity_records(run_id=RUN, limit=1, offset=1)
    assert [record.sequence for record in older] == [1]
    consequential = reader.strategy_activity_records(run_id=RUN, consequential_only=True,
                                                      limit=2)
    assert [record.sequence for record in consequential] == [2]
    assert consequential[0].event_time.tzinfo == timezone.utc
    assert all("FROM arte.bt_event_v1" in query for query in client.queries)


def test_live_reader_uses_confirmed_publisher_fence_without_revalidating_chain(monkeypatch):
    monkeypatch.setattr(reader_module, "load_fenced_checkpoint",
                        lambda *_args: (_ for _ in ()).throw(AssertionError("full recovery scan")))
    reader = BacktestJournalReader(Client(), RUN, fenced_sequence=2, batch_ids=(BATCH,))
    assert [row.sequence for row in reader.strategy_activity_records(limit=1)] == [2]


def test_compact_activity_resolves_only_chart_evidence(monkeypatch):
    monkeypatch.setattr(reader_module, "load_fenced_checkpoint", lambda _client, _run: {
        "sequence": 1, "batch_ids": (BATCH,),
    })
    raw = json.dumps({"level": {"price": 10.5, "side": 1, "unused": "large"}})
    digest = sha256(raw.encode()).hexdigest()

    class EvidenceClient(Client):
        def __init__(self):
            super().__init__()
            self.rows = [_row(1, BATCH, "buy")]
            self.rows[0]["payload_json"] = json.dumps({
                "ticker": "AAPL", "action": "buy",
                "metadata": {"unified_structural_trigger": {REFERENCE: digest}},
            })

        def execute(self, sql):
            if "FROM arte.bt_blob_v1" in sql:
                assert digest in sql
                return json.dumps({"payload_json": raw})
            return super().execute(sql)

    records = BacktestJournalReader(EvidenceClient(), RUN).strategy_activity_records(
        limit=1, compact=True)
    projected = records[0].payload["metadata"]["unified_structural_trigger"]
    assert projected["level"] == {"price": 10.5, "side": 1}


def test_consequential_projection_matches_wait_and_protection_rules():
    def record(category, entity, payload):
        return JournalRecord("id", RUN, 1, AT, AT, category, entity, "entity", "paper", payload)

    assert not _consequential(record("strategy_decision", "signal", {"action": "wait"}))
    assert _consequential(record("strategy_decision", "signal", {"action": "buy"}))
    assert _consequential(record("strategy_decision", "strategy_assignment_state",
                                 {"state": {"active_stop": 2.0, "initial_stop": 1.0}}))
    assert _consequential(record("order_management", "protection_reconciliation",
                                 {"actions": [{"type": "replace"}]}))
    assert not _consequential(record("order_management", "protection_reconciliation",
                                     {"actions": []}))
