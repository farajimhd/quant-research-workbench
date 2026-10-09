"""Real typed INSERT/Keeper transport with explicit installation/prefix seams.

These controlled fixtures are not an installed own release or real database.
"""
from copy import copy
from dataclasses import replace
from types import SimpleNamespace
import json
import asyncio
import pytest

from test_profit_armed_structural_rejection_management import fixture,financial,quote,RUN,DAY
from src.trading_runtime.strategy_one_stateful import StrategyOneEntryProposal
from test_arte_typed_insert_dispatch import Keeper,Client,attest_direct
from src.trading_runtime import profit_armed_structural_rejection_profile as profile
from src.trading_runtime import profit_armed_structural_rejection_publication as publication
from src.trading_runtime import arte_journal_writer as writer
from src.trading_runtime.arte_typed_insert_dispatch import TypedInsertDispatch,_manager_token
from src.trading_runtime.profit_armed_structural_rejection_snapshot import PARENT,STATE
from src.trading_runtime.keeper_ownership import KeeperUnavailable

BATCH='00000000-0000-0000-0000-000000000012'
ZERO='00000000-0000-0000-0000-000000000000'


def prepared(monkeypatch,*,lose_response=False):
    manager,owner,_=fixture(monkeypatch)
    import src.backend.backtest_strategy_certified_price_break as price
    import src.backend.backtest_strategy_episode_activity_source as activity
    monkeypatch.setattr(price,'certified_price_entry_intent',lambda *args,**kwargs:None)
    monkeypatch.setattr(activity,'certified_episode_activity_witness',lambda *args,**kwargs:None)
    async def drive():
        # Genuine normalized integer episode clock, rather than the older
        # pure projection fixture's research-only 0.0 value.
        await manager.on_entry_proposal(StrategyOneEntryProposal('A1','DU1','AAA',100,100,
            10.,9.,12.,'ENTRY-R',.5,100,'S1',strategy_number=57))
        await manager.on_management(financial(),{},1000)
        for clock in (10000,15000,20000,25000):
            await manager.on_management(financial(),quote(owner,clock),clock)
    asyncio.run(drive())
    import src.trading_runtime.strategy_registry as registry
    import src.backend.backtest_fixed_v4_certification as certification
    contract=manager.contract
    release=SimpleNamespace(**vars(contract.release),approved_digest='a'*64,
        executor_strategy_id=1,executor_revision=57,verify=lambda:None)
    contract.release=release
    executor=SimpleNamespace(verify=lambda:None,contract_factory=lambda:contract)
    monkeypatch.setattr(registry,'numbered_strategy',lambda _:release)
    monkeypatch.setattr(registry,'fixed_strategy_executor',lambda *args:executor)
    proof_calls=[]
    monkeypatch.setattr(certification,'certify_numbered_fixed_v4_projection',
                        lambda n:proof_calls.append(n) or 'b'*64)
    issued=profile.issue_native_structural_rejection_profile(owner)
    dispatch=TypedInsertDispatch(Keeper());dispatch.initialize_new_run(RUN);attest_direct(dispatch,RUN)
    dispatch.assert_next_batch(run_id=RUN,batch_id=BATCH,prior_batch_id=ZERO,first_sequence=1,last_sequence=9)
    client=Client(dispatch,lose_response=lose_response);client.structural_rejection_profile=issued
    sql=("INSERT INTO arte.trading_event_v1 (run_id) SETTINGS "
         "async_insert=1,wait_for_async_insert=1,insert_deduplicate=1,"
         "insert_deduplication_token='initial' FORMAT JSONEachRow\n{}")
    # A genuine complete-prefix certifier seam, not a fabricated production
    # authority: the existing transport gate is exercised without a database.
    client.lose_response=False
    dispatch.execute_typed_insert(client,run_id=RUN,table='trading_event_v1',token='initial',sql=sql,
        batch_id=BATCH,batch_last_sequence=9)
    dispatch.seal_verified_operation(run_id=RUN,table='trading_event_v1',token='initial',
        batch_id=BATCH,batch_last_sequence=9)
    dispatch.compact_verified_batch(run_id=RUN,batch_id=BATCH,prior_batch_id=ZERO,
        first_sequence=1,last_sequence=9,commit_hash='c'*64,operations=(('trading_event_v1','initial'),))
    client.calls.clear();client.lose_response=lose_response
    import src.trading_runtime.arte_journal_commit_v4 as commits
    import src.trading_runtime.arte_journal_projection as projection
    prefix=SimpleNamespace(status='running',last_sequence=9,last_batch_id=BATCH)
    cursor=dict(run_id=RUN,event_sequence=9,batch_id=BATCH,boundary_ms=25000,session_date=DAY.isoformat())
    monkeypatch.setattr(commits,'load_writer_v4_snapshot_prefix',lambda *args,**kwargs:prefix)
    monkeypatch.setattr(projection,'load_latest_backtest_cursor',lambda *args:cursor)
    state=manager.capture_state(boundary_ms=25000)
    context=publication.issue_manager_publication(client,state,sequence=9,batch_id=BATCH)
    return client,context,proof_calls,executor,prefix,cursor


def insert(client,context,table=STATE.name,**overrides):
    families=publication.require_manager_publication(context,client=client)
    rows=tuple(families[table]);root=families[PARENT.name][0]
    args=dict(dispatch_sequence=9,dispatch_batch_id=BATCH,
        dispatch_manager_snapshot_hash=root['content_hash'],dispatch_structural_rejection_manager_context=context)
    args.update(overrides)
    return writer._insert(client,table,rows,_manager_token(RUN,9,root['content_hash'],table),**args)


def test_actual_selected_insert_acknowledged_and_profile_proof_once(monkeypatch):
    client,context,calls,*_=prepared(monkeypatch)
    sql=insert(client,context)
    assert len(client.calls)==1 and client.calls[0][0]==sql
    assert calls==[57]
    for _ in range(5): publication.require_manager_publication(context,client=client)
    assert calls==[57]
    # INSERT acknowledgement alone has not published a manager head.
    assert publication.selected_manager_head_path(RUN) not in client.typed_insert_dispatch.keeper.rows


@pytest.mark.parametrize('kind',('copy','constructor','foreign-client','profile-copy','wire','cursor'),ids=str)
def test_publication_rejects_substitution_before_insert(monkeypatch,kind):
    client,context,*_=prepared(monkeypatch)
    if kind=='copy': context=copy(context)
    elif kind=='constructor': context=replace(context)
    elif kind=='foreign-client': client=Client(client.typed_insert_dispatch)
    elif kind=='profile-copy': client.structural_rejection_profile=copy(context.profile)
    elif kind=='wire': object.__setattr__(context,'rows_json',context.rows_json+' ')
    else: object.__setattr__(context,'sequence',10)
    with pytest.raises(ValueError): insert(client,context)
    assert client.calls==[]


def test_mutated_returned_image_does_not_change_issued_image(monkeypatch):
    client,context,*_=prepared(monkeypatch)
    image=publication.require_manager_publication(context,client=client)
    image[STATE.name][0]['original_ask_int']+=1
    root=publication.require_manager_publication(context)[PARENT.name][0]
    with pytest.raises(ValueError,match='exact issued inventory'):
        writer._insert(client,STATE.name,tuple(image[STATE.name]),'bad',dispatch_sequence=9,
            dispatch_batch_id=BATCH,dispatch_manager_snapshot_hash=root['content_hash'],
            dispatch_structural_rejection_manager_context=context)
    assert client.calls==[]


def test_ordinary_client_cannot_write_selected_family(monkeypatch):
    client,context,*_=prepared(monkeypatch)
    rows=publication.require_manager_publication(context)[STATE.name]
    with pytest.raises(ValueError,match='issued manager context'):
        writer._insert(client,STATE.name,tuple(rows),'bad')
    assert client.calls==[]


def test_direct_dispatch_rejects_changed_sql_and_conflicting_context(monkeypatch):
    client,context,*_=prepared(monkeypatch);sql=insert(client,context)
    root=publication.require_manager_publication(context)[PARENT.name][0]
    before=len(client.calls)
    args=dict(run_id=RUN,table=STATE.name,token=_manager_token(RUN,9,root['content_hash'],STATE.name),
        batch_id=BATCH,batch_last_sequence=9,manager_snapshot_hash=root['content_hash'],
        structural_rejection_manager_context=context)
    with pytest.raises(ValueError,match='complete issued rows'):
        client.typed_insert_dispatch.execute_typed_insert(client,sql=sql+' ',**args)
    with pytest.raises(ValueError,match='conflicting'):
        client.typed_insert_dispatch.execute_typed_insert(client,sql=sql,fixed_lot_manager_context=object(),**args)
    assert len(client.calls)==before


def test_lost_insert_ack_remains_pending_without_head(monkeypatch):
    client,context,*_=prepared(monkeypatch,lose_response=True)
    with pytest.raises(TimeoutError): insert(client,context)
    assert len(client.calls)==1
    assert publication.selected_manager_head_path(RUN) not in client.typed_insert_dispatch.keeper.rows
    gate,_=client.typed_insert_dispatch._read_gate(RUN)
    assert gate.registered==1 and gate.inflight==1


@pytest.mark.parametrize('field',('last_sequence','last_batch_id','status','boundary_ms','session_date'),ids=str)
def test_real_cursor_join_rejects_foreign_prefix(monkeypatch,field):
    client,context,_,_,prefix,cursor=prepared(monkeypatch)
    if hasattr(prefix,field): setattr(prefix,field,10 if field=='last_sequence' else 'foreign')
    else: cursor[field]=0 if field=='boundary_ms' else '2026-08-19'
    with pytest.raises(ValueError,match='cursor'):
        publication.issue_manager_publication(client,context.profile.owner.manager.capture_state(boundary_ms=25000),
                                             sequence=9,batch_id=BATCH)


def test_registered_factory_policy_mismatch_cannot_issue(monkeypatch):
    client,context,_,executor,*_=prepared(monkeypatch)
    executor.contract_factory=lambda:object()
    with pytest.raises(ValueError,match='registered own declaration'):
        profile.issue_native_structural_rejection_profile(context.profile.owner)


def test_profile_constructor_and_copy_cannot_issue_tables(monkeypatch):
    _,context,*_=prepared(monkeypatch)
    for forged in (copy(context.profile),replace(context.profile)):
        with pytest.raises(ValueError,match='Unissued'): profile.selected_structural_rejection_tables(forged)


def stored_transport(client,context,*,change=None):
    image=publication.require_manager_publication(context,client=client)
    queries=[]
    def execute(sql,**kwargs):
        queries.append(sql)
        table=sql.split(' FROM arte.',1)[1].split(' ',1)[0]
        rows=image[table]
        if change is not None: rows=change(table,rows)
        return '\n'.join(json.dumps(dict(row)) for row in rows)
    client.execute=execute
    return image,queries


def test_complete_bounded_readback_has_distinct_copied_receipt_guard(monkeypatch):
    client,context,*_=prepared(monkeypatch)
    image,queries=stored_transport(client,context)
    receipt=publication.readback_manager_publication(context,client=client)
    assert publication.require_manager_readback(receipt,client=client) is context
    assert len(queries)==len(image)
    for name,rows in image.items():
        query=next(q for q in queries if f' FROM arte.{name} ' in q)
        assert f'LIMIT {len(rows)+1} FORMAT JSONEachRow' in query
    assert publication.selected_manager_head_path(RUN) not in client.typed_insert_dispatch.keeper.rows
    with pytest.raises(ValueError,match='Unissued'): publication.require_manager_readback(copy(receipt),client=client)
    with pytest.raises(ValueError): publication.require_manager_readback(receipt,client=Client())


@pytest.mark.parametrize('kind',('missing','duplicate','foreign-root','tampered-child','extra-empty'),ids=str)
def test_readback_rejects_complete_graph_drift(monkeypatch,kind):
    client,context,*_=prepared(monkeypatch)
    def change(table,rows):
        if table==STATE.name:
            if kind=='missing': return rows[:-1]
            if kind=='duplicate': return rows+[rows[0]]
            if kind in ('foreign-root','tampered-child'):
                rows=[dict(row) for row in rows]
                field='run_id' if kind=='foreign-root' else 'original_ask_int'
                rows[0][field]='foreign' if kind=='foreign-root' else rows[0][field]+1
        if kind=='extra-empty' and not rows:
            # Even an otherwise empty family must not hide an orphan row.
            return [{'unexpected':1}]
        return rows
    stored_transport(client,context,change=change)
    with pytest.raises(ValueError,match='readback differs'):
        publication.readback_manager_publication(context,client=client)
    assert publication.selected_manager_head_path(RUN) not in client.typed_insert_dispatch.keeper.rows


def test_readback_rechecks_prefix_after_all_rows(monkeypatch):
    client,context,_,_,prefix,_=prepared(monkeypatch)
    image,queries=stored_transport(client,context)
    original=client.execute
    def execute(sql,**kwargs):
        value=original(sql,**kwargs)
        if len(queries)==len(image): prefix.last_sequence=10
        return value
    client.execute=execute
    with pytest.raises(ValueError,match='actual running V4 cursor'):
        publication.readback_manager_publication(context,client=client)


def test_real_clickhouse_json_uint64_decimal_string_readback(monkeypatch):
    client,context,*_=prepared(monkeypatch)
    def wire(table,rows):
        kinds=dict(writer._CONTRACTS[table].columns)
        return [{name:str(value) if value is not None and (
            kind in ('UInt64','Nullable(UInt64)') or 'Decimal(' in kind) else value
            for name,value in row.items() for kind in (kinds[name],)} for row in rows]
    _,queries=stored_transport(client,context,change=wire)
    receipt=publication.readback_manager_publication(context,client=client)
    assert publication.require_manager_readback(receipt,client=client) is context
    assert any('toString(' in query for query in queries)


@pytest.mark.parametrize('kind',('extra-column','rehashed-change','oversized'),ids=str)
def test_readback_rejects_schema_hash_and_byte_drift(monkeypatch,kind):
    client,context,*_=prepared(monkeypatch)
    def change(table,rows):
        if table!=STATE.name: return rows
        rows=[dict(row) for row in rows]
        if kind=='extra-column': rows[0]['unexpected']=1
        elif kind=='rehashed-change':
            from src.trading_runtime.profit_armed_structural_rejection_snapshot import _digest
            rows[0]['original_ask_int']+=1
            rows[0]['content_hash']=_digest({k:v for k,v in rows[0].items() if k!='content_hash'})
        else: rows[0]['policy_id']='x'*100000
        return rows
    stored_transport(client,context,change=change)
    with pytest.raises(ValueError,match='extra typed|readback differs|byte bound'):
        publication.readback_manager_publication(context,client=client)


def sealed_publication_operations(client,context):
    families=publication.require_manager_publication(context,client=client)
    root=families[PARENT.name][0]
    operations=[]
    for name,rows in families.items():
        if not rows: continue
        insert(client,context,name)
        token=_manager_token(RUN,9,root['content_hash'],name)
        client.typed_insert_dispatch.seal_verified_operation(run_id=RUN,table=name,
            token=token,batch_id=BATCH,batch_last_sequence=9,manager_snapshot=True)
        operations.append((name,token))
    assert client.typed_insert_dispatch._read_gate(RUN)[0].inflight==0
    return tuple(operations)


def test_selected_manager_head_cas_compacts_complete_sealed_inventory(monkeypatch):
    from src.trading_runtime import profit_armed_structural_rejection_financial_checkpoint as finance
    client,context,*_=prepared(monkeypatch)
    operations=sealed_publication_operations(client,context)
    stored_transport(client,context)
    readback=publication.readback_manager_publication(context,client=client)
    financial_receipt=object();session=object();checks=[]
    # Explicit financial reader seam: this test qualifies actual dispatch
    # and Keeper CAS, not financial actor capture or a real DB installation.
    def require(receipt,**kwargs):
        assert receipt is financial_receipt
        assert kwargs==dict(publication=context,client=client,session=session)
        checks.append('financial')
    monkeypatch.setattr(finance,'require_financial_verification',require)
    client.typed_insert_dispatch.compact_verified_structural_rejection_manager_snapshot(
        client=client,session=session,context=context,readback=readback,
        financial=financial_receipt,operations=operations,previous=None)
    wire,_=client.typed_insert_dispatch.keeper.get(publication.selected_manager_head_path(RUN))
    root=publication.require_manager_publication(context)[PARENT.name][0]
    assert wire==f"1\n{RUN}\n9\n{BATCH}\n{root['content_hash']}".encode()
    assert client.typed_insert_dispatch._read_gate(RUN)[0].registered==0
    assert checks==['financial']


def test_financial_failure_keeps_selected_head_unpublished(monkeypatch):
    from src.trading_runtime import profit_armed_structural_rejection_financial_checkpoint as finance
    client,context,*_=prepared(monkeypatch)
    operations=sealed_publication_operations(client,context)
    stored_transport(client,context)
    readback=publication.readback_manager_publication(context,client=client)
    def fail(*args,**kwargs): raise ValueError('Portfolio inventory changed')
    monkeypatch.setattr(finance,'require_financial_verification',fail)
    with pytest.raises(ValueError,match='Portfolio inventory changed'):
        client.typed_insert_dispatch.compact_verified_structural_rejection_manager_snapshot(
            client=client,session=object(),context=context,readback=readback,
            financial=object(),operations=operations,previous=None)
    assert publication.selected_manager_head_path(RUN) not in client.typed_insert_dispatch.keeper.rows
    assert client.typed_insert_dispatch._read_gate(RUN)[0].registered==len(operations)


def test_actual_publication_seals_snapshot_operations_and_publishes_head(monkeypatch):
    from src.trading_runtime.keeper_session import ManagedKeeperSession
    from src.trading_runtime import profit_armed_structural_rejection_financial_checkpoint as finance
    client,context,*_=prepared(monkeypatch)
    keeper=client.typed_insert_dispatch.keeper
    keeper.connected=True;keeper.client_state='CONNECTED';keeper.client_id=(1,b'fixture')
    keeper.add_listener=lambda callback:None
    keeper.exists=lambda path:keeper.rows[path][1] if path in keeper.rows else None
    session=ManagedKeeperSession(keeper);session._on_state('CONNECTED')
    image=publication.require_manager_publication(context,client=client)
    inserted=[];selects=[]
    def execute(sql,**kwargs):
        if sql.startswith('INSERT '):
            inserted.append(sql)
            return ''
        table=sql.split(' FROM arte.',1)[1].split(' ',1)[0]
        selects.append(table)
        return '\n'.join(json.dumps(row) for row in image[table])
    client.execute=execute
    captured=object();verified=object();checks=[]
    monkeypatch.setattr(finance,'require_financial_capture',lambda value,**kw:
        None if value is captured else pytest.fail('foreign financial capture'))
    def verify(value,pub,**kw):
        assert value is captured and pub is context
        checks.append('verify')
        return verified
    def require(value,**kw):
        assert value is verified
        checks.append('fresh')
    monkeypatch.setattr(finance,'verify_financial_capture',verify)
    monkeypatch.setattr(finance,'require_financial_verification',require)
    head=publication.publish_manager_publication(client,session,context,captured)
    assert head.checkpoint_sequence==9 and head.journal_batch_id==BATCH
    assert head.snapshot_hash==image[PARENT.name][0]['content_hash']
    assert len(inserted)==sum(bool(rows) for rows in image.values())
    assert set(selects)==set(image)
    assert checks==['verify','fresh']
    gate=client.typed_insert_dispatch._read_gate(RUN)[0]
    assert gate.inflight==0 and gate.registered==0


def prewriter_profile(monkeypatch):
    from src.backend import backtest_profit_armed_structural_rejection_management as native
    client,context,calls,*_=prepared(monkeypatch)
    previous=context.profile.owner
    source=native.prepare_structural_rejection_source(object(),contract=previous.manager.contract,
        strategy_id=previous.manager.runtime.config.strategy_id,
        strategy_revision=previous.manager.runtime.config.strategy_revision,
        run_id=RUN,session_date=DAY,market=previous.market,seeds=previous.seeds,
        intervals=previous.intervals,price_authority=previous.price_authority,through_boundary_ms=40000)
    selected=profile.issue_prepared_structural_rejection_profile(source)
    return client,selected,calls,previous


def test_issued_prewriter_scope_binds_same_source_without_new_proof_or_tables(monkeypatch):
    from src.backend.backtest_profit_armed_structural_rejection_management import bind_prepared_structural_rejection_manager
    from src.backend.backtest_strategy_one_management import StrategyOneManagementRunner
    from test_profit_armed_structural_rejection_management import Evidence
    _,selected,calls,previous=prewriter_profile(monkeypatch)
    before=tuple(calls)
    tables=profile.selected_structural_rejection_tables(selected)
    with pytest.raises(ValueError,match='no bound actual manager'): selected.owner
    manager=StrategyOneManagementRunner(runtime=previous.manager.runtime,evidence=Evidence(),tick_for_ticker=lambda _: .01)
    owner=bind_prepared_structural_rejection_manager(manager,selected.source)
    manager.bind_structural_rejection_management(owner)
    assert profile.bind_prepared_structural_rejection_profile(selected,owner) is selected
    assert selected.owner is owner
    assert profile.selected_structural_rejection_tables(selected)==tables
    assert tuple(calls)==before
    with pytest.raises(ValueError,match='certified manager operation'):
        profile.bind_prepared_structural_rejection_profile(selected,previous)


def test_prewriter_profile_copy_and_bound_only_profile_cannot_select_credentials(monkeypatch):
    client,selected,*_=prewriter_profile(monkeypatch)
    with pytest.raises(ValueError,match='Unissued'):
        writer._validate_structural_rejection_profile(replace(selected))
    with pytest.raises(ValueError,match='prewriter profile'):
        writer._validate_structural_rejection_profile(client.structural_rejection_profile)
    with pytest.raises(ValueError,match='unmixed'):
        writer._validate_structural_rejection_profile(selected,entry_spread_risk=True)


def test_prewriter_preflight_audits_own_parts_and_exact_write_scope(monkeypatch):
    client,selected,*_=prewriter_profile(monkeypatch)
    client.structural_rejection_profile=selected
    client.execute=lambda sql,**kw:'backtest_v4_structural_rejection_runner'
    storage=[];permissions=[]
    # Catalog/actual-part transport seams are explicit; no DB installed here.
    monkeypatch.setattr(writer,'storage_preflight',lambda client,*,tables:storage.append(tuple(tables)))
    monkeypatch.setattr(writer,'journal_permission_preflight',lambda client,**kw:permissions.append(kw))
    monkeypatch.setattr(writer,'_rows',lambda *a,**kw:[])
    seal=writer._v4_preflight(client)
    assert seal.structural_rejection_profile is selected
    assert profile.selected_structural_rejection_tables(selected) in storage
    assert {t.name for t in profile.selected_structural_rejection_tables(selected)}<=permissions[0]['journal_tables']
    assert not {t.name for t in profile.selected_structural_rejection_tables(selected)}&permissions[0]['read_only_tables']
    with pytest.raises(ValueError,match='no bound actual manager'): selected.owner
    client.execute=lambda sql,**kw:'backtest_v4_runner'
    with pytest.raises(RuntimeError,match='dedicated principal'): writer._v4_preflight(client)


def test_prepared_profile_requires_dedicated_private_credentials_before_transport(monkeypatch):
    _,selected,*_=prewriter_profile(monkeypatch)
    monkeypatch.delenv('BACKTEST_V4_STRUCTURAL_REJECTION_RUNNER_CREDENTIAL_FILE',raising=False)
    with pytest.raises(ValueError,match='private credential FILE'):
        writer._v4_runner_credentials(structural_rejection_profile=selected)
