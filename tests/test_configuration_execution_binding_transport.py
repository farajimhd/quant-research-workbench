import asyncio
import json
import re
from types import SimpleNamespace

import pytest

from src.backend import configuration_execution_binding_transport as transport
from src.backend import strategy_configuration_transport as base
from src.trading_runtime import strategy_configuration_schema as selections
from src.trading_runtime.configuration_execution_binding import POLICY, SEAL, TABLES
from src.trading_runtime.keeper_session import ManagedKeeperSession
from tests.test_columnar_strategy_publication import Keeper
from tests.product_binary_fixture import product_request_as_json
from test_configuration_execution_binding import binding


@pytest.mark.parametrize('failure', [None, 'ack', 'session', 'grant'])
def test_concrete_execution_transport_preserves_immutable_claims_and_seal_last(monkeypatch, failure):
    monkeypatch.setattr(transport, 'storage_preflight', lambda *_args, **_kwargs: None)
    monkeypatch.setattr(base, 'storage_preflight', lambda *_args, **_kwargs: None)
    monkeypatch.setattr(transport, 'read_configuration_selection', lambda *_args, **_kwargs:
        SimpleNamespace(header={'oms_limit_offset_bps': 5.}))
    packet = binding()
    stored = {table.name: () for table in TABLES}
    clients, writes = [], []
    keeper = Keeper()
    keeper.client_id = (1, b'test')
    keeper.add_listener = lambda *_: None
    session = ManagedKeeperSession(keeper)
    session._on_state('CONNECTED')
    class Client:
        base_url = 'http://test'
        def __init__(self, writer): self.writer, self.closed = writer, False
        def close(self): self.closed = True
        def execute(self, query):
            if isinstance(query, bytes): query = product_request_as_json(query, TABLES)
            if query == "SELECT getSetting('readonly')": return '0' if self.writer else '1'
            if query == 'SELECT currentUser()': return 'configuration_writer'
            if query == 'SHOW GRANTS FINAL':
                lines = [f'GRANT SELECT, INSERT ON arte.{table.name} TO configuration_writer'
                    for table in (*selections.TABLES, *TABLES)]
                lines += [f'GRANT SELECT ON system.{name} TO configuration_writer' for name in
                    ('tables', 'columns', 'parts', 'storage_policies', 'data_skipping_indices')]
                if failure == 'grant': lines.append('GRANT CREATE ON arte.* TO configuration_writer')
                return '\n'.join(lines)
            if query.startswith('INSERT'):
                assert self.writer
                name = re.search(r'`arte`\.`([a-z0-9_]+)`', query)[1]
                stored[name] = tuple(json.loads(line) for line in query.splitlines()[1:])
                writes.append(name)
                if name == POLICY.name:
                    if failure == 'ack': raise RuntimeError('Unknown acknowledgement')
                    if failure == 'session':
                        session._on_state('SUSPENDED'); session._on_state('CONNECTED')
                return ''
            name = re.search(r'FROM arte\.([a-z0-9_]+)', query)[1]
            return '\n'.join(json.dumps(dict(row), default=str) for row in stored[name])
    def factory(writer=False):
        client = Client(writer); clients.append(client); return client
    def publish():
        return asyncio.run(transport.publish_configuration_execution_binding(reader_factory=factory,
            writer_factory=lambda: factory(True), session=session, source=packet.rows, concurrency=1))
    if failure:
        with pytest.raises((ValueError, RuntimeError)):
            publish()
        assert not stored[SEAL.name]
        if failure != 'grant':
            with pytest.raises(RuntimeError, match='reconcile explicitly'):
                publish()
    else:
        assert publish() == packet
        assert writes == [POLICY.name, SEAL.name]
        assert publish() == packet and len(writes) == 2
    assert all(client.closed for client in clients)
