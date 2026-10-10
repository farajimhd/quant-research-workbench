"""Authenticated, namespace-limited composition with the managed Keeper session.

The operator must provision the product root with the exact declared digest ACLs
before opening this adapter. It never installs or repairs an ACL, adopts an
unknown dispatch, grants financial authority, or changes session discovery.
"""
from contextlib import contextmanager
import re

from kazoo.exceptions import NodeExistsError
from kazoo.security import ACL, Id, make_digest_acl

from src.trading_runtime.keeper_session import (
    ManagedKeeperSession, open_workstation_keeper_session,
)
from .completed_return_insert_authority import ROOT, InsertAuthorityUnavailable


def _acl_key(acl):
    return (acl.perms, acl.id.scheme, acl.id.id)


class ScopedProductKeeper:
    """Kazoo-shaped product interface; the managed session retains ownership."""

    def __init__(self, session, acls):
        if type(session) is not ManagedKeeperSession:
            raise InsertAuthorityUnavailable('Managed Keeper session required')
        acls = tuple(acls)
        keys = tuple(_acl_key(a) for a in acls)
        if (len(keys) != 2 or len(set(keys)) != 2
                or any(p != 31 or scheme != 'digest'
                       or not re.fullmatch(r'[A-Za-z0-9_-]+:[A-Za-z0-9+/]{27}=', identity)
                       for p, scheme, identity in keys)):
            raise InsertAuthorityUnavailable('Exact producer and custodian digest ACLs required')
        self._session, self._client = session, session.client
        self._acls, self._keys = acls, frozenset(keys)
        self._proof(ROOT)

    @property
    def connected(self):
        return self._session.client is self._client and self._session.writable

    def _path(self, path):
        if (type(path) is not str or not (path == ROOT or path.startswith(ROOT + '/'))
                or any(p in ('', '.', '..') for p in path.split('/')[1:])):
            raise InsertAuthorityUnavailable('Foreign or noncanonical product Keeper path')
        if not self.connected:
            raise InsertAuthorityUnavailable('Managed producer Keeper session unavailable')

    def _proof(self, path):
        self._path(path)
        acls, stat = self._client.get_acls(path)
        keys = tuple(_acl_key(a) for a in acls)
        if len(keys) != 2 or frozenset(keys) != self._keys:
            raise InsertAuthorityUnavailable('Product Keeper ACL differs; explicit provisioning required')
        self._path(path)
        return stat

    def ensure_path(self, path):
        self._path(path)
        self._proof(ROOT)
        parent = ROOT
        for part in path[len(ROOT):].strip('/').split('/') if path != ROOT else ():
            child = parent + '/' + part
            self._proof(parent)
            try:
                self._client.create(child, b'', acl=list(self._acls), makepath=False)
            except NodeExistsError:
                pass
            self._proof(child)
            parent = child

    def create(self, path, value, ephemeral=False):
        self._path(path)
        if path == ROOT:
            raise InsertAuthorityUnavailable('Product root installation is operator-owned')
        self._proof(ROOT)
        self._proof(path.rsplit('/', 1)[0])
        result = self._client.create(path, value, acl=list(self._acls),
                                     ephemeral=ephemeral, makepath=False)
        self._proof(path)
        return result

    def get(self, path):
        self._proof(ROOT)
        self._proof(path)
        result = self._client.get(path)
        self._path(path)
        return result

    def set(self, path, value, version):
        self._proof(ROOT)
        self._proof(path)
        if path == ROOT or type(version) is not int or version < 0:
            raise InsertAuthorityUnavailable('Versioned child-node CAS required')
        result = self._client.set(path, value, version=version)
        self._path(path)
        return result

    def delete(self, path, version):
        self._proof(ROOT)
        self._proof(path)
        if path == ROOT or type(version) is not int or version < 0:
            raise InsertAuthorityUnavailable('Versioned nonrecursive child deletion required')
        result = self._client.delete(path, version=version, recursive=False)
        self._path(path)
        return result


@contextmanager
def open_scoped_product_keeper(*, username, password, custodian_digest_id,
                              opener=open_workstation_keeper_session):
    """Private credentials are supplied by operator bootstrap, never a strategy."""
    if (type(username) is not str or not re.fullmatch(r'[A-Za-z0-9_-]+', username)
            or type(password) is not str or len(password) < 40
            or type(custodian_digest_id) is not str):
        raise InsertAuthorityUnavailable('Private producer identity is incomplete')
    producer = make_digest_acl(username, password, all=True)
    custodian = ACL(31, Id('digest', custodian_digest_id))
    session = opener()
    try:
        session.client.add_auth('digest', username + ':' + password)
        yield ScopedProductKeeper(session, (producer, custodian))
    finally:
        session.close()
