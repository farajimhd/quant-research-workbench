import asyncio
import json
from threading import Event, Lock

import pytest

from src.backend import strategy_configuration_reader as reader
from src.trading_runtime import strategy_configuration_schema as schema
from tests.test_strategy_configuration_selection import prepare, ID


class Source:
    def __init__(self, failure=None, blocked=False):
        self.packet = prepare()
        self.failure = failure
        self.clients = []
        self.lock = Lock()
        self.entered, self.release = Event(), Event()
        if not blocked:
            self.release.set()

    def factory(self):
        source = self
        class Client:
            def __init__(self):
                self.closed, self.head_reads = False, 0
            def close(self): self.closed = True
            def execute(self, sql):
                if sql == "SELECT getSetting('readonly')":
                    return "0" if source.failure == "writer" else "1"
                assert sql.startswith("SELECT ") and f"toUUID('{ID}')" in sql
                table = sql.split("FROM arte.", 1)[1].split()[0]
                rows = [dict(row) for row in source.packet.rows[table]]
                if table == schema.SELECTION.name:
                    self.head_reads += 1
                    if source.failure == "head" and self.head_reads > 1:
                        rows[0]["label"] = "changed"
                else:
                    source.entered.set()
                    assert source.release.wait(5)
                    if source.failure == "multiple" and table in {schema.ACCOUNT.name, schema.MODE.name}:
                        raise RuntimeError(table)
                raw = "\n".join(json.dumps(row, default=lambda at: at.isoformat()) for row in rows)
                if source.failure == "duplicate" and table == schema.ACCOUNT.name:
                    raw = raw.replace('"enabled": 1', '"enabled": 1, "enabled": 0')
                return raw
        client = Client()
        with self.lock:
            self.clients.append(client)
        return client


@pytest.mark.parametrize("failure", [None, "head", "duplicate", "multiple", "writer", "storage"])
def test_reader_checks_pins_seals_and_closes_all_connections(monkeypatch, failure):
    source = Source(failure)
    def storage(*_args, **_kwargs):
        if failure == "storage": raise ValueError("wrong storage placement")
    monkeypatch.setattr(reader, "storage_preflight", storage)
    kwargs = dict(configuration_id=ID, expected_content_hash=source.packet.header["content_hash"], concurrency=2)
    if failure:
        with pytest.raises((ValueError, RuntimeError, BaseExceptionGroup)):
            reader.read_configuration_selection(source.factory, **kwargs)
    else:
        assert reader.read_configuration_selection(source.factory, **kwargs) == source.packet
        assert len(source.clients) == 7
    assert source.clients and all(client.closed for client in source.clients)


def test_reader_settles_same_owned_reads_after_repeated_cancellation(monkeypatch):
    monkeypatch.setattr(reader, "storage_preflight", lambda *_args, **_kwargs: None)
    source = Source(blocked=True)
    async def run():
        task = asyncio.create_task(reader.load_configuration_selection(source.factory,
            configuration_id=ID, expected_content_hash=source.packet.header["content_hash"], concurrency=2))
        assert await asyncio.to_thread(source.entered.wait, 3)
        task.cancel()
        await asyncio.sleep(0)
        task.cancel()
        await asyncio.sleep(0)
        assert not task.done()
        source.release.set()
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(task, 5)
    asyncio.run(run())
    assert len(source.clients) == 7 and all(client.closed for client in source.clients)
