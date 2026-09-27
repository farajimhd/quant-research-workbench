from __future__ import annotations

import pytest

from src.backend.signal_dispatch_insert_dispatch import (
    SignalDispatchInsertDispatch, dispatch_batch_proof, dispatch_family_hash,
    dispatch_run_id,
)
from src.backend.signal_dispatch_typed_cursor import (
    ACK, ACK_COMMIT, INTENT, INTENT_COMMIT,
)
from src.trading_runtime.keeper_ownership import KeeperUnavailable
from test_keeper_ownership import _Client, _Store


RUN = dispatch_run_id("2026-08-21", "approved-1")
INTENT_HASH = "a" * 64
ACK_HASH = "b" * 64
INTENT_FAMILY = dispatch_family_hash((INTENT_HASH,))
ACK_FAMILY = dispatch_family_hash((ACK_HASH,))


class _ClickHouse:
    def __init__(self, *, lose: bool = False) -> None:
        self.lose = lose
        self.queries: list[tuple[str, str]] = []

    def execute(self, sql: str, *, query_id: str) -> None:
        self.queries.append((sql, query_id))
        if self.lose:
            raise TimeoutError("lost dispatch INSERT response")


def _sql(table: str, digest: str) -> str:
    token = f"dispatch:{RUN}:1:{table}:{digest}"
    return (f"INSERT INTO arte.{table} (session_key) SETTINGS "
            "async_insert=1,wait_for_async_insert=1,insert_deduplicate=1,"
            f"insert_deduplication_token='{token}' FORMAT JSONEachRow\n{{}}")


def _execute(dispatch: SignalDispatchInsertDispatch, client: _ClickHouse,
             *, phase: str, table: str, digest: str) -> None:
    dispatch.execute(client, run_id=RUN, sequence=1, phase=phase,
                     table=table, row_hash=digest, sql=_sql(table, digest))


def _new() -> SignalDispatchInsertDispatch:
    dispatch = SignalDispatchInsertDispatch(_Client(_Store(), 11))
    dispatch.initialize_new_session(RUN, has_ch_rows=False)
    return dispatch


def test_intent_must_precede_ack_and_cold_requires_both_receipts() -> None:
    dispatch = _new()
    with pytest.raises(KeeperUnavailable, match="phase or intent predecessor"):
        dispatch.reserve(RUN, sequence=1, phase="ack", row_hashes={
            ACK.name: None, ACK_COMMIT.name: ACK_FAMILY},
            intent_commit_hash=INTENT_HASH)
    dispatch.reserve(RUN, sequence=1, phase="intent", row_hashes={
        INTENT.name: None, INTENT_COMMIT.name: INTENT_FAMILY})
    client = _ClickHouse()
    _execute(dispatch, client, phase="intent", table=INTENT_COMMIT.name,
             digest=INTENT_FAMILY)
    dispatch.seal_readback(run_id=RUN, sequence=1, phase="intent",
                           table=INTENT_COMMIT.name, row_hash=INTENT_FAMILY)
    dispatch.finish_intent(run_id=RUN, sequence=1,
                           intent_commit_hash=INTENT_HASH,
                           commit_family_hash=INTENT_FAMILY)
    with pytest.raises(KeeperUnavailable, match="unresolved phase"):
        dispatch.close_for_cold(RUN)
    dispatch.reserve(RUN, sequence=1, phase="ack", row_hashes={
        ACK.name: None, ACK_COMMIT.name: ACK_FAMILY},
        intent_commit_hash=INTENT_HASH)
    _execute(dispatch, client, phase="ack", table=ACK_COMMIT.name,
             digest=ACK_FAMILY)
    dispatch.seal_readback(run_id=RUN, sequence=1, phase="ack",
                           table=ACK_COMMIT.name, row_hash=ACK_FAMILY)
    proof = dispatch.finish_ack(run_id=RUN, sequence=1,
                                intent_commit_hash=INTENT_HASH,
                                ack_commit_hash=ACK_HASH,
                                commit_family_hash=ACK_FAMILY)
    assert proof == dispatch_batch_proof(1, INTENT_HASH, ACK_HASH)
    dispatch.close_for_cold(RUN)
    dispatch.assert_cold_receipts(RUN, {1: (INTENT_HASH, ACK_HASH)})
    with pytest.raises(KeeperUnavailable, match="incomplete"):
        dispatch.assert_cold_receipts(RUN, {})
    assert len(client.queries) == 2


def test_lost_http_response_cannot_be_promoted_by_visible_rows() -> None:
    dispatch = _new()
    dispatch.reserve(RUN, sequence=1, phase="intent", row_hashes={
        INTENT.name: None, INTENT_COMMIT.name: INTENT_FAMILY})
    with pytest.raises(TimeoutError, match="lost dispatch INSERT"):
        _execute(dispatch, _ClickHouse(lose=True), phase="intent",
                 table=INTENT_COMMIT.name, digest=INTENT_FAMILY)
    with pytest.raises(KeeperUnavailable, match="ambiguous"):
        _execute(dispatch, _ClickHouse(), phase="intent",
                 table=INTENT_COMMIT.name, digest=INTENT_FAMILY)
    with pytest.raises(KeeperUnavailable, match="unresolved phase"):
        dispatch.close_for_cold(RUN)


def test_existing_unregistered_session_and_wrong_phase_are_rejected() -> None:
    dispatch = SignalDispatchInsertDispatch(_Client(_Store(), 11))
    with pytest.raises(KeeperUnavailable, match="unregistered"):
        dispatch.initialize_new_session(RUN, has_ch_rows=True)
    dispatch = _new()
    dispatch.reserve(RUN, sequence=1, phase="intent", row_hashes={
        INTENT.name: None, INTENT_COMMIT.name: INTENT_FAMILY})
    with pytest.raises(KeeperUnavailable, match="unresolved phase"):
        dispatch.close_for_cold(RUN)
