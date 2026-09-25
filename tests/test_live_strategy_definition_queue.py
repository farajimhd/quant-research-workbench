from __future__ import annotations

from copy import deepcopy
import threading

import pytest

from src.backend.live_strategy_definition_publication import (
    DefinitionPublicationQueue, KeeperDefinitionHeadFence,
    prepare_definition_publication,
)
from src.trading_runtime.strategy_engine import STRATEGY_ID
from tests.test_live_signal_completion_keeper import FakeKazoo
from tests.test_live_strategy_definition_authority import _saved


class BlockingStorage:
    def __init__(self):
        self.definitions = []
        self.changes = []
        self.entered = threading.Event()
        self.release = threading.Event()
        self.fail_after_change = False
        self.call_threads = []

    def insert_definition(self, row):
        self.call_threads.append(threading.get_ident())
        self.entered.set()
        if not self.release.wait(3):
            raise RuntimeError("test worker did not release")
        self.definitions.append(deepcopy(row))

    def insert_enable_change(self, row):
        self.call_threads.append(threading.get_ident())
        self.changes.append(deepcopy(row))
        if self.fail_after_change:
            raise RuntimeError("ambiguous definition change insert")

    def read_definition_rows(self, *, strategy_id, strategy_revision):
        return deepcopy([row for row in self.definitions
                         if row["strategy_id"] == strategy_id
                         and row["strategy_revision"] == strategy_revision])

    def read_enable_change_rows(self, *, strategy_id, strategy_revision):
        return deepcopy([row for row in self.changes
                         if row["strategy_id"] == strategy_id
                         and row["strategy_revision"] == strategy_revision])


def _queue(capacity=2):
    storage = BlockingStorage()
    keeper = KeeperDefinitionHeadFence(FakeKazoo(), endpoint="127.0.0.1:9181")
    return storage, keeper, DefinitionPublicationQueue(
        storage, keeper, owner_id="publisher", capacity=capacity)


def test_submit_is_o1_handoff_without_network_and_resists_caller_mutation() -> None:
    storage, keeper, lane = _queue()
    saved = _saved(47)
    packet = prepare_definition_publication(saved)
    saved["config"]["direction"] = "mutated"
    with pytest.raises(TypeError):
        packet.saved["config"]["direction"] = "mutated"
    caller_thread = threading.get_ident()
    receipt = lane.submit(packet)
    try:
        assert storage.entered.wait(3)
        assert not receipt.done()
        assert not receipt.cancel()
        assert all(thread != caller_thread for thread in storage.call_threads)
        storage.release.set()
        assert receipt.result(timeout=3).change_sequence == 1
    finally:
        storage.release.set()
        lane.close()


def test_queue_capacity_duplicate_pending_and_shutdown_drain() -> None:
    storage, _, lane = _queue(capacity=2)
    first = prepare_definition_publication(_saved(47))
    second = prepare_definition_publication(_saved(46))
    third = prepare_definition_publication(_saved(45))
    receipt_one = lane.submit(first)
    assert storage.entered.wait(3)
    receipt_two = lane.submit(second)
    with pytest.raises(ValueError, match="already pending"):
        lane.submit(first)
    with pytest.raises(RuntimeError, match="capacity"):
        lane.submit(third)
    with pytest.raises(RuntimeError, match="did not drain"):
        lane.close(timeout=0.01)
    storage.release.set()
    assert receipt_one.result(timeout=3).change_sequence == 1
    assert receipt_two.result(timeout=3).change_sequence == 1
    lane.close()
    with pytest.raises(RuntimeError, match="unavailable"):
        lane.submit(third)


def test_ambiguous_first_failure_halts_queued_work_without_retry() -> None:
    storage, keeper, lane = _queue()
    storage.fail_after_change = True
    first = lane.submit(prepare_definition_publication(_saved(47)))
    assert storage.entered.wait(3)
    second = lane.submit(prepare_definition_publication(_saved(46)))
    storage.release.set()
    try:
        with pytest.raises(RuntimeError, match="ambiguous"):
            first.result(timeout=3)
        with pytest.raises(RuntimeError, match="halted"):
            second.result(timeout=3)
        assert len(storage.changes) == 1
        assert keeper._client.exists(keeper.path(STRATEGY_ID, 47)) is None
        with pytest.raises(RuntimeError, match="unavailable"):
            lane.submit(prepare_definition_publication(_saved(45)))
    finally:
        lane.close()
