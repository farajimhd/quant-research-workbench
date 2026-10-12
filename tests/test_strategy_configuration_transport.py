import asyncio
import json
import re
from threading import Lock
from types import SimpleNamespace

import pytest

from src.backend import strategy_configuration_transport as transport
from src.trading_runtime import strategy_configuration_schema as schema
from src.trading_runtime.keeper_session import ManagedKeeperSession
from tests.test_columnar_strategy_publication import Keeper
from tests.test_strategy_configuration_selection import prepare


def environment(monkeypatch, failure=None):
    monkeypatch.setattr(transport, "storage_preflight", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(transport.ConfigurationReferenceReader, "verify",
                        lambda self, selected: SimpleNamespace(selection=selected))
    stored = {table.name: () for table in schema.TABLES}
    clients, writes, lock = [], [], Lock()
    keeper = Keeper()
    keeper.client_id = (1, b"test")
    keeper.add_listener = lambda *_: None
    session = ManagedKeeperSession(keeper)
    session._on_state("CONNECTED")
    class Client:
        base_url = "http://test"
        def __init__(self, writer): self.writer, self.closed = writer, False
        def close(self): self.closed = True
        def execute(self, query):
            if query == "SELECT getSetting('readonly')": return "0" if self.writer else "1"
            if query == "SELECT currentUser()": return "configuration_writer"
            if query == "SHOW GRANTS FINAL":
                lines = [f"GRANT SELECT, INSERT ON arte.{table.name} TO configuration_writer" for table in schema.TABLES]
                lines += [f"GRANT SELECT ON system.{name} TO configuration_writer" for name in
                    ("tables", "columns", "parts", "storage_policies", "data_skipping_indices")]
                if failure == "grant": lines.append("GRANT CREATE ON arte.* TO configuration_writer")
                return "\n".join(lines)
            if query.startswith("INSERT"):
                assert self.writer
                table = re.search(r"`arte`\.`([a-z0-9_]+)`", query)[1]
                rows = tuple(json.loads(line) for line in query.splitlines()[1:])
                assert all(type(row) is dict for row in rows)
                with lock:
                    stored[table] = rows
                    writes.append(table)
                if failure == "ack" and table == schema.ACCOUNT.name: raise RuntimeError("unknown acknowledgement")
                if failure == "session" and table == schema.ACCOUNT.name:
                    session._on_state("SUSPENDED")
                    session._on_state("CONNECTED")
                return ""
            table = re.search(r"FROM arte\.([a-z0-9_]+)", query)[1]
            return "\n".join(json.dumps(dict(row), default=str) for row in stored[table])
    def factory(writer=False):
        client = Client(writer)
        clients.append(client)
        return client
    def publish(packet):
        return asyncio.run(transport.publish_configuration_selection(reader_factory=factory,
            writer_factory=lambda: factory(True), session=session, source=packet.rows,
            reference_reader=transport.ConfigurationReferenceReader("http://test", None, None, None, 2), concurrency=2))
    return stored, clients, writes, keeper, publish


def test_concrete_selection_transport_writes_defined_rows_and_exact_retry_is_readonly(monkeypatch):
    stored, clients, writes, keeper, publish = environment(monkeypatch)
    packet = prepare()
    assert publish(packet) == packet
    assert writes[-1] == schema.SELECTION.name
    assert next(iter(keeper.claims.values()))[0].startswith(b"1\ncommitted\n")
    count = len(writes)
    assert publish(packet) == packet and len(writes) == count
    assert all(client.closed for client in clients)


@pytest.mark.parametrize("failure", ["ack", "session", "grant"])
def test_transport_leaves_failed_publication_unsealed_and_never_adopts_unfinished_claim(monkeypatch, failure):
    stored, clients, writes, keeper, publish = environment(monkeypatch, failure)
    with pytest.raises((RuntimeError, ValueError, BaseExceptionGroup)):
        publish(prepare())
    assert not stored[schema.SELECTION.name]
    assert all(client.closed for client in clients)
    if failure == "grant":
        assert not keeper.claims and not writes
    else:
        assert keeper.claims
        with pytest.raises(RuntimeError, match="reconcile explicitly"):
            publish(prepare())
