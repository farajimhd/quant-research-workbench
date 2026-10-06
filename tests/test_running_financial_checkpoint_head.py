"""Real generic dispatch CAS with in-memory Keeper; no database or source approval."""
from dataclasses import replace
import pytest
from src.trading_runtime.arte_typed_insert_dispatch import TypedInsertDispatch,_running_financial_token
from src.trading_runtime.arte_running_financial_checkpoint_schema import TABLES
from src.trading_runtime.running_financial_checkpoint_head import (
    RunningFinancialCheckpointHead,ManagedRunningFinancialCheckpointHeadReader)
from src.trading_runtime.keeper_session import ManagedKeeperSession
from src.trading_runtime.keeper_ownership import KeeperUnavailable
from test_arte_typed_insert_dispatch import Keeper,Client,Stat,BATCH_ID,compact_running_prefix

HASH='b'*64
class SessionKeeper(Keeper):
    connected=True
    client_state='CONNECTED'
    client_id=(123,b'private-fixture')
    def add_listener(self,callback): self.callback=callback

def prepare(*,seal=True,lose=False):
    keeper=SessionKeeper(); authority=TypedInsertDispatch(keeper)
    authority.initialize_new_run('run-1'); compact_running_prefix(authority)
    operations=[]
    for table in TABLES:
        token=_running_financial_token('run-1',1,HASH,table.name)
        sql=(f"INSERT INTO arte.{table.name} (run_id) SETTINGS async_insert=1,wait_for_async_insert=1,"
             f"insert_deduplicate=1,insert_deduplication_token='{token}' FORMAT JSONEachRow\n{{}}")
        authority.execute_typed_insert(Client(authority,lose_response=lose),run_id='run-1',
            table=table.name,token=token,sql=sql,batch_id=BATCH_ID,batch_last_sequence=1,
            running_financial_checkpoint_hash=HASH)
        if seal: authority.seal_verified_operation(run_id='run-1',table=table.name,token=token,
            batch_id=BATCH_ID,batch_last_sequence=1,running_financial_checkpoint=True)
        operations.append((table.name,token))
    return authority,tuple(operations)

def compact(authority,ops,**changes):
    args=dict(run_id='run-1',batch_id=BATCH_ID,last_sequence=1,snapshot_hash=HASH,
              operations=ops,previous=None);args.update(changes)
    authority.compact_verified_running_financial_checkpoint(**args)

def reader(keeper):
    session=ManagedKeeperSession(keeper);session._on_state('CONNECTED')
    return ManagedRunningFinancialCheckpointHeadReader(session)

def test_real_compaction_selects_head_and_repeat_preserves_gate_and_rows():
    authority,ops=prepare(); compact(authority,ops)
    observed=reader(authority.keeper).read_head(run_id='run-1')
    assert observed==RunningFinancialCheckpointHead('run-1',1,BATCH_ID,HASH,0)
    assert authority._read_gate('run-1')[0].registered==0
    assert authority._read_gate('run-1')[0].inflight==0
    before=dict(authority.keeper.rows);compact(authority,ops)
    assert authority.keeper.rows==before
    assert '/typed_dispatch_running_financial_head/' in reader(authority.keeper).path('run-1')

@pytest.mark.parametrize('change',[
 {'last_sequence':True},{'last_sequence':1.0},{'snapshot_hash':'0'*64},
 {'batch_id':'00000000-0000-0000-0000-000000000000'},
 {'last_sequence':2},{'batch_id':'11111111-1111-4111-8111-111111111111'},
 {'previous':object()},
])
def test_compaction_rejects_aliases_foreign_cursor_and_previous_without_head(change):
    authority,ops=prepare();before=dict(authority.keeper.rows)
    with pytest.raises((ValueError,KeeperUnavailable)):compact(authority,ops,**change)
    assert authority.keeper.rows==before

@pytest.mark.parametrize('kind',['missing','foreign','duplicate','unsealed'])
def test_requires_complete_exact_sealed_family_inventory(kind):
    authority,ops=prepare(seal=kind!='unsealed');before=dict(authority.keeper.rows)
    selected=ops[:1] if kind=='missing' else ops
    if kind=='foreign': selected=(ops[0],('foreign',ops[1][1]))
    if kind=='duplicate': selected=ops+(ops[0],)
    with pytest.raises((ValueError,KeeperUnavailable)):compact(authority,selected)
    assert authority.keeper.rows==before

def test_lost_response_keeps_no_selected_head():
    with pytest.raises(TimeoutError):prepare(lose=True)

@pytest.mark.parametrize('field,value',[
 ('checkpoint_sequence',True),('checkpoint_sequence',1.0),('checkpoint_sequence',0),
 ('journal_batch_id','00000000-0000-0000-0000-000000000000'),
 ('snapshot_hash','0'*64),('snapshot_hash','B'*64),('keeper_version',True),
 ('keeper_version',-1),('run_id','bad\nrun')])
def test_strict_head_scalar_contract(field,value):
    head=RunningFinancialCheckpointHead('run-1',1,BATCH_ID,HASH,0)
    with pytest.raises(ValueError):replace(head,**{field:value})

@pytest.mark.parametrize('wire',[
 b'',b'2\nrun-1\n1\n'+BATCH_ID.encode()+b'\n'+HASH.encode(),
 ('1\nrun-1\n01\n'+BATCH_ID+'\n'+HASH).encode(),
 ('1\nforeign\n1\n'+BATCH_ID+'\n'+HASH).encode(),
 ('1\nrun-1\n1\n'+BATCH_ID+'\n'+'0'*64).encode(),
])
def test_read_head_rejects_missing_and_corrupt_wire(wire):
    k=SessionKeeper();r=reader(k);k.rows[r.path('run-1')]=(wire,Stat())
    with pytest.raises(ValueError,match='missing or corrupt'):r.read_head(run_id='run-1')

def test_read_head_rejects_generation_change_during_get():
    k=SessionKeeper();r=reader(k);k.rows[r.path('run-1')]=(
        ('1\nrun-1\n1\n'+BATCH_ID+'\n'+HASH).encode(),Stat())
    get=k.get
    def changed(path):
        result=get(path);r._session._generation+=1;return result
    k.get=changed
    with pytest.raises(RuntimeError,match='changed during read'):r.read_head(run_id='run-1')

def test_unmanaged_and_unavailable_session_rejected():
    with pytest.raises(TypeError):ManagedRunningFinancialCheckpointHeadReader(object())
    k=SessionKeeper();s=ManagedKeeperSession(k);r=ManagedRunningFinancialCheckpointHeadReader(s)
    with pytest.raises(RuntimeError,match='unavailable'):r.read_head(run_id='run-1')

def test_optional_absence_is_distinct_from_corruption_and_missing_strict_head():
    k=SessionKeeper();r=reader(k)
    assert r.read_optional_head(run_id='run-1') is None
    with pytest.raises(ValueError):r.read_head(run_id='run-1')
    k.rows[r.path('run-1')]=(b'corrupt',Stat())
    with pytest.raises(ValueError):r.read_optional_head(run_id='run-1')

def test_optional_absence_does_not_hide_session_change():
    k=SessionKeeper();r=reader(k);get=k.get
    def changed(path):
        r._session._generation+=1
        return get(path)
    k.get=changed
    with pytest.raises(RuntimeError,match='changed during read'):r.read_optional_head(run_id='run-1')
