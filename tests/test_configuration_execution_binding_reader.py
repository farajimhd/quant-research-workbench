import json
import asyncio

import pytest

from src.backend import configuration_execution_binding_reader as reader
from src.trading_runtime.configuration_execution_binding import POLICY, SEAL
from test_configuration_execution_binding import binding


@pytest.mark.parametrize('failure', [None, 'readonly', 'missing', 'changed', 'hash'])
def test_cold_reader_checks_exact_seal_and_closes_its_owned_connection(monkeypatch, failure):
    actual = binding()
    calls = []
    class Client:
        closed = False
        seals = 0
        def execute(self, query):
            calls.append(query)
            if query == "SELECT getSetting('readonly')":
                return '0' if failure == 'readonly' else '1'
            assert query.startswith('SELECT ') and 'LIMIT 4097' in query
            if 'FROM arte.' + SEAL.name in query:
                self.seals += 1
                if failure == 'missing' or failure == 'changed' and self.seals > 1:
                    return ''
                rows = actual.rows[SEAL.name]
            else:
                assert 'FROM arte.' + POLICY.name in query
                rows = actual.rows[POLICY.name]
            return '\n'.join(json.dumps(dict(row), default=str) for row in rows)
        def close(self): self.closed = True
    client = Client()
    monkeypatch.setattr(reader, 'storage_preflight', lambda owned, tables: (
        calls.append('storage_preflight') if owned is client and tables == (POLICY, SEAL)
        else pytest.fail('Storage authority differs')))
    pins = dict(configuration_id=actual.header['configuration_id'],
        expected_configuration_hash=actual.header['configuration_hash'],
        expected_content_hash=('b' * 64 if failure == 'hash' else actual.header['content_hash']))
    if failure is None:
        result = asyncio.run(reader.load_configuration_execution_binding(lambda: client, **pins))
        assert result.header == actual.header and client.seals == 2
    else:
        with pytest.raises(ValueError):
            asyncio.run(reader.load_configuration_execution_binding(lambda: client, **pins))
    assert client.closed


@pytest.mark.parametrize('changed', [False, True])
def test_risk_reader_pins_both_reads_and_closes(monkeypatch, changed):
    from tests.test_configuration_execution_risk import contract
    from src.trading_runtime.configuration_execution_risk import RISK
    selected = contract()
    class Client:
        reads = 0
        closed = False
        def execute(self, query):
            if query == "SELECT getSetting('readonly')":
                return '1'
            self.reads += 1
            if changed and self.reads == 2:
                return ''
            assert 'FROM arte.' + RISK.name in query and 'LIMIT 4097' in query
            return json.dumps(dict(selected.header), default=str)
        def close(self): self.closed = True
    client = Client()
    monkeypatch.setattr(reader, 'storage_preflight', lambda owned, tables: None)
    pins = dict(configuration_id=selected.header['configuration_id'],
        expected_configuration_hash=selected.header['configuration_hash'],
        expected_content_hash=selected.header['content_hash'])
    if changed:
        with pytest.raises(ValueError, match='changed'):
            asyncio.run(reader.load_configuration_execution_risk(lambda: client, **pins))
    else:
        assert asyncio.run(reader.load_configuration_execution_risk(lambda: client, **pins)).header == selected.header
    assert client.closed
