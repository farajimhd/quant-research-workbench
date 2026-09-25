"""V3 terminal proof is separate from V2 and epoch-CAS bound."""
from __future__ import annotations

import asyncio
from hashlib import sha256

import pytest

from src.backend.backtest_terminal_v3_keeper import (
    attest_terminal_v3, load_terminal_v3_receipt,
)
from src.trading_runtime.keeper_ownership import KeeperOwnershipCoordinator, KeeperUnavailable, _path
from src.trading_runtime.arte_typed_insert_dispatch import typed_insert_query_id
from tests.test_keeper_ownership import _Client, _Store


RUN = "run-v3-terminal"
BATCH = "00000000-0000-0000-0000-000000000b04"


def test_v3_keeper_terminal_receipt_cas_and_cold_read():
    async def run():
        coordinator = KeeperOwnershipCoordinator(_Client(_Store(), 17))
        query_id = typed_insert_query_id(RUN, "trading_backtest_terminal_commit_v3",
                                         f"terminal-v3:{BATCH}:commit")
        operation_path = _path("backtest_terminal_v3_operations", RUN, BATCH) + "/" + query_id
        coordinator._client.ensure_path(operation_path.rsplit("/", 1)[0])
        operation_value = (f"1\n{RUN}\n{BATCH}\ntrading_backtest_terminal_commit_v3\n"
                           f"terminal-v3:{BATCH}:commit\n"
                           f"{query_id}\n{'a' * 64}\nacknowledged").encode()
        coordinator._client.create(operation_path, operation_value)
        operations = ((operation_path, 0, sha256(operation_value).hexdigest()),)
        async with coordinator.claim_portfolio_snapshot(RUN, "DU1") as lease:
            seal = {"run_id": RUN, "batch_id": BATCH, "content_hash": "a" * 64}
            accounts = {"DU1": {"state_hash": "b" * 64}}
            proof = attest_terminal_v3(
                coordinator, (("DU1", lease),), run_id=RUN,
                seal=seal, accounts=accounts, operations=operations)
            assert proof.startswith(b"4\n")
            assert load_terminal_v3_receipt(
                coordinator, run_id=RUN, batch_id=BATCH) == proof
            assert attest_terminal_v3(
                coordinator, (("DU1", lease),), run_id=RUN,
                seal=seal, accounts=accounts, operations=operations) == proof
            with pytest.raises(KeeperUnavailable, match="conflicts"):
                attest_terminal_v3(
                    coordinator, (("DU1", lease),), run_id=RUN,
                    seal={**seal, "content_hash": "c" * 64}, accounts=accounts,
                    operations=operations)
    asyncio.run(run())


def test_v3_keeper_rejects_unheld_claim_without_proof():
    async def run():
        coordinator = KeeperOwnershipCoordinator(_Client(_Store(), 19))
        async with coordinator.claim_portfolio_snapshot(RUN, "DU1") as lease:
            stale = dict(lease)
        with pytest.raises(KeeperUnavailable):
            attest_terminal_v3(
                coordinator, (("DU1", stale),), run_id=RUN,
                seal={"run_id": RUN, "batch_id": BATCH},
                accounts={"DU1": {"state_hash": "b" * 64}},
                operations=(("/missing", 0, "a" * 64),))
        assert load_terminal_v3_receipt(
            coordinator, run_id=RUN, batch_id=BATCH) is None
    asyncio.run(run())
