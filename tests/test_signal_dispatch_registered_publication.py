from __future__ import annotations

import json
import re

import pytest

from src.backend.signal_dispatch_insert_dispatch import (
    SignalDispatchInsertDispatch, dispatch_run_id,
)
from src.backend.signal_dispatch_registered_publication import (
    publish_registered_ack, publish_registered_intents,
)
from src.backend.signal_dispatch_typed_cursor import (
    ACK, ACK_COMMIT, INTENT, INTENT_COMMIT, project_dispatch_ack,
)
from src.trading_runtime.keeper_ownership import KeeperUnavailable
from test_keeper_ownership import _Client, _Store
from tests.test_signal_dispatch_typed_cursor import (
    ACTIVATION_HASH, _input, _intents,
)


RUN = dispatch_run_id("2026-09-24", "approved-revision-1")


class _MemoryClient:
    def __init__(self, *, lose_table: str = "") -> None:
        self.rows = {name: [] for name in (
            INTENT.name, INTENT_COMMIT.name, ACK.name, ACK_COMMIT.name)}
        self.lose_table = lose_table

    def execute(self, sql: str, *, query_id: str | None = None) -> str:
        if sql.startswith("INSERT INTO arte."):
            name = sql.split("arte.", 1)[1].split(" ", 1)[0]
            self.rows[name].extend(json.loads(line) for line in
                                   sql.split("FORMAT JSONEachRow\n", 1)[1].splitlines())
            if name == self.lose_table:
                raise TimeoutError("lost dispatch INSERT response")
            return ""
        if sql.startswith("SELECT "):
            name = sql.split("FROM arte.", 1)[1].split(" ", 1)[0]
            session = re.search(r"session_key='([^']+)'", sql).group(1)
            sequence = int(re.search(r"source_batch_sequence=([0-9]+)", sql).group(1))
            return "\n".join(json.dumps(row) for row in self.rows[name]
                             if row["session_key"] == session and
                             row["source_batch_sequence"] == sequence)
        raise AssertionError(sql)


def _dispatch() -> SignalDispatchInsertDispatch:
    dispatch = SignalDispatchInsertDispatch(_Client(_Store(), 11))
    dispatch.initialize_new_session(RUN, has_ch_rows=False)
    return dispatch


def test_intent_then_ack_publishes_exact_typed_rows_and_cold_receipt() -> None:
    _, delivery = _input()
    intents = _intents([delivery])
    acks = project_dispatch_ack(intents, [{
        "delivery_id": delivery["delivery_id"],
        "ack_kind": "activation_durable",
        "activation_receipt_hash": ACTIVATION_HASH,
    }], acknowledged_at="2026-09-24T14:00:01+00:00")
    client, dispatch = _MemoryClient(), _dispatch()
    assert publish_registered_intents(
        client, dispatch, run_id=RUN, projected=intents) == intents["commit"]["content_hash"]
    with pytest.raises(KeeperUnavailable, match="unresolved phase"):
        dispatch.close_for_cold(RUN)
    assert publish_registered_ack(
        client, dispatch, run_id=RUN, intents=intents,
        projected=acks) == acks["commit"]["content_hash"]
    dispatch.close_for_cold(RUN)
    dispatch.assert_cold_receipts(RUN, {1: (
        intents["commit"]["content_hash"], acks["commit"]["content_hash"])})
    assert {name: len(rows) for name, rows in client.rows.items()} == {
        INTENT.name: 1, INTENT_COMMIT.name: 1,
        ACK.name: 1, ACK_COMMIT.name: 1}


def test_visible_intent_row_after_lost_response_does_not_advance_phase() -> None:
    _, delivery = _input()
    intents = _intents([delivery])
    client, dispatch = _MemoryClient(lose_table=INTENT_COMMIT.name), _dispatch()
    with pytest.raises(TimeoutError, match="lost dispatch INSERT response"):
        publish_registered_intents(client, dispatch, run_id=RUN,
                                   projected=intents)
    assert len(client.rows[INTENT_COMMIT.name]) == 1
    with pytest.raises(KeeperUnavailable, match="unresolved phase"):
        dispatch.close_for_cold(RUN)


def test_zero_delivery_batch_still_requires_intent_and_ack_fences() -> None:
    intents = _intents([])
    acks = project_dispatch_ack(intents, [],
                                acknowledged_at="2026-09-24T14:00:01+00:00")
    client, dispatch = _MemoryClient(), _dispatch()
    publish_registered_intents(client, dispatch, run_id=RUN,
                               projected=intents)
    assert client.rows[INTENT.name] == []
    publish_registered_ack(client, dispatch, run_id=RUN,
                           intents=intents, projected=acks)
    dispatch.close_for_cold(RUN)
    dispatch.assert_cold_receipts(RUN, {1: (
        intents["commit"]["content_hash"], acks["commit"]["content_hash"])})


def test_visible_ack_after_lost_response_cannot_complete_prefix() -> None:
    _, delivery = _input()
    intents = _intents([delivery])
    acks = project_dispatch_ack(intents, [{
        "delivery_id": delivery["delivery_id"],
        "ack_kind": "activation_durable",
        "activation_receipt_hash": ACTIVATION_HASH,
    }], acknowledged_at="2026-09-24T14:00:01+00:00")
    client, dispatch = _MemoryClient(), _dispatch()
    publish_registered_intents(client, dispatch, run_id=RUN,
                               projected=intents)
    client.lose_table = ACK_COMMIT.name
    with pytest.raises(TimeoutError, match="lost dispatch INSERT response"):
        publish_registered_ack(client, dispatch, run_id=RUN,
                               intents=intents, projected=acks)
    assert len(client.rows[ACK_COMMIT.name]) == 1
    with pytest.raises(KeeperUnavailable, match="unresolved phase"):
        dispatch.close_for_cold(RUN)
