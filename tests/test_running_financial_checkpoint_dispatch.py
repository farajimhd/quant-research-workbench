"""Real in-memory Keeper fences; no database or installed admission."""
import pytest
from test_arte_typed_insert_dispatch import Keeper, Client, compact_running_prefix, BATCH_ID
from src.trading_runtime.arte_typed_insert_dispatch import TypedInsertDispatch, _running_financial_token
from src.trading_runtime.keeper_ownership import KeeperUnavailable


@pytest.mark.parametrize('mutation', ['none', 'sequence', 'token', 'table', 'mixed'])
def test_running_financial_dispatch_has_own_exact_compacted_parent(mutation):
    authority=TypedInsertDispatch(Keeper())
    authority.initialize_new_run('run-1')
    compact_running_prefix(authority)
    client=Client(authority)
    table='trading_running_financial_checkpoint_v1'
    digest='f'*64
    token=_running_financial_token('run-1',1,digest,table)
    sequence=1
    extra={}
    if mutation=='sequence': sequence=2
    elif mutation=='token': token='foreign'
    elif mutation=='table': table='trading_event_v1'
    elif mutation=='mixed': extra['broker_snapshot_hash']='b'*64
    sql=(f'INSERT INTO arte.{table} (run_id) SETTINGS '
         'async_insert=1,wait_for_async_insert=1,insert_deduplicate=1,'
         f"insert_deduplication_token='{token}' FORMAT JSONEachRow\n{{}}")
    def execute():
        authority.execute_typed_insert(client,run_id='run-1',table=table,token=token,sql=sql,
            batch_id=BATCH_ID,batch_last_sequence=sequence,
            running_financial_checkpoint_hash=digest,**extra)
    if mutation!='none':
        with pytest.raises((ValueError,KeeperUnavailable)): execute()
        assert client.calls==[]
    else:
        execute()
        assert authority._read_gate('run-1')[0].inflight==1
        authority.seal_verified_operation(run_id='run-1',table=table,token=token,
            batch_id=BATCH_ID,batch_last_sequence=1,running_financial_checkpoint=True)
        assert authority._read_gate('run-1')[0].inflight==0
