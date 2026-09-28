from __future__ import annotations

import json
import pytest

from src.backend.live_completion_insert_dispatch import (
    CompletionInsertDispatch, completion_insert_proof,
    completion_insert_run_id,
)
from src.backend.live_signal_completion_keeper import completion_resource
from src.backend.live_signal_work_completion import project_completion
from src.backend.live_completion_registered_storage import RegisteredCompletionStorage
from src.backend.live_signal_work_completion import (
    CompletionPublicationQueue, prepare_completion_proof,
)
from src.trading_runtime.keeper_ownership import KeeperUnavailable
from test_keeper_ownership import _Client, _Store
from tests.test_live_signal_work_completion import Keeper, Storage, _proof_inputs


SESSION = "2026-09-24"
RUN = completion_insert_run_id(SESSION)


def _row():
    _, intents, acks = _proof_inputs()
    return project_completion(
        intents, acks, ordinal=0, processed_at="2026-09-24T14:00:02+00:00",
        keeper_owner_id="owner-1", keeper_epoch=1).row


def _sql():
    return ("INSERT INTO arte.live_signal_work_completion_typed_v1 ("
            "schema_version) SETTINGS "
            "async_insert=1,wait_for_async_insert=1,insert_deduplicate=1,"
            "insert_deduplication_token='completion-test' FORMAT JSONEachRow\n{}")


def test_completion_insert_requires_ack_exact_readback_and_keeper_proof() -> None:
    row = _row()
    resource = completion_resource(row["session_key"], row["source_batch_sequence"],
                                   row["ordinal"], row["delivery_id"])
    storage, proof_keeper = Storage(), Keeper()
    gate = CompletionInsertDispatch(_Client(_Store(), 11))
    gate.initialize_new_session(RUN, has_ch_rows=False)
    class Client:
        def execute(self, sql, *, query_id):
            assert query_id.startswith("arte_completion_")
            storage.insert_completion_row(row)
    gate.execute(Client(), run_id=RUN, resource=resource,
                 row_hash=row["content_hash"], sql=_sql(), token="completion-test")
    with pytest.raises(KeeperUnavailable, match="unresolved"):
        gate.close_for_cold(RUN)
    with pytest.raises(KeeperUnavailable, match="attested"):
        gate.seal_readback(run_id=RUN, row=row, storage=storage,
                           keeper=proof_keeper)
    proof_keeper.proof = (resource, row["keeper_owner_id"],
                          row["keeper_epoch"], row["content_hash"])
    proof = gate.seal_readback(run_id=RUN, row=row, storage=storage,
                               keeper=proof_keeper)
    assert proof == completion_insert_proof(resource, row["content_hash"])
    gate.close_for_cold(RUN)
    gate.assert_cold_receipts(RUN, {resource: row["content_hash"]})
    with pytest.raises(KeeperUnavailable, match="receipt inventory"):
        gate.assert_cold_receipts(RUN, {})


def test_lost_completion_insert_response_never_drains_cold_gate() -> None:
    row = _row()
    resource = completion_resource(row["session_key"], row["source_batch_sequence"],
                                   row["ordinal"], row["delivery_id"])
    storage = Storage()
    gate = CompletionInsertDispatch(_Client(_Store(), 11))
    gate.initialize_new_session(RUN, has_ch_rows=False)
    class Client:
        def execute(self, sql, *, query_id):
            storage.insert_completion_row(row)
            raise TimeoutError("lost completion response")
    with pytest.raises(TimeoutError, match="lost completion response"):
        gate.execute(Client(), run_id=RUN, resource=resource,
                     row_hash=row["content_hash"], sql=_sql(),
                     token="completion-test")
    assert len(storage.rows) == 1
    with pytest.raises(KeeperUnavailable, match="unresolved"):
        gate.close_for_cold(RUN)
    with pytest.raises(KeeperUnavailable, match="free gate"):
        gate.execute(Client(), run_id=RUN, resource=resource,
                     row_hash=row["content_hash"], sql=_sql(),
                     token="completion-test")


def test_registered_completion_worker_seals_only_after_attestation() -> None:
    _, intents, acks = _proof_inputs()
    proof = prepare_completion_proof(intents, acks, ordinal=0)
    class ClickHouse:
        def __init__(self):
            self.rows = []
        def execute(self, sql, *, query_id=None):
            if sql.startswith("INSERT INTO "):
                assert query_id and query_id.startswith("arte_completion_")
                self.rows.append(json.loads(sql.split("\n", 1)[1]))
                return ""
            if sql.startswith("SELECT 1 "):
                return "1\n" if self.rows else ""
            assert sql.startswith("SELECT ") and "LIMIT 2" in sql
            return "\n".join(json.dumps(row) for row in self.rows)
    client = ClickHouse()
    gate = CompletionInsertDispatch(_Client(_Store(), 11))
    storage = RegisteredCompletionStorage(client, client, gate,
                                          session_key=SESSION)
    storage.initialize_new_session()
    keeper = Keeper()
    queue = CompletionPublicationQueue(storage, keeper, owner_id="owner-1")
    try:
        receipt = queue.submit(proof, processed_at="2026-09-24T14:00:02+00:00")
        result = receipt.result(timeout=5)
    finally:
        queue.close()
    resource = completion_resource(result.row["session_key"],
                                   result.row["source_batch_sequence"],
                                   result.row["ordinal"], result.row["delivery_id"])
    storage.close_for_cold()
    storage.assert_cold_receipts({resource: result.row["content_hash"]})
    assert len(client.rows) == 1


def test_strategy_one_completion_uses_isolated_table_and_gate() -> None:
    row = _row()
    isolated = "trading_strategy_one_live_signal_work_completion_typed_v1"
    gate = CompletionInsertDispatch(_Client(_Store(), 11), strategy_one=True)
    run_id = completion_insert_run_id(SESSION, strategy_one=True)
    assert gate.table == isolated
    gate.initialize_new_session(run_id, has_ch_rows=False)
    with pytest.raises(ValueError, match="authority"):
        gate._read(RUN)
    resource = completion_resource(row["session_key"], row["source_batch_sequence"],
                                   row["ordinal"], row["delivery_id"])
    sql = _sql().replace("arte.live_signal_work_completion_typed_v1",
                         f"arte.{isolated}")
    class Client:
        def execute_registered_signal_insert(self, sql, **kwargs):
            assert kwargs["kind"] == "completion"
            assert kwargs["table"] == isolated
            assert kwargs["run_id"] == run_id
    gate.execute(Client(), run_id=run_id, resource=resource,
                 row_hash=row["content_hash"], sql=sql, token="completion-test")
    assert gate._read(run_id)[0].status == "ack"
