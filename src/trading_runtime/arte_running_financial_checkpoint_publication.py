"""Fenced publication of independently reconstructed running clock links."""
from .arte_running_financial_checkpoint import RunningFinancialCheckpointRows
from .arte_running_financial_checkpoint_schema import ROOT, ACCOUNT, TABLES
from .arte_running_financial_checkpoint_readback import (
    reconstruct_running_financial_products, load_running_financial_checkpoint)
from .arte_journal_writer import _literal, _rows, _wire_row
from .arte_typed_insert_dispatch import TypedInsertDispatch, _running_financial_token
from .journal_contract import canonical_json


def verify_running_financial_storage(client):
    """Verify explicit policy and actual part placement before any INSERT."""
    names = ','.join(_literal(t.name) for t in TABLES)
    tables = _rows(client, "SELECT name,storage_policy FROM system.tables WHERE database='arte' "
                   f'AND name IN ({names}) FORMAT JSONEachRow')
    if {r['name']: r['storage_policy'] for r in tables} != {t.name: 'live_market_ssd' for t in TABLES} or len(tables) != 2:
        raise RuntimeError('Running financial tables lack explicit live_market_ssd policy')
    policies = _rows(client, "SELECT disks FROM system.storage_policies WHERE policy_name='live_market_ssd' FORMAT JSONEachRow")
    disks = {d for r in policies for d in r['disks']}
    if not disks or 'default' in disks or 'hdd' in disks:
        raise RuntimeError('Running financial storage policy is absent or unsafe')
    parts = _rows(client, "SELECT DISTINCT disk_name FROM system.parts WHERE database='arte' "
                  f'AND table IN ({names}) AND active FORMAT JSONEachRow')
    if any(r['disk_name'] not in disks for r in parts):
        raise RuntimeError('Running financial parts are outside required storage')


def publish_running_financial_checkpoint(client, rows, *, first_price_source=None,
                                         declared_native_contexts=()):
    """Read committed products first; account links precede the checkpoint root."""
    if type(rows) is not RunningFinancialCheckpointRows:
        raise ValueError('Running financial publisher needs exact immutable rows')
    rows.__post_init__()
    dispatch = getattr(client, 'typed_insert_dispatch', None)
    if type(dispatch) is not TypedInsertDispatch or getattr(client, 'typed_insert_strict', False) is not True:
        raise RuntimeError('Running financial publisher lacks strict Keeper dispatch')
    scope = dict(run_id=rows.root['run_id'], batch_id=rows.root['batch_id'],
                 checkpoint_sequence=rows.root['last_sequence'], first_price_source=first_price_source,
                 declared_native_contexts=declared_native_contexts)
    rebuilt = reconstruct_running_financial_products(client, **scope)
    if rebuilt.rows != rows:
        raise RuntimeError('Running financial publisher differs from committed products')
    verify_running_financial_storage(client)
    from .running_financial_checkpoint_head import ManagedRunningFinancialCheckpointHeadReader
    head_reader = ManagedRunningFinancialCheckpointHeadReader(client.manager_keeper_session)
    previous = head_reader.read_optional_head(run_id=scope['run_id'])
    if previous is not None and previous.checkpoint_sequence == scope['checkpoint_sequence']:
        if (previous.journal_batch_id != scope['batch_id']
                or previous.snapshot_hash != rows.root['content_hash']
                or load_running_financial_checkpoint(client, **scope).rows != rows):
            raise RuntimeError('Running financial existing receipt conflicts with checkpoint')
        return previous
    operations = []
    filters = f"WHERE run_id={_literal(scope['run_id'])} AND last_sequence={scope['checkpoint_sequence']} "
    for contract, values in ((ACCOUNT, rows.accounts), (ROOT, (rows.root,))):
        wire = [_wire_row(contract.name, r) for r in values]
        columns = ','.join(n for n,_ in contract.columns)
        query = f'SELECT {columns} FROM arte.{contract.name} {filters}ORDER BY ' + (
            'ordinal' if contract is ACCOUNT else 'last_sequence') + f' LIMIT {len(wire)+1} FORMAT JSONEachRow'
        actual = _rows(client, query)
        if actual and actual != wire:
            raise RuntimeError('Running financial checkpoint has conflicting partial content')
        token = _running_financial_token(scope['run_id'], scope['checkpoint_sequence'], rows.root['content_hash'], contract.name)
        # Repeat exact acknowledged operations on retries; Keeper rejects uncertain
        # requests and binds each stable token to its exact request bytes.
        sql = (f'INSERT INTO arte.{contract.name} ({columns}) SETTINGS '
               'async_insert=1,wait_for_async_insert=1,insert_deduplicate=1,'
               f'insert_deduplication_token={_literal(token)} FORMAT JSONEachRow\n' +
               '\n'.join(canonical_json(r) for r in wire))
        dispatch.execute_typed_insert(client, run_id=scope['run_id'], table=contract.name,
            token=token, sql=sql, batch_id=scope['batch_id'], batch_last_sequence=scope['checkpoint_sequence'],
            running_financial_checkpoint_hash=rows.root['content_hash'])
        if _rows(client, query) != wire:
            raise RuntimeError('Running financial checkpoint rows did not become durable')
        operations.append((contract.name, token))
    verified = load_running_financial_checkpoint(client, **scope)
    if verified.rows != rows:
        raise RuntimeError('Running financial checkpoint final readback changed')
    for table, token in operations:
        dispatch.seal_verified_operation(run_id=scope['run_id'], table=table, token=token,
            batch_id=scope['batch_id'], batch_last_sequence=scope['checkpoint_sequence'],
            running_financial_checkpoint=True)
    dispatch.compact_verified_running_financial_checkpoint(run_id=scope['run_id'],
        batch_id=scope['batch_id'], last_sequence=scope['checkpoint_sequence'],
        snapshot_hash=rows.root['content_hash'], operations=tuple(operations), previous=previous)
    return head_reader.read_head(run_id=scope['run_id'])
