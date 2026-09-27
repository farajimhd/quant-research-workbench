"""The staged source transport never treats an unknown INSERT as durable."""
import pytest

from src.backend.signal_source_insert_dispatch import (
    SignalSourceInsertDispatch, source_insert_run_id,
)
from src.trading_runtime.keeper_ownership import KeeperUnavailable
from tests.test_arte_typed_insert_dispatch import Keeper, Client


TABLE = "signal_stream_python_occurrence_v1"
TOKEN = "source:2026-08-18:1:occurrence"
ROW_HASH = "a" * 64
COMMIT_HASH = "b" * 64
SQL = (f"INSERT INTO arte.{TABLE} (session_key,event_id) SETTINGS "
       "async_insert=1,wait_for_async_insert=1,insert_deduplicate=1,"
       f"insert_deduplication_token='{TOKEN}' FORMAT JSONEachRow\n"
       '{"session_key":"2026-08-18","event_id":"one"}')
RUN_ID = source_insert_run_id("2026-08-18", "approved-1")


def _started():
    dispatch = SignalSourceInsertDispatch(Keeper())
    dispatch.initialize_new_session(RUN_ID, has_ch_rows=False)
    dispatch.begin_batch(RUN_ID, sequence=1, previous_commit_hash="0" * 64)
    return dispatch


def test_source_insert_requires_ack_readback_and_commit_before_cold_barrier():
    dispatch = _started()
    client = Client()
    dispatch.execute(client, run_id=RUN_ID, sequence=1, table=TABLE,
                     token=TOKEN, row_hash=ROW_HASH, sql=SQL)
    assert len(client.calls) == 1
    with pytest.raises(KeeperUnavailable, match="unsealed"):
        dispatch.finish_batch(run_id=RUN_ID, sequence=1, commit_hash=COMMIT_HASH,
                              expected_operations=1)
    with pytest.raises(KeeperUnavailable, match="pending or ambiguous"):
        dispatch.acquire_cold_barrier(RUN_ID)
    dispatch.seal_readback(run_id=RUN_ID, sequence=1, table=TABLE,
                           token=TOKEN, row_hash=ROW_HASH)
    dispatch.finish_batch(run_id=RUN_ID, sequence=1, commit_hash=COMMIT_HASH,
                          expected_operations=1)
    assert dispatch.acquire_cold_barrier(RUN_ID) == (1, COMMIT_HASH)
    with pytest.raises(KeeperUnavailable, match="prefix differs"):
        dispatch.release_cold_barrier(RUN_ID, sequence=1, commit_hash="c" * 64)
    dispatch.release_cold_barrier(RUN_ID, sequence=1, commit_hash=COMMIT_HASH)
    dispatch.begin_batch(RUN_ID, sequence=2, previous_commit_hash=COMMIT_HASH)


def test_unknown_source_insert_response_remains_pending_and_cold_blocked():
    dispatch = _started()
    client = Client(lose_response=True)
    with pytest.raises(TimeoutError, match="response lost"):
        dispatch.execute(client, run_id=RUN_ID, sequence=1, table=TABLE,
                         token=TOKEN, row_hash=ROW_HASH, sql=SQL)
    with pytest.raises(KeeperUnavailable, match="free batch gate"):
        dispatch.execute(Client(), run_id=RUN_ID, sequence=1, table=TABLE,
                         token=TOKEN, row_hash=ROW_HASH, sql=SQL)
    with pytest.raises(KeeperUnavailable, match="pending or ambiguous"):
        dispatch.acquire_cold_barrier(RUN_ID)


def test_source_insert_rejects_unregistered_rows_and_foreign_table():
    dispatch = SignalSourceInsertDispatch(Keeper())
    with pytest.raises(KeeperUnavailable, match="unregistered"):
        dispatch.initialize_new_session(RUN_ID, has_ch_rows=True)
    dispatch.initialize_new_session(RUN_ID, has_ch_rows=False)
    dispatch.begin_batch(RUN_ID, sequence=1, previous_commit_hash="0" * 64)
    with pytest.raises(ValueError, match="typed INSERT contract"):
        dispatch.execute(Client(), run_id=RUN_ID, sequence=1,
                         table="trading_event_v1", token=TOKEN,
                         row_hash=ROW_HASH, sql=SQL)
    with pytest.raises(ValueError, match="typed INSERT contract"):
        dispatch.execute(Client(), run_id=RUN_ID, sequence=1,
                         table=TABLE, token="unsafe'token",
                         row_hash=ROW_HASH, sql=SQL)
