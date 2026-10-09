"""Issued, immutable selected manager INSERT image at a genuine V4 cursor.

This capability authorizes only its exact rows on its exact profile/client.
It does not attest financial roots, readback, or a selected Keeper head.
Those additional publication gates must complete before an exit is executable.
"""
from dataclasses import dataclass
from hashlib import sha256
from uuid import UUID
from weakref import WeakKeyDictionary
from types import MappingProxyType
import json

_ISSUED=WeakKeyDictionary()
_READBACKS=WeakKeyDictionary()
_MAX_RESPONSE_BYTES=64*1024*1024


@dataclass(frozen=True,slots=True,eq=False,weakref_slot=True)
class StructuralRejectionManagerPublication:
    profile: object
    run_id: str
    sequence: int
    batch_id: str
    rows_json: str


@dataclass(frozen=True,slots=True,eq=False,weakref_slot=True)
class StructuralRejectionManagerReadback:
    publication: StructuralRejectionManagerPublication


def selected_manager_head_path(run_id):
    from .keeper_ownership import _ROOT,_identity
    _identity(run_id,'run')
    return f'{_ROOT}/structural-rejection-manager-snapshot/v1/{sha256(run_id.encode()).hexdigest()}/head'


def manager_head_reader(session):
    from .strategy_one_management_snapshot import ManagedManagerSnapshotHeadReader
    class StructuralRejectionHeadReader(ManagedManagerSnapshotHeadReader):
        path=staticmethod(selected_manager_head_path)
    return StructuralRejectionHeadReader(session)


def _canonical(value):
    return json.dumps(value,sort_keys=True,separators=(',',':'),allow_nan=False)


def _cursor(client,owner,sequence,batch_id,boundary):
    from .arte_journal_commit_v4 import load_writer_v4_snapshot_prefix
    from .arte_journal_projection import load_latest_backtest_cursor
    prefix=load_writer_v4_snapshot_prefix(client,owner.manager.runtime.run_id,
                                         first_price_source=owner.price_authority)
    if (prefix is None or prefix.status!='running' or prefix.last_sequence!=sequence
            or prefix.last_batch_id!=batch_id):
        raise ValueError('Structural rejection publication lacks actual running V4 cursor')
    cursor=load_latest_backtest_cursor(client,prefix)
    expected={'run_id':owner.manager.runtime.run_id,'event_sequence':sequence,
              'batch_id':batch_id,'boundary_ms':boundary,
              'session_date':owner.manager.runtime.config.anchor_date.isoformat()}
    if type(cursor) is not dict or any(cursor.get(k)!=v for k,v in expected.items()):
        raise ValueError('Structural rejection publication differs from actual source cursor')
    return prefix


def issue_manager_publication(client,state,*,sequence,batch_id):
    from .profit_armed_structural_rejection_profile import require_native_structural_rejection_profile
    from src.backend.backtest_profit_armed_structural_rejection_management import require_structural_rejection_capture
    from .profit_armed_structural_rejection_snapshot import project_structural_rejection_snapshot,CHILDREN,PARENT
    from .strategy_one_management_snapshot import SOURCE,BREAK,HIGH,CLOSED,FIRST_HELD
    from .strategy_one_protection_snapshot import TABLES as PROTECTION
    profile=require_native_structural_rejection_profile(getattr(client,'structural_rejection_profile',None))
    owner=require_structural_rejection_capture(state)
    require_native_structural_rejection_profile(profile,owner=owner)
    if (type(sequence) is not int or sequence<1 or type(batch_id) is not str
            or str(UUID(batch_id))!=batch_id or UUID(batch_id).int==0):
        raise ValueError('Structural rejection publication requires exact committed sequence/batch')
    _cursor(client,owner,sequence,batch_id,state.boundary_ms)
    rows=project_structural_rejection_snapshot(run_id=owner.manager.runtime.run_id,
        session_date=owner.manager.runtime.config.anchor_date,checkpoint_sequence=sequence,state=state)
    inherited=rows.inherited
    families={PROTECTION[0].name:(inherited.protection.snapshot,),
              PROTECTION[1].name:inherited.protection.states,
              PROTECTION[2].name:inherited.protection.resistances,
              SOURCE.name:inherited.sources,BREAK.name:inherited.pending_breaks,
              HIGH.name:inherited.position_highs,CLOSED.name:inherited.closed_positions,
              FIRST_HELD.name:inherited.first_held_boundaries,
              **{t.name:v for t,v in zip(CHILDREN,(rows.states,rows.bars,rows.links,rows.levels))},
              PARENT.name:(rows.snapshot,)}
    result=StructuralRejectionManagerPublication(profile,owner.manager.runtime.run_id,
                                                  sequence,batch_id,_canonical(families))
    immutable=MappingProxyType({name:tuple(MappingProxyType(dict(row)) for row in values)
                               for name,values in families.items()})
    row_wires=MappingProxyType({name:_canonical(values) for name,values in families.items()})
    sql_wires=MappingProxyType({name:_dispatch_sqls(name,values,result) for name,values in families.items() if values})
    _ISSUED[result]=(profile,client,result.run_id,sequence,batch_id,result.rows_json,
                     immutable,row_wires,sql_wires)
    return result


def _bound(context,*,client=None):
    from .profit_armed_structural_rejection_profile import require_native_structural_rejection_profile
    if type(context) is not StructuralRejectionManagerPublication or context not in _ISSUED:
        raise ValueError('Unissued structural rejection manager publication')
    profile,issued_client,run,sequence,batch,wire,*_=binding=_ISSUED[context]
    require_native_structural_rejection_profile(profile)
    if (context.profile is not profile or client is not None and client is not issued_client
            or (context.run_id,context.sequence,context.batch_id,context.rows_json)!=(run,sequence,batch,wire)
            or getattr(issued_client,'structural_rejection_profile',None) is not profile):
        raise ValueError('Structural rejection publication changed client/profile/image/cursor')
    return binding


def require_manager_publication(context,*,client=None):
    # Public callers receive independent copies; transport guards use the
    # once-frozen private inventory, never repeatedly decode the whole image.
    return json.loads(_bound(context,client=client)[5])


def verify_manager_insert(context,*,client,table,rows,run_id,sequence,batch_id,snapshot_hash):
    from .profit_armed_structural_rejection_snapshot import PARENT
    binding=_bound(context,client=client)
    families=binding[6]
    if ((run_id,sequence,batch_id,snapshot_hash)!=(context.run_id,context.sequence,
            context.batch_id,families[PARENT.name][0]['content_hash'])
            or table not in families or _canonical(rows)!=binding[7][table]):
        raise ValueError('Structural rejection INSERT differs from exact issued inventory')
    return families


def _dispatch_sqls(table,rows,context):
    from .arte_journal_writer import _CONTRACTS,_wire_row,_literal,canonical_json
    from .arte_typed_insert_dispatch import _manager_token
    from .profit_armed_structural_rejection_snapshot import PARENT
    root=json.loads(context.rows_json)[PARENT.name][0]
    token=_manager_token(context.run_id,context.sequence,root['content_hash'],table)
    contract=_CONTRACTS[table]
    wire=tuple(_wire_row(table,row) for row in rows)
    header=(f"INSERT INTO arte.{table} ({','.join(n for n,_ in contract.columns)}) "
        "SETTINGS async_insert=1,wait_for_async_insert=1,insert_deduplicate=1,"
        "precise_float_parsing=1,"
        f"insert_deduplication_token={_literal(token)} FORMAT ")
    from .arte_journal_rowbinary import encode_journal_rows
    return (header+'JSONEachRow\n'+'\n'.join(canonical_json(row) for row in wire),
            (header+'RowBinary\n').encode('utf-8')+encode_journal_rows(contract.columns,wire))


def verify_manager_dispatch(context,*,client,table,sql,run_id,sequence,batch_id,snapshot_hash,token):
    """Direct dispatch is bound to a once-built complete per-table request."""
    from .profit_armed_structural_rejection_snapshot import PARENT
    from .arte_typed_insert_dispatch import _manager_token
    binding=_bound(context,client=client);families=binding[6]
    if ((run_id,sequence,batch_id,snapshot_hash)!=(context.run_id,context.sequence,
            context.batch_id,families[PARENT.name][0]['content_hash'])
            or table not in binding[8]
            or token!=_manager_token(run_id,sequence,snapshot_hash,table)
            or type(sql) not in (str,bytes) or sql!=binding[8][table][type(sql) is bytes]):
        raise ValueError('Structural rejection dispatch SQL differs from complete issued rows')
    return families


def readback_manager_publication(context,*,client):
    """Verify every exact child, including empty inventories, before a head.

    The finite reads use expected count+1 and never truncate or adopt orphan
    rows. This receipt alone still does not attest financial roots.
    """
    from src.backend.backtest_market_data import assert_select_only
    from .arte_journal_writer import _CONTRACTS,_wire_row,_literal
    from .profit_armed_structural_rejection_snapshot import PARENT
    binding=_bound(context,client=client);families=binding[6]
    owner=context.profile.owner
    root=families[PARENT.name][0]
    _cursor(client,owner,context.sequence,context.batch_id,root['boundary_ms'])
    for name,expected in families.items():
        contract=_CONTRACTS[name]
        expected_bytes=len(binding[7][name].encode('utf-8'))
        if expected_bytes>_MAX_RESPONSE_BYTES:
            raise ValueError('Structural rejection expected family exceeds bounded readback bytes')
        response_bound=min(_MAX_RESPONSE_BYTES,max(65536,4*expected_bytes+65536))
        projection=','.join(f'toString({column}) AS {column}' if 'Decimal(' in kind else column
                            for column,kind in contract.columns)
        sql=assert_select_only(f'SELECT {projection} FROM arte.{name} '
            f"WHERE snapshot_id=toUUID({_literal(root['snapshot_id'])}) "
            f'LIMIT {len(expected)+1} FORMAT JSONEachRow')
        response=client.execute(sql)
        if type(response) is not str:
            raise ValueError('Structural rejection readback requires bounded JSONEachRow text')
        if len(response)>response_bound or len(response.encode('utf-8'))>response_bound:
            raise ValueError('Structural rejection readback exceeds explicit response byte bound')
        observed=tuple(json.loads(line) for line in response.splitlines() if line.strip())
        if (len(observed)!=len(expected)
                or sorted((_canonical(_wire_row(name,row)) for row in observed))
                !=sorted((_canonical(_wire_row(name,row)) for row in expected))):
            raise ValueError('Structural rejection complete readback differs: '+name)
    _cursor(client,owner,context.sequence,context.batch_id,root['boundary_ms'])
    result=StructuralRejectionManagerReadback(context)
    _READBACKS[result]=(context,client,binding)
    return result


def require_manager_readback(receipt,*,client):
    if type(receipt) is not StructuralRejectionManagerReadback or receipt not in _READBACKS:
        raise ValueError('Unissued structural rejection complete readback')
    context,issued_client,binding=_READBACKS[receipt]
    if (receipt.publication is not context or client is not issued_client
            or _bound(context,client=client) is not binding):
        raise ValueError('Structural rejection readback changed its publication/client')
    return context


def publish_manager_publication(client,session,context,financial_capture):
    """Worker only: all rows/readback/financial roots, then own head CAS."""
    from .keeper_session import ManagedKeeperSession
    from .arte_typed_insert_dispatch import TypedInsertDispatch,_manager_token
    from .arte_journal_writer import _insert
    from .profit_armed_structural_rejection_snapshot import PARENT
    from .profit_armed_structural_rejection_financial_checkpoint import require_financial_capture,verify_financial_capture
    if (type(session) is not ManagedKeeperSession or not session.writable
            or getattr(client,'typed_insert_strict',False) is not True
            or type(getattr(client,'typed_insert_dispatch',None)) is not TypedInsertDispatch
            or client.typed_insert_dispatch.keeper is not session.client):
        raise ValueError('Structural rejection head requires actual fenced writer/managed session')
    require_financial_capture(financial_capture,profile=context.profile)
    families=_bound(context,client=client)[6];root=families[PARENT.name][0]
    reader=manager_head_reader(session)
    previous=reader.read_head(run_id=context.run_id) if session.client.exists(reader.path(context.run_id)) is not None else None
    if previous is not None and (previous.checkpoint_sequence>context.sequence
            or previous.checkpoint_sequence==context.sequence and (
                previous.snapshot_hash!=root['content_hash'] or previous.journal_batch_id!=context.batch_id)):
        raise ValueError('Structural rejection manager head conflicts or would rewind')
    if previous is not None and previous.checkpoint_sequence==context.sequence:
        readback_manager_publication(context,client=client)
        verify_financial_capture(financial_capture,context,client=client,session=session)
        return previous
    operations=[]
    for name,values in families.items():
        if not values:continue
        token=_manager_token(context.run_id,context.sequence,root['content_hash'],name)
        _insert(client,name,tuple(dict(row) for row in values),token,
            dispatch_sequence=context.sequence,dispatch_batch_id=context.batch_id,
            dispatch_manager_snapshot_hash=root['content_hash'],
            dispatch_structural_rejection_manager_context=context)
        operations.append((name,token))
    readback=readback_manager_publication(context,client=client)
    financial=verify_financial_capture(financial_capture,context,client=client,session=session)
    for name,token in operations:
        client.typed_insert_dispatch.seal_verified_operation(run_id=context.run_id,table=name,
            token=token,batch_id=context.batch_id,batch_last_sequence=context.sequence,
            manager_snapshot=True)
    client.typed_insert_dispatch.compact_verified_structural_rejection_manager_snapshot(
        client=client,session=session,context=context,readback=readback,financial=financial,
        operations=tuple(operations),previous=previous)
    head=reader.read_head(run_id=context.run_id)
    if (head.checkpoint_sequence,head.journal_batch_id,head.snapshot_hash)!=(
            context.sequence,context.batch_id,root['content_hash']):
        raise ValueError('Structural rejection published head differs from exact verified image')
    return head
