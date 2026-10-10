from types import SimpleNamespace
from uuid import uuid4

import pytest
from kazoo.exceptions import NodeExistsError
from kazoo.security import make_digest_acl, OPEN_ACL_UNSAFE

from src.market_engine.completed_return_keeper_bootstrap import (
    ScopedProductKeeper, open_scoped_product_keeper,
)
from src.market_engine.completed_return_insert_authority import (
    ROOT, KeeperProductInsertAuthority, InsertAuthorityUnavailable,
)
from src.trading_runtime.keeper_session import ManagedKeeperSession


ACLS = (make_digest_acl('producer', 'p' * 48, all=True),
        make_digest_acl('custodian', 'c' * 48, all=True))


class Keeper:
    connected = True
    client_state = 'CONNECTED'
    def __init__(self):
        self.nodes = {ROOT: (b'', 0, ACLS)}
        self.auth = []
        self.closed = False
        self.after_get = None
    def add_listener(self, listener): pass
    def remove_listener(self, listener): pass
    def add_auth(self, scheme, credential): self.auth.append((scheme, credential))
    def stop(self): self.connected = False
    def close(self): self.closed = True
    def get_acls(self, path):
        _, version, acl = self.nodes[path]
        return list(acl), SimpleNamespace(version=version)
    def create(self, path, value, *, acl, ephemeral=False, makepath=False):
        assert makepath is False
        if path in self.nodes: raise NodeExistsError()
        assert (path.rsplit('/', 1)[0] or '/') in self.nodes
        self.nodes[path] = (value, 0, tuple(acl))
        return path
    def get(self, path):
        value, version, _ = self.nodes[path]
        if self.after_get: self.after_get()
        return value, SimpleNamespace(version=version)
    def set(self, path, value, *, version):
        _, prior, acl = self.nodes[path]
        assert version == prior
        self.nodes[path] = (value, version + 1, acl)
    def delete(self, path, *, version, recursive):
        assert recursive is False and self.nodes[path][1] == version
        del self.nodes[path]


def managed(client):
    session = ManagedKeeperSession(client)
    session._on_state('CONNECTED')
    return session


def test_scoped_adapter_preserves_real_authority_dispatch_and_completion():
    client = Keeper()
    session = managed(client)
    adapter = ScopedProductKeeper(session, ACLS)
    authority = KeeperProductInsertAuthority(adapter)
    attempt = str(uuid4())
    writes = []
    http = SimpleNamespace(execute=lambda payload, query_id: writes.append((payload, query_id)))
    with authority.ownership(attempt) as lease:
        lease.admit_existing(False)
        lease.execute(http, authority.allowed_tables[0], b'actual-payload',
                      lambda: writes.append('verified'))
        lease.complete('a' * 64)
    authority.assert_complete(attempt, 'a' * 64)
    assert writes[0][0] == b'actual-payload' and writes[1] == 'verified'
    assert not any(path.endswith('/owner') for path in client.nodes)
    assert all(acl == ACLS for _, _, acl in client.nodes.values())
    assert any('/verified-inserts/' in path for path in client.nodes)
    session.close()


def test_uncertain_insert_remains_fenced_with_scoped_managed_adapter():
    client = Keeper()
    session = managed(client)
    authority = KeeperProductInsertAuthority(ScopedProductKeeper(session, ACLS))
    attempt = str(uuid4())
    def unknown(payload, query_id): raise TimeoutError('unknown original dispatch')
    with pytest.raises(TimeoutError):
        with authority.ownership(attempt) as lease:
            lease.admit_existing(False)
            lease.execute(SimpleNamespace(execute=unknown), authority.allowed_tables[0],
                          b'payload', lambda: pytest.fail('No readback after unknown POST'))
    with pytest.raises(InsertAuthorityUnavailable, match='unknown/unverified'):
        with authority.ownership(attempt): pytest.fail('Pending attempt admitted')
    assert b'pending' in client.nodes[ROOT + '/' + attempt + '/gate'][0]
    session.close()


@pytest.mark.parametrize('path', ['/other', ROOT + '-other/x', ROOT + '/../x',
                                 ROOT + '//x', ROOT + '/./x'])
def test_foreign_or_ambiguous_path_never_written(path):
    client = Keeper()
    session = managed(client)
    adapter = ScopedProductKeeper(session, ACLS)
    before = dict(client.nodes)
    with pytest.raises(InsertAuthorityUnavailable): adapter.ensure_path(path)
    assert client.nodes == before
    session.close()


@pytest.mark.parametrize('defect', ['root_acl', 'child_acl', 'read_only', 'lost', 'changed_client'])
def test_existing_acl_or_session_defect_rejects_mutation(defect):
    client = Keeper()
    session = managed(client)
    adapter = ScopedProductKeeper(session, ACLS)
    path = ROOT + '/attempt'
    adapter.ensure_path(path)
    if defect == 'root_acl': client.nodes[ROOT] = (b'', 0, tuple(OPEN_ACL_UNSAFE))
    if defect == 'child_acl': client.nodes[path] = (b'', 0, tuple(OPEN_ACL_UNSAFE))
    if defect == 'read_only': client.client_state = 'CONNECTED_RO'
    if defect == 'lost': session._on_state('LOST')
    if defect == 'changed_client': session.client = Keeper()
    before = dict(client.nodes)
    with pytest.raises(InsertAuthorityUnavailable): adapter.set(path, b'new', version=0)
    assert client.nodes == before
    if defect == 'changed_client': session.client = client
    session.close()


def test_disconnect_during_read_invalidates_returned_state():
    client = Keeper()
    session = managed(client)
    adapter = ScopedProductKeeper(session, ACLS)
    client.after_get = lambda: session._on_state('SUSPENDED')
    with pytest.raises(InsertAuthorityUnavailable): adapter.get(ROOT)
    session.close()


def test_factory_authenticates_and_closes_real_managed_wrapper():
    client = Keeper()
    session = managed(client)
    with open_scoped_product_keeper(username='producer', password='p' * 48,
            custodian_digest_id=ACLS[1].id.id, opener=lambda: session) as adapter:
        assert adapter.connected
        assert client.auth == [('digest', 'producer:' + 'p' * 48)]
    assert client.closed and not adapter.connected


def test_missing_root_never_implicitly_provisioned_and_factory_closes():
    client = Keeper()
    client.nodes.clear()
    session = managed(client)
    with pytest.raises(KeyError):
        with open_scoped_product_keeper(username='producer', password='p' * 48,
                custodian_digest_id=ACLS[1].id.id, opener=lambda: session):
            pytest.fail('Missing operator root admitted')
    assert not client.nodes and client.closed


@pytest.mark.parametrize('acls', [(), (ACLS[0],), (ACLS[0], ACLS[0]),
                                 tuple(OPEN_ACL_UNSAFE), (*ACLS, ACLS[0])])
def test_unapproved_acl_policy_fails_before_node_access(acls):
    client = Keeper()
    session = managed(client)
    with pytest.raises(InsertAuthorityUnavailable): ScopedProductKeeper(session, acls)
    assert len(client.nodes) == 1
    session.close()
