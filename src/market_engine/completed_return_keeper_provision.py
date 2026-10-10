"""Explicit product-tree installation; existing ACLs are never rewritten."""
from kazoo.exceptions import NodeExistsError

from src.trading_runtime.keeper_session import ManagedKeeperSession
from .completed_return_insert_authority import ROOT, InsertAuthorityUnavailable
from .completed_return_keeper_bootstrap import ScopedProductKeeper, _acl_key


def provision_product_keeper(session, *, producer_acl, custodian_acl, progress):
    if type(session) is not ManagedKeeperSession or not callable(progress):
        raise InsertAuthorityUnavailable('Managed operator session and progress sink required')
    # Reuse the adapter's exact digest policy validation without opening a node.
    keys = (_acl_key(producer_acl), _acl_key(custodian_acl))
    import re
    if (len(set(keys)) != 2 or any(p != 31 or s != 'digest'
            or not re.fullmatch(r'[A-Za-z0-9_-]+:[A-Za-z0-9+/]{27}=', i)
            for p, s, i in keys)):
        raise InsertAuthorityUnavailable('Exact distinct producer/custodian digest ACLs required')
    client = session.client
    paths = tuple('/' + '/'.join(ROOT.split('/')[1:i])
                  for i in range(2, len(ROOT.split('/')) + 1))
    expected = {p: (producer_acl, custodian_acl) if p == ROOT else (custodian_acl,)
                for p in paths}

    def healthy():
        if session.client is not client or not session.writable:
            raise InsertAuthorityUnavailable('Operator Keeper session unavailable')

    def verify(path):
        healthy()
        acls, _ = client.get_acls(path)
        if (len(acls) != len(expected[path])
                or {_acl_key(a) for a in acls} != {_acl_key(a) for a in expected[path]}):
            raise InsertAuthorityUnavailable('Existing product-tree ACL differs; no implicit migration')
        healthy()

    # Reject known foreign ancestors before any write or partial installation.
    for path in paths:
        healthy()
        if client.exists(path) is not None:
            verify(path)
        healthy()
    completed = skipped = 0
    for index, path in enumerate(paths):
        progress(dict(active=1, queued=len(paths)-index-1, completed=completed,
                      skipped=skipped, failed=0, retried=0, path=path))
        try:
            healthy()
            if client.exists(path) is not None:
                verify(path)
                skipped += 1
            else:
                if index:
                    verify(paths[index-1])
                try:
                    client.create(path, b'', acl=list(expected[path]), makepath=False)
                except NodeExistsError:
                    # Only an explicit server NodeExists response is a race.
                    # Unknown creation outcomes stop; later invocation verifies.
                    pass
                verify(path)
                completed += 1
        except BaseException:
            progress(dict(active=0, queued=len(paths)-index-1, completed=completed,
                          skipped=skipped, failed=1, retried=0, path=path))
            raise
    for path in paths:
        verify(path)
    ScopedProductKeeper(session, (producer_acl, custodian_acl))
    result=dict(active=0, queued=0, completed=completed, skipped=skipped,
                failed=0, retried=0, total=len(paths))
    progress(result)
    return result
