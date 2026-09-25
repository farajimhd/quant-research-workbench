from __future__ import annotations

from copy import deepcopy

import pytest

from src.backend.live_strategy_definition_cold_reader import cold_read_installed_definition
from src.backend.live_strategy_definition_publication import (
    KeeperDefinitionHeadFence, publish_installed_definition,
)
from src.trading_runtime.strategy_engine import STRATEGY_ID, STRATEGY_REVISION
from tests.test_live_signal_completion_keeper import FakeKazoo
from tests.test_live_strategy_definition_cold_reader import _actual_route_row


class FakeStorage:
    def __init__(self):
        self.definitions = []
        self.changes = []
        self.fail_after_change = False

    def insert_definition(self, row):
        self.definitions.append(deepcopy(row))

    def insert_enable_change(self, row):
        self.changes.append(deepcopy(row))
        if self.fail_after_change:
            raise RuntimeError("ambiguous change insert")

    def read_definition_rows(self, *, strategy_id, strategy_revision):
        return deepcopy(self.definitions)

    def read_enable_change_rows(self, *, strategy_id, strategy_revision):
        return deepcopy(self.changes)


def _case():
    storage = FakeStorage()
    keeper = KeeperDefinitionHeadFence(FakeKazoo(), endpoint="127.0.0.1:9181")
    return storage, keeper, _actual_route_row()


def test_initial_and_same_day_enable_change_publish_then_cold_recover() -> None:
    storage, keeper, saved = _case()
    first = publish_installed_definition(storage, keeper, saved, owner_id="owner-1")
    assert first.change_sequence == 1
    assert len(storage.definitions) == 1 and len(storage.changes) == 1
    assert cold_read_installed_definition(
        storage, keeper, strategy_id=STRATEGY_ID,
        strategy_revision=STRATEGY_REVISION)["enabled"] is True
    second = publish_installed_definition(
        storage, keeper, {**saved, "enabled": False}, owner_id="owner-2",
        changed_at="2026-09-24T14:00:01+00:00")
    assert second.change_sequence == 2 and second.keeper_version > first.keeper_version
    assert len(storage.definitions) == 1 and len(storage.changes) == 2
    assert cold_read_installed_definition(
        storage, keeper, strategy_id=STRATEGY_ID,
        strategy_revision=STRATEGY_REVISION)["enabled"] is False


def test_ambiguous_change_insert_never_attests_or_blindly_retries() -> None:
    storage, keeper, saved = _case()
    storage.fail_after_change = True
    with pytest.raises(RuntimeError, match="ambiguous"):
        publish_installed_definition(storage, keeper, saved, owner_id="owner-1")
    assert keeper._client.exists(keeper.path(STRATEGY_ID, STRATEGY_REVISION)) is None
    storage.fail_after_change = False
    with pytest.raises(RuntimeError, match="uncommitted rows"):
        publish_installed_definition(storage, keeper, saved, owner_id="owner-2")
    assert len(storage.definitions) == len(storage.changes) == 1


def test_keeper_contention_and_stale_holder_cas_fail_closed() -> None:
    storage, keeper, saved = _case()
    epoch = keeper.acquire(STRATEGY_ID, STRATEGY_REVISION, owner_id="other")
    assert epoch == 1
    with pytest.raises(RuntimeError, match="another owner"):
        publish_installed_definition(storage, keeper, saved, owner_id="publisher")
    assert keeper.release(STRATEGY_ID, STRATEGY_REVISION,
                          owner_id="other", epoch=epoch)
    first = publish_installed_definition(storage, keeper, saved, owner_id="publisher")
    new_epoch = keeper.acquire(STRATEGY_ID, STRATEGY_REVISION, owner_id="new")
    assert new_epoch == 3
    with pytest.raises(RuntimeError, match="lost"):
        keeper.attest(STRATEGY_ID, STRATEGY_REVISION, owner_id="publisher", epoch=2,
                      previous=first, definition_content_hash=first.definition_content_hash,
                      change_sequence=2, change_content_hash="a" * 64)


def test_holder_version_replacement_between_readback_and_attest_is_rejected() -> None:
    storage, keeper, saved = _case()
    client = keeper._client
    holder = f"{keeper._base(STRATEGY_ID, STRATEGY_REVISION)}/holder"
    original_insert = storage.insert_enable_change

    def insert_and_schedule_replacement(row):
        original_insert(row)
        def replace():
            value, version, owner = client.nodes[holder]
            client.nodes[holder] = (value, version + 1, owner)
        client.before_commit = replace

    storage.insert_enable_change = insert_and_schedule_replacement
    with pytest.raises(RuntimeError, match="CAS failed"):
        publish_installed_definition(storage, keeper, saved, owner_id="publisher")
    assert client.exists(keeper.path(STRATEGY_ID, STRATEGY_REVISION)) is None
