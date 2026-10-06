"""Publication rejection and storage checks without database writes."""
import pytest
import json
from dataclasses import replace
from types import SimpleNamespace
from test_arte_running_financial_checkpoint import case
from src.trading_runtime.arte_running_financial_checkpoint import project_running_financial_checkpoint
from src.trading_runtime import arte_running_financial_checkpoint_publication as subject
from src.trading_runtime.arte_journal_writer import _insert


@pytest.mark.parametrize('mutation',['none','partial_conflict','lost_response'])
def test_positive_publication_and_repeat_use_real_keeper_fences(monkeypatch,mutation):
    from test_running_financial_checkpoint_head import SessionKeeper
    from src.trading_runtime.arte_typed_insert_dispatch import TypedInsertDispatch, _gate_path
    from src.trading_runtime.keeper_session import ManagedKeeperSession
    args=case(); rows=project_running_financial_checkpoint(**args)
    keeper=SessionKeeper(); authority=TypedInsertDispatch(keeper)
    run_id=args['prefix'].run_id
    authority.initialize_new_run(run_id)
    # The existing fixture helper creates run-1; explicitly establish the
    # selected test cursor using the same real Keeper gate wire contract.
    from test_arte_typed_insert_dispatch import attest_direct
    attest_direct(authority,run_id)
    gate,version=authority._read_gate(run_id)
    txn=keeper.transaction()
    txn.set_data(_gate_path(run_id),replace(gate,compacted_through=7,
        compacted_batch_id=args['prefix'].last_batch_id,compacted_commit_hash='a'*64).wire(),version=version)
    txn.commit()
    session=ManagedKeeperSession(keeper);session._on_state('CONNECTED')
    class Client:
        typed_insert_dispatch=authority
        typed_insert_strict=True
        manager_keeper_session=session
        def __init__(self): self.tables={};self.inserts=[]
        def execute(self,sql,**kwargs):
            assert sql.startswith('INSERT INTO arte.')
            table=sql.split(' ')[2].split('.')[1]
            self.inserts.append(table)
            self.tables[table]=[json.loads(r) for r in sql.split('FORMAT JSONEachRow\n',1)[1].splitlines()]
            if mutation=='lost_response': raise RuntimeError('fixture lost acknowledgement')
            return ''
    client=Client()
    if mutation=='partial_conflict':
        client.tables[subject.ACCOUNT.name]=[{'foreign':'partial content'}]
    product=SimpleNamespace(rows=rows)
    monkeypatch.setattr(subject,'reconstruct_running_financial_products',lambda *a,**k:product)
    def stored(client,sql):
        if 'system.tables' in sql:return [dict(name=t.name,storage_policy='live_market_ssd') for t in subject.TABLES]
        if 'system.storage_policies' in sql:return [dict(disks=['live_ssd'])]
        if 'system.parts' in sql:return []
        return client.tables.get(sql.split('FROM arte.')[1].split(' ')[0],[])
    monkeypatch.setattr(subject,'_rows',stored)
    def verified(client,**scope):
        assert client.tables[subject.ROOT.name]==[subject._wire_row(subject.ROOT.name,rows.root)]
        assert client.tables[subject.ACCOUNT.name]==[subject._wire_row(subject.ACCOUNT.name,r) for r in rows.accounts]
        return product
    monkeypatch.setattr(subject,'load_running_financial_checkpoint',verified)
    if mutation!='none':
        from src.trading_runtime.running_financial_checkpoint_head import ManagedRunningFinancialCheckpointHeadReader
        with pytest.raises(Exception): subject.publish_running_financial_checkpoint(client,rows)
        assert ManagedRunningFinancialCheckpointHeadReader(session).read_optional_head(run_id=run_id) is None
        calls=len(client.inserts)
        with pytest.raises(Exception): subject.publish_running_financial_checkpoint(client,rows)
        assert len(client.inserts)==calls
        return
    head=subject.publish_running_financial_checkpoint(client,rows)
    assert head.snapshot_hash==rows.root['content_hash']
    assert client.inserts==[subject.ACCOUNT.name,subject.ROOT.name]
    assert authority._read_gate(run_id)[0].inflight==0
    assert authority._read_gate(run_id)[0].registered==0
    assert subject.publish_running_financial_checkpoint(client,rows)==head
    assert len(client.inserts)==2


class NoIO:
    def execute(self,*args,**kwargs):
        raise AssertionError('Unfenced publication reached I/O')


def test_unfenced_publisher_cannot_touch_durable_client():
    with pytest.raises(RuntimeError,match='strict Keeper'):
        subject.publish_running_financial_checkpoint(NoIO(),project_running_financial_checkpoint(**case()))


def test_generic_insert_cannot_publish_checkpoint_links():
    with pytest.raises(RuntimeError,match='fenced checkpoint publisher'):
        _insert(NoIO(),subject.ROOT.name,(), 'foreign',journal_profile='backtest_v4')


@pytest.mark.parametrize('mutation',['none','policy','missing','disk','parts'])
def test_storage_preflight_verifies_policy_and_actual_parts(monkeypatch,mutation):
    def rows(client,sql):
        if 'system.tables' in sql:
            result=[dict(name=t.name,storage_policy='live_market_ssd') for t in subject.TABLES]
            if mutation=='policy': result[0]['storage_policy']='default'
            if mutation=='missing': result.pop()
            return result
        if 'system.storage_policies' in sql:
            return [dict(disks=['default' if mutation=='disk' else 'live_ssd'])]
        return [dict(disk_name='default' if mutation=='parts' else 'live_ssd')]
    monkeypatch.setattr(subject,'_rows',rows)
    if mutation=='none': subject.verify_running_financial_storage(NoIO())
    else:
        with pytest.raises(RuntimeError): subject.verify_running_financial_storage(NoIO())
