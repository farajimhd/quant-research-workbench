"""Private digest-ACL native product namespace; common Keeper paths are preserved."""
from dataclasses import dataclass, field

from kazoo.exceptions import NodeExistsError
from kazoo.security import make_digest_acl

from src.backend.native_channel_clients import private_native_channel_credentials, PRINCIPALS
from src.market_engine.native_channel_insert_authority import ROOT
from src.trading_runtime.keeper_session import open_workstation_keeper_session

BASE = '/market-products/native-causal-channels'


@dataclass(frozen=True, slots=True)
class NativeChannelKeeperSession:
    session: object = field(repr=False)
    role: str
    expected_acl: tuple = field(repr=False)

    @property
    def client(self):
        return self.session.client

    def close(self):
        self.session.close()

    def _verify_acl(self, path):
        acl, _ = self.client.get_acls(path)
        if self.role == 'producer':
            if set(acl) != set(self.expected_acl) or len(acl) != len(self.expected_acl):
                raise ValueError('Native product Keeper ACL differs; no automatic replacement')
        else:
            # ZooKeeper may mask digest hashes for READ-only getACL callers.
            # Successful authenticated access proves the reader digest; inspect
            # exact schemes, principal names and permissions without ADMIN.
            reader = [a for a in acl if a.id.scheme == 'digest' and
                      a.id.id.startswith(PRINCIPALS['read'] + ':') and a.perms == 1]
            owner = [a for a in acl if a.id.scheme == 'digest' and
                     a.id.id.startswith(PRINCIPALS['producer'] + ':') and a.perms == 31]
            if len(acl) != 2 or len(reader) != 1 or len(owner) != 1:
                raise ValueError('Native product Keeper lacks exact private reader/owner ACL')

    def verify_namespace(self):
        for path in (BASE, BASE+'/insert-authority', ROOT):
            self._verify_acl(path)

    def ensure_namespace(self):
        if self.role != 'producer':
            raise ValueError('Read-only native Keeper session cannot install a namespace')
        # A newly needed organizational parent inherits the existing Keeper root
        # policy. Never rewrite a common parent or any other product's ACL.
        parent = '/market-products'
        if self.client.exists(parent) is None:
            inherited, _ = self.client.get_acls('/')
            try:
                self.client.create(parent, b'', acl=inherited)
            except NodeExistsError:
                pass
        for path in (BASE, BASE+'/insert-authority', ROOT):
            if self.client.exists(path) is None:
                try:
                    self.client.create(path, b'', acl=list(self.expected_acl))
                except NodeExistsError:
                    pass
            self._verify_acl(path)


def open_native_channel_keeper(role, *, environment=None, client_factory=None):
    """Owner can create/update; the feature reader has READ only in this namespace."""
    _, user, password = private_native_channel_credentials(role, environment=environment)
    reader_acl = make_digest_acl(user, password, read=True) if role == 'read' else None
    if role == 'producer':
        producer_url, _, _ = private_native_channel_credentials('producer', environment=environment)
        reader_url, reader_user, reader_password = private_native_channel_credentials('read', environment=environment)
        if producer_url != reader_url:
            raise ValueError('Native Keeper roles have different market endpoints')
        acl = (make_digest_acl(user, password, all=True),
               make_digest_acl(reader_user, reader_password, read=True))
    else:
        acl = (reader_acl,)
    if client_factory is None:
        from kazoo.client import KazooClient
        client_factory = KazooClient
    def factory(**kwargs):
        return client_factory(**kwargs, auth_data=[('digest', f'{user}:{password}')], default_acl=list(acl))
    session = open_workstation_keeper_session(client_factory=factory)
    return NativeChannelKeeperSession(session, role, acl)
