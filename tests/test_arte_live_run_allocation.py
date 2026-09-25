from __future__ import annotations

from dataclasses import replace

import pytest

from src.trading_runtime import arte_live_run_allocation as allocation_module
from src.trading_runtime.keeper_ownership import KeeperUnavailable, _ROOT
from test_keeper_ownership import _Client, _Store


NAMESPACE = "11111111-1111-4111-8111-111111111111"
REQUEST_A = "22222222-2222-4222-8222-222222222222"
REQUEST_B = "33333333-3333-4333-8333-333333333333"


def _allocator():
    keeper = _Client(_Store(), 11)
    keeper.ensure_path(f"{_ROOT}/live_run_allocator")
    keeper.ensure_path(f"{_ROOT}/live_run_allocation")
    keeper.create(allocation_module._HEAD,
                  allocation_module.LiveRunAllocatorHead(NAMESPACE, 0).wire())
    return keeper, allocation_module.LiveRunAllocator(keeper)


def test_allocator_requires_durable_genesis_and_never_reuses_sequence():
    absent = allocation_module.LiveRunAllocator(_Client(_Store(), 11))
    with pytest.raises(KeeperUnavailable, match="head is absent"):
        absent.allocate(request_id=REQUEST_A, owner_id="controller")
    _keeper, allocator = _allocator()
    first = allocator.allocate(request_id=REQUEST_A, owner_id="controller")
    second = allocator.allocate(request_id=REQUEST_B, owner_id="controller")
    assert first.run_id != second.run_id
    assert (first.sequence, second.sequence) == (1, 2)
    with pytest.raises(KeeperUnavailable, match="already allocated"):
        allocator.allocate(request_id=REQUEST_A, owner_id="controller")
    assert allocator.load(REQUEST_A)[0] == first


def test_lost_allocation_response_burns_id_and_retry_fails_closed(monkeypatch):
    keeper, allocator = _allocator()
    original = keeper.transaction
    def lost_response():
        txn = original()
        commit = txn.commit
        def lost():
            commit()
            raise TimeoutError("lost Keeper result")
        txn.commit = lost
        return txn
    monkeypatch.setattr(keeper, "transaction", lost_response)
    with pytest.raises(KeeperUnavailable, match="outcome is ambiguous"):
        allocator.allocate(request_id=REQUEST_A, owner_id="controller")
    monkeypatch.setattr(keeper, "transaction", original)
    assert allocator.load(REQUEST_A)[0].sequence == 1
    with pytest.raises(KeeperUnavailable, match="already allocated"):
        allocator.allocate(request_id=REQUEST_A, owner_id="controller")
    assert allocator.allocate(request_id=REQUEST_B, owner_id="controller").sequence == 2


def test_context_binding_requires_normalized_fence_and_keeper_receipt(monkeypatch):
    keeper, allocator = _allocator()
    allocated = allocator.allocate(request_id=REQUEST_A, owner_id="controller")
    path = allocation_module._receipt_path(REQUEST_A)
    keeper.set(path, replace(allocated, status="gates_bound").wire(), version=0)
    monkeypatch.setattr(allocation_module, "load_typed_run_context",
                        lambda _client, run_id: {"mode": "live", "run_id": run_id})
    row = {"run_id": allocated.run_id, "run_month": "2026-09-01",
           "run_hash": "a" * 64, "config_hash": "b" * 64,
           "account_count": 1, "account_hash": "c" * 64}
    monkeypatch.setattr(allocation_module, "_rows", lambda _client, _sql: [row])
    class Core:
        def assert_run_context_receipt(self, *, run_id, fence_hash):
            assert run_id == allocated.run_id and len(fence_hash) == 64
    core = Core()
    bound = allocator.bind_context(object(), core, allocated)
    assert bound.status == "context_bound" and bound.context_hash != "0" * 64
    assert allocator.verify_context_bound(object(), core, bound) == {
        "mode": "live", "run_id": allocated.run_id}
    row["account_count"] = 2
    with pytest.raises(KeeperUnavailable, match="changed after binding"):
        allocator.verify_context_bound(object(), core, bound)
