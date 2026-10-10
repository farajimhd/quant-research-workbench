from pathlib import Path

import pytest
from kazoo.security import OPEN_ACL_UNSAFE

from test_completed_return_keeper_bootstrap import Keeper, managed, ACLS
from src.market_engine.completed_return_insert_authority import ROOT, InsertAuthorityUnavailable
from src.market_engine.completed_return_keeper_provision import provision_product_keeper
from scripts.clickhouse import provision_completed_return_keeper as command


class OperatorKeeper(Keeper):
    def __init__(self):
        super().__init__()
        self.nodes = {'/': (b'', 0, tuple(OPEN_ACL_UNSAFE))}
        self.creates = []
        self.unknown_once = False
    def exists(self, path):
        return self.get_acls(path)[1] if path in self.nodes else None
    def create(self, path, value, **kwargs):
        result = super().create(path, value, **kwargs)
        self.creates.append(path)
        if self.unknown_once:
            self.unknown_once = False
            raise TimeoutError('unknown root installation outcome')
        return result


def run(client, events):
    session = managed(client)
    try:
        return provision_product_keeper(session, producer_acl=ACLS[0],
                                        custodian_acl=ACLS[1], progress=events.append)
    finally:
        session.close()


def test_fresh_tree_exact_acls_and_idempotent_no_rotation():
    client = OperatorKeeper()
    events = []
    result = run(client, events)
    assert result == dict(active=0, queued=0, completed=4, skipped=0, failed=0, retried=0, total=4)
    assert len(client.creates) == 4 and client.nodes[ROOT][2] == ACLS
    assert all(acl == (ACLS[1],) for path, (_, _, acl) in client.nodes.items()
               if path not in ('/', ROOT))
    before = dict(client.nodes)
    client.connected = True
    result = run(client, [])
    assert result['completed'] == 0 and result['skipped'] == 4
    assert client.nodes == before and len(client.creates) == 4


@pytest.mark.parametrize('foreign', ['/market-products', ROOT])
def test_known_foreign_acl_rejects_before_any_write(foreign):
    client = OperatorKeeper()
    client.nodes[foreign] = (b'foreign', 0, tuple(OPEN_ACL_UNSAFE))
    before = dict(client.nodes)
    with pytest.raises(InsertAuthorityUnavailable, match='no implicit migration'):
        run(client, [])
    assert client.nodes == before and not client.creates


def test_unknown_creation_stops_then_explicit_restart_proves_existing_node():
    client = OperatorKeeper()
    client.unknown_once = True
    events = []
    with pytest.raises(TimeoutError): run(client, events)
    assert events[-1]['failed'] == 1 and events[-1]['retried'] == 0
    assert client.creates == ['/market-products']
    client.connected = True
    result = run(client, [])
    assert result['completed'] == 3 and result['skipped'] == 1
    assert client.creates.count('/market-products') == 1


def test_disconnect_during_provision_does_not_continue():
    client = OperatorKeeper()
    session = managed(client)
    def progress(row):
        if row['active']: session._on_state('LOST')
    with pytest.raises(InsertAuthorityUnavailable):
        provision_product_keeper(session, producer_acl=ACLS[0], custodian_acl=ACLS[1],
                                  progress=progress)
    assert not client.creates
    session.close()


def test_private_credential_first_write_is_restricted_and_restart_preserves(tmp_path):
    path = tmp_path / 'private.env'
    calls = []
    def restrict(p):
        assert p.exists()
        calls.append(p)
    first = command.credentials(path, root_exists=False, restrict=restrict)
    before = path.read_bytes()
    second = command.credentials(path, root_exists=True, restrict=restrict)
    assert first == second and path.read_bytes() == before and len(calls) == 2
    assert len(first['PRODUCT_KEEPER_PRODUCER_PASSWORD']) >= 40
    assert first['PRODUCT_KEEPER_PRODUCER_PASSWORD'] != first['PRODUCT_KEEPER_CUSTODIAN_PASSWORD']


def test_existing_root_without_identity_never_generates_replacement(tmp_path):
    path = tmp_path / 'private.env'
    with pytest.raises(RuntimeError, match='no rotation'):
        command.credentials(path, root_exists=True, restrict=lambda p: None)
    assert not path.exists()


def test_malformed_or_incomplete_existing_private_file_is_not_overwritten(tmp_path):
    path = tmp_path / 'private.env'
    path.write_text('incomplete\n')
    with pytest.raises(RuntimeError):
        command.credentials(path, root_exists=False, restrict=lambda p: None)
    assert path.read_text() == 'incomplete\n'


def test_dry_run_connects_and_writes_nothing(monkeypatch, capsys):
    monkeypatch.setattr(command, 'open_workstation_keeper_session', lambda **kw: pytest.fail('Connected'))
    assert command.main([]) == 0
    output = capsys.readouterr().out
    assert 'Dry run' in output and 'No connections or writes' in output
