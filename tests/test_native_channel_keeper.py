from types import SimpleNamespace

from kazoo.security import ACL, Id, OPEN_ACL_UNSAFE, make_digest_acl
import pytest

from src.market_engine.native_channel_keeper import NativeChannelKeeperSession, BASE, ROOT


class Keeper:
    def __init__(self):
        self.acls = {'/':OPEN_ACL_UNSAFE, '/other-product':OPEN_ACL_UNSAFE}
        self.created=[]

    def exists(self,path):
        return SimpleNamespace() if path in self.acls else None

    def get_acls(self,path):
        return self.acls[path], SimpleNamespace()

    def create(self,path,value,*,acl):
        self.created.append(path);self.acls[path]=acl


def fixture(role):
    keeper=Keeper()
    reader=make_digest_acl('native_causal_channel_reader','fixture-read',read=True)
    owner=make_digest_acl('native_causal_channel_producer','fixture-producer',all=True)
    session=NativeChannelKeeperSession(SimpleNamespace(client=keeper),role,
        (owner,reader) if role=='producer' else (reader,))
    return session,keeper,owner,reader


def test_producer_installs_private_namespace_and_preserves_common_acl():
    session,keeper,owner,reader=fixture('producer')
    session.ensure_namespace();session.ensure_namespace()
    assert keeper.created == ['/market-products',BASE,BASE+'/insert-authority',ROOT]
    assert keeper.acls['/market-products'] == OPEN_ACL_UNSAFE
    assert keeper.acls['/other-product'] == OPEN_ACL_UNSAFE
    assert set(keeper.acls[ROOT]) == {owner,reader}


def test_existing_broad_native_acl_is_held_without_replacement():
    session,keeper,_,_=fixture('producer')
    keeper.acls['/market-products']=OPEN_ACL_UNSAFE
    keeper.acls[BASE]=OPEN_ACL_UNSAFE
    with pytest.raises(ValueError,match='no automatic'):
        session.ensure_namespace()
    assert keeper.acls[BASE] == OPEN_ACL_UNSAFE
    assert keeper.created == []


def test_reader_accepts_masked_digest_permissions_but_cannot_install():
    session,keeper,_,_=fixture('read')
    masked=[ACL(31,Id('digest','native_causal_channel_producer:masked')),
            ACL(1,Id('digest','native_causal_channel_reader:masked'))]
    for path in (BASE,BASE+'/insert-authority',ROOT):keeper.acls[path]=masked
    session.verify_namespace()
    with pytest.raises(ValueError,match='Read-only'):
        session.ensure_namespace()
    keeper.acls[ROOT]=[masked[0],ACL(3,masked[1].id)]
    with pytest.raises(ValueError,match='ACL'):
        session.verify_namespace()
