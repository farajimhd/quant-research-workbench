import asyncio

import pytest

from src.backend.configuration_execution_binding_publication import ConfigurationExecutionBindingPublisher
from src.trading_runtime.configuration_execution_binding import POLICY, SEAL
from test_configuration_execution_binding import binding


@pytest.mark.parametrize('failure', [None, 'ack', 'readback', 'reference'])
def test_binding_reuses_owned_publication_and_withholds_seal_on_failure(failure):
    packet = binding()
    class Storage:
        rows = {POLICY.name: (), SEAL.name: ()}
        wrote = False
        references = 0
        def preflight(self): pass
        def assert_owner(self): pass
        def verify_references(self, selected):
            self.references += 1
            assert selected.header['configuration_hash'] == packet.header['configuration_hash']
            if failure == 'reference' and self.references > 1:
                raise ValueError('Configuration reference changed')
        def read_selection(self, identity):
            return dict(self.rows) if self.rows[SEAL.name] else None
        def read_children(self, identity):
            return {POLICY.name: (() if failure == 'readback' and self.wrote else self.rows[POLICY.name])}
        def insert_child(self, name, rows):
            assert name == POLICY.name
            self.rows[name] = rows
            self.wrote = True
            if failure == 'ack': raise RuntimeError('Ambiguous acknowledgement')
        def insert_seal(self, rows):
            assert self.wrote
            self.rows[SEAL.name] = rows
    storage = Storage()
    publisher = ConfigurationExecutionBindingPublisher(storage, concurrency=1)
    async def run():
        if failure:
            with pytest.raises((ValueError, RuntimeError)):
                await publisher.publish(packet.rows)
            assert not storage.rows[SEAL.name] and publisher.failed
            with pytest.raises(RuntimeError, match='stopped'):
                await publisher.publish(packet.rows)
        else:
            assert await publisher.publish(packet.rows) == packet
            assert await publisher.publish(packet.rows) == packet
    asyncio.run(run())
