from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256

import pytest

from src.trading_runtime.arte_portfolio_sync_dispatch import (
    PortfolioSyncDispatch, _FENCE, _MARKER,
)
from src.trading_runtime.keeper_ownership import KeeperUnavailable
from test_keeper_ownership import _Client, _Store


RUN = "sync-run"
ACCOUNT = "DU1"
REVISION = 1
MARKER_HASH = "a" * 64
FENCE_HASH = "b" * 64


def _sql(table: str) -> str:
    return (f"INSERT INTO arte.{table} (run_id) SETTINGS "
            "async_insert=1,wait_for_async_insert=1,insert_deduplicate=1 "
            "FORMAT JSONEachRow\n{}")


def _token(table: str) -> str:
    return f"portfolio-sync:{RUN}:{ACCOUNT}:{REVISION}" + (
        ":marker" if table == _MARKER else "")


class Client:
    def __init__(self, *, lose: bool = False) -> None:
        self.lose = lose
        self.queries = []

    def execute(self, sql, *, query_id):
        self.queries.append((sql, query_id))
        if self.lose:
            raise TimeoutError("lost ClickHouse response")


@dataclass(frozen=True)
class Proof:
    run_id: str = RUN
    account_id: str = ACCOUNT
    state_revision: int = REVISION
    marker_hash: str = MARKER_HASH
    fence_hash: str = FENCE_HASH

    def wire(self):
        return b"exact-v2-proof"


def _dispatch():
    dispatch = PortfolioSyncDispatch(_Client(_Store(), 11))
    dispatch.initialize_new_run(RUN)
    dispatch.reserve(RUN, ACCOUNT, REVISION)
    return dispatch


def _execute(dispatch, client, table, row_hash):
    dispatch.execute(client, run_id=RUN, account_id=ACCOUNT,
                     revision=REVISION, table=table, token=_token(table),
                     sql=_sql(table), row_hash=row_hash)


def test_lost_response_stays_pending_and_cannot_seal_or_cold_fence():
    dispatch = _dispatch()
    with pytest.raises(TimeoutError, match="lost ClickHouse response"):
        _execute(dispatch, Client(lose=True), _MARKER, MARKER_HASH)
    with pytest.raises(KeeperUnavailable, match="ambiguous"):
        _execute(dispatch, Client(), _MARKER, MARKER_HASH)
    with pytest.raises(KeeperUnavailable, match="acknowledged"):
        dispatch.seal_readback(run_id=RUN, account_id=ACCOUNT,
            revision=REVISION, table=_MARKER, row_hash=MARKER_HASH)
    class Base:
        def assert_fenced(self, _run_id):
            pass
    with pytest.raises(KeeperUnavailable, match="unresolved"):
        dispatch.acquire_cold_barrier(RUN, Base(), object())


def test_acknowledged_exact_rows_compact_to_bounded_gate_and_cold_barrier():
    dispatch = _dispatch()
    client = Client()
    for table, digest in ((_MARKER, MARKER_HASH), (_FENCE, FENCE_HASH)):
        _execute(dispatch, client, table, digest)
        _execute(dispatch, client, table, digest)  # Same identity never re-dispatches.
        dispatch.seal_readback(run_id=RUN, account_id=ACCOUNT,
            revision=REVISION, table=table, row_hash=digest)
    assert len(client.queries) == 2
    with pytest.raises(KeeperUnavailable, match="proof differs"):
        dispatch.compact(run_id=RUN, account_id=ACCOUNT, revision=REVISION,
            marker_hash=MARKER_HASH, fence_hash=FENCE_HASH,
            proof=Proof(fence_hash="c" * 64))
    dispatch.compact(run_id=RUN, account_id=ACCOUNT, revision=REVISION,
        marker_hash=MARKER_HASH, fence_hash=FENCE_HASH, proof=Proof())
    dispatch.compact(run_id=RUN, account_id=ACCOUNT, revision=REVISION,
        marker_hash=MARKER_HASH, fence_hash=FENCE_HASH, proof=Proof())
    gate, _ = dispatch._read(RUN)
    assert gate.revision == 0 and gate.count == 1
    digest = sha256(Proof().wire()).hexdigest()
    assert gate.proof_xor == digest
    class Keeper:
        def load_portfolio_sync_transition_head(self, run_id, account_id):
            assert (run_id, account_id) == (RUN, None)
            return type("Head", (), {"proof_count": 1, "proof_xor": digest})(), 0
    class Base:
        def assert_fenced(self, run_id):
            assert run_id == RUN
    barrier = dispatch.acquire_cold_barrier(RUN, Base(), Keeper())
    barrier.assert_fenced(RUN)
    with pytest.raises(KeeperUnavailable, match="cold-fenced"):
        dispatch.reserve(RUN, ACCOUNT, 2)


def test_competing_sync_cannot_displace_active_transition():
    dispatch = _dispatch()
    with pytest.raises(KeeperUnavailable, match="Competing"):
        dispatch.reserve(RUN, "DU2", 1)


def test_strict_writer_routes_marker_only_through_sync_dispatch():
    from src.trading_runtime.arte_journal_writer import _insert
    from src.trading_runtime.arte_portfolio_sync import _marker
    from src.trading_runtime.arte_journal_projection import project_portfolio_reconciliation_records
    from test_arte_portfolio_sync import captured, records, identity

    image = captured()
    batch = project_portfolio_reconciliation_records(
        records(), captured=image, **identity())
    row = _marker(batch, image)
    class StrictClient(Client):
        typed_insert_strict = True
    client = StrictClient()
    token = f"portfolio-sync:{image.run_id}:{image.account_id}:{image.state_revision}:marker"
    with pytest.raises(RuntimeError, match="lacks dispatch identity"):
        _insert(client, _MARKER, (row,), token)
    dispatch = PortfolioSyncDispatch(_Client(_Store(), 11))
    dispatch.initialize_new_run(image.run_id)
    dispatch.reserve(image.run_id, image.account_id, image.state_revision)
    client.typed_sync_insert_dispatch = dispatch
    _insert(client, _MARKER, (row,), token,
            dispatch_sync_account_id=image.account_id,
            dispatch_sync_revision=image.state_revision)
    assert len(client.queries) == 1
