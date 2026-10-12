import asyncio
from threading import Lock, Event

import pytest

from src.backend.strategy_configuration_publication import ConfigurationSelectionPublisher
from src.trading_runtime import strategy_configuration_schema as schema
from tests.test_strategy_configuration_selection import prepare


class Storage:
    def __init__(self, failure=None):
        self.failure = failure
        self.rows = {table.name: () for table in schema.TABLES}
        self.lock = Lock()
        self.finished = []
        self.references = 0
        self.entered, self.release = Event(), Event()
        if failure != "cancel": self.release.set()
    def preflight(self):
        if self.failure == "storage": raise ValueError("wrong storage")
    def assert_owner(self):
        if self.failure == "owner" and self.finished: raise RuntimeError("lost owner")
    def verify_references(self, packet):
        self.references += 1
        if self.failure == "reference" and self.references > 1: raise ValueError("changed reference")
    def read_selection(self, identity):
        return dict(self.rows) if self.rows[schema.SELECTION.name] else None
    def read_children(self, identity):
        result = {table.name: self.rows[table.name] for table in schema.CHILDREN}
        if self.failure == "readback" and self.finished: result[schema.ACCOUNT.name] = ()
        return result
    def insert_child(self, table, rows):
        self.entered.set()
        assert self.release.wait(5)
        with self.lock:
            self.rows[table] = rows
            self.finished.append(table)
        if self.failure == "ack" and table == schema.ACCOUNT.name: raise RuntimeError("unknown acknowledgement")
    def insert_seal(self, rows):
        assert len(self.finished) == len(schema.CHILDREN)
        self.rows[schema.SELECTION.name] = rows


@pytest.mark.parametrize("failure", [None, "storage", "owner", "reference", "readback", "ack"])
def test_selection_publication_seals_only_complete_verified_children(failure):
    storage = Storage(failure)
    publisher = ConfigurationSelectionPublisher(storage, concurrency=2)
    packet = prepare()
    async def run():
        if failure:
            with pytest.raises((ValueError, RuntimeError, BaseExceptionGroup)):
                await publisher.publish(packet.rows)
            assert publisher.failed and not publisher.busy
            assert not storage.rows[schema.SELECTION.name]
            with pytest.raises(RuntimeError, match="stopped"): await publisher.publish(packet.rows)
        else:
            assert await publisher.publish(packet.rows) == packet
            assert await publisher.publish(packet.rows) == packet
            assert len(storage.finished) == len(schema.CHILDREN)
    asyncio.run(run())


def test_selection_publication_drains_owned_writes_on_repeated_cancel():
    storage = Storage("cancel")
    publisher = ConfigurationSelectionPublisher(storage, concurrency=2)
    async def run():
        task = asyncio.create_task(publisher.publish(prepare().rows))
        assert await asyncio.to_thread(storage.entered.wait, 3)
        task.cancel()
        await asyncio.sleep(0)
        task.cancel()
        await asyncio.sleep(0)
        assert not task.done() and publisher.busy
        storage.release.set()
        with pytest.raises(asyncio.CancelledError): await asyncio.wait_for(task, 5)
        assert publisher.failed and not publisher.busy
        assert len(storage.finished) == len(schema.CHILDREN)
    asyncio.run(run())


def test_unsealed_children_cannot_be_retried_and_identity_cannot_be_replaced():
    packet = prepare()
    storage = Storage()
    storage.rows[schema.ACCOUNT.name] = packet.rows[schema.ACCOUNT.name]
    with pytest.raises(ValueError, match="explicit recovery"):
        asyncio.run(ConfigurationSelectionPublisher(storage, concurrency=2).publish(packet.rows))
    assert not storage.finished and not storage.rows[schema.SELECTION.name]
    storage = Storage()
    asyncio.run(ConfigurationSelectionPublisher(storage, concurrency=2).publish(packet.rows))
    changed = prepare(label="Changed")
    with pytest.raises(ValueError, match="different immutable selection"):
        asyncio.run(ConfigurationSelectionPublisher(storage, concurrency=2).publish(changed.rows))
    assert storage.rows[schema.SELECTION.name] == packet.rows[schema.SELECTION.name]
