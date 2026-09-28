from __future__ import annotations

from datetime import date, datetime, timezone
from hashlib import sha256

import pytest

from src.trading_runtime.arte_activation_insert_dispatch import (
    ActivationInsertDispatch, _TABLES, activation_insert_proof,
    publish_registered_activation,
)
from src.trading_runtime.arte_activation_projection import (
    _family_hash, load_activation, project_activation,
    strategy_one_activation_run_id,
)
from src.trading_runtime.keeper_ownership import KeeperUnavailable
from src.trading_runtime.arte_strategy_one_activation_schema import (
    strategy_one_activation_table,
)
from test_keeper_ownership import _Client, _Store
from tests.test_arte_activation_projection import _MemoryClient, _delivery


RUN = "strategy-one:paper:2026-08-21:plan-hash"
DELIVERY = "plan-1:event-1"
PARENT = "a" * 64
COMMIT = "b" * 64
PARENT_FAMILY = _family_hash(({"content_hash": PARENT},))
COMMIT_FAMILY = _family_hash(({"content_hash": COMMIT},))


class _ClickHouse:
    def __init__(self, *, lose: bool = False) -> None:
        self.lose = lose
        self.queries: list[tuple[str, str]] = []

    def execute(self, sql: str, *, query_id: str) -> None:
        self.queries.append((sql, query_id))
        if self.lose:
            raise TimeoutError("lost ClickHouse response")


class _ActivationClient(_MemoryClient):
    def __init__(self, *, lose_table: str = "", persist_before_loss: bool = False) -> None:
        super().__init__()
        self.lose_table = lose_table
        self.persist_before_loss = persist_before_loss

    def execute(self, sql: str, *, query_id: str | None = None) -> str:
        if (query_id is not None and sql.startswith(
                f"INSERT INTO arte.{self.lose_table} (")):
            if self.persist_before_loss:
                super().execute(sql)
            raise TimeoutError("lost activation INSERT response")
        return super().execute(sql)


def _dispatch() -> ActivationInsertDispatch:
    dispatch = ActivationInsertDispatch(_Client(_Store(), 11))
    dispatch.initialize_new_run(RUN, has_ch_rows=False)
    dispatch.reserve(RUN, DELIVERY, {
        _TABLES[0]: PARENT_FAMILY, _TABLES[1]: None,
        _TABLES[2]: None, _TABLES[3]: COMMIT_FAMILY,
    })
    return dispatch


def _sql(table: str, digest: str) -> str:
    token = (f"activation:{RUN}:{sha256(DELIVERY.encode()).hexdigest()}:"
             f"{table}:{digest}")
    return (f"INSERT INTO arte.{table} (run_id) SETTINGS "
            "async_insert=1,wait_for_async_insert=1,insert_deduplicate=1,"
            f"insert_deduplication_token='{token}' FORMAT JSONEachRow\n{{}}")


def _execute(dispatch: ActivationInsertDispatch, client: _ClickHouse,
             table: str, digest: str) -> None:
    dispatch.execute(client, run_id=RUN, delivery_id=DELIVERY,
                     table=table, row_hash=digest, sql=_sql(table, digest))


def test_lost_insert_response_cannot_be_cleared_by_cold_scan() -> None:
    dispatch = _dispatch()
    with pytest.raises(TimeoutError, match="lost ClickHouse response"):
        _execute(dispatch, _ClickHouse(lose=True), _TABLES[0], PARENT_FAMILY)
    with pytest.raises(KeeperUnavailable, match="ambiguous"):
        _execute(dispatch, _ClickHouse(), _TABLES[0], PARENT_FAMILY)
    with pytest.raises(KeeperUnavailable, match="acknowledged"):
        dispatch.seal_readback(run_id=RUN, delivery_id=DELIVERY,
                               table=_TABLES[0], row_hash=PARENT_FAMILY)
    with pytest.raises(KeeperUnavailable, match="unresolved"):
        dispatch.close_for_cold(RUN)


def test_exact_ack_readback_and_receipts_are_required_for_cold_fence() -> None:
    dispatch = _dispatch()
    client = _ClickHouse()
    for table, digest in ((_TABLES[0], PARENT_FAMILY),
                          (_TABLES[3], COMMIT_FAMILY)):
        _execute(dispatch, client, table, digest)
        _execute(dispatch, client, table, digest)
        dispatch.seal_readback(run_id=RUN, delivery_id=DELIVERY,
                               table=table, row_hash=digest)
    assert len(client.queries) == 2
    proof = dispatch.compact(run_id=RUN, delivery_id=DELIVERY,
                             parent_hash=PARENT)
    assert proof == activation_insert_proof(DELIVERY, PARENT)
    dispatch.close_for_cold(RUN)
    dispatch.assert_cold_receipts(RUN, {DELIVERY: proof})
    with pytest.raises(KeeperUnavailable, match="inventory differs"):
        dispatch.assert_cold_receipts(RUN, {})
    with pytest.raises(KeeperUnavailable, match="cold-fenced"):
        dispatch.reserve(RUN, "plan-1:event-2", {
            _TABLES[0]: PARENT_FAMILY, _TABLES[1]: None,
            _TABLES[2]: None, _TABLES[3]: COMMIT_FAMILY,
        })


def test_unregistered_run_rows_and_unsealed_insert_fail_closed() -> None:
    dispatch = ActivationInsertDispatch(_Client(_Store(), 11))
    with pytest.raises(KeeperUnavailable, match="unregistered"):
        dispatch.initialize_new_run(RUN, has_ch_rows=True)
    dispatch = _dispatch()
    _execute(dispatch, _ClickHouse(), _TABLES[0], PARENT_FAMILY)
    with pytest.raises(KeeperUnavailable, match="unresolved"):
        dispatch.compact(run_id=RUN, delivery_id=DELIVERY,
                         parent_hash=PARENT)


def test_registered_publisher_round_trips_normalized_strategy_one_rows() -> None:
    run_id = strategy_one_activation_run_id(
        date(2026, 8, 21), mode="paper", run_plan_id="plan-1")
    client = _ActivationClient()
    dispatch = ActivationInsertDispatch(_Client(_Store(), 11), strategy_one=True)
    dispatch.initialize_new_run(run_id, has_ch_rows=False)
    parent_hash = publish_registered_activation(
        client, dispatch, project_activation(_delivery()), run_id=run_id,
        committed_at=datetime(2026, 8, 21, 8, 11, tzinfo=timezone.utc))
    restored = load_activation(
        client, session_date=date(2026, 8, 21), run_plan_id="plan-1",
        ticker="SUGP", event_id="event-1", run_id=run_id,
        strategy_one=True)
    assert restored["delivery_id"] == DELIVERY
    assert len(client.inserts) == 4
    assert set(client.inserts) == {
        strategy_one_activation_table(name) for name in _TABLES}
    assert all(not client.rows[name] for name in _TABLES)
    dispatch.close_for_cold(run_id)
    dispatch.assert_cold_receipts(run_id, {
        DELIVERY: activation_insert_proof(DELIVERY, parent_hash)})


def test_registered_publisher_lost_response_blocks_cold_recovery() -> None:
    run_id = strategy_one_activation_run_id(
        date(2026, 8, 21), mode="paper", run_plan_id="plan-1")
    client = _ActivationClient(lose_table=strategy_one_activation_table("trading_activation_v1"),
                               persist_before_loss=True)
    dispatch = ActivationInsertDispatch(_Client(_Store(), 11), strategy_one=True)
    dispatch.initialize_new_run(run_id, has_ch_rows=False)
    with pytest.raises(TimeoutError, match="lost activation INSERT response"):
        publish_registered_activation(
            client, dispatch, project_activation(_delivery()), run_id=run_id)
    with pytest.raises(KeeperUnavailable, match="unresolved"):
        dispatch.close_for_cold(run_id)
    assert len(client.rows[strategy_one_activation_table("trading_activation_v1")]) == 1


def test_registered_publisher_rejects_legacy_dispatch_before_any_insert() -> None:
    run_id = strategy_one_activation_run_id(
        date(2026, 8, 21), mode="paper", run_plan_id="plan-1")
    client = _ActivationClient()
    dispatch = ActivationInsertDispatch(_Client(_Store(), 11))
    with pytest.raises(ValueError, match="isolated Keeper dispatch"):
        publish_registered_activation(
            client, dispatch, project_activation(_delivery()), run_id=run_id)
    assert client.inserts == []
