"""Shared cold journal graph with real causal reproof and controlled source I/O."""
from dataclasses import replace
from datetime import timezone
from types import SimpleNamespace
import json
import re

import pytest

from tests.test_backtest_native_channel_qualification import installed, unit  # noqa: F401
from tests.test_backtest_native_channel_confirmed_exit_unit import confirmed_unit_case
from tests.test_backtest_native_channel_financial_checkpoint import checkpoint_case
from src.backend.backtest_native_channel_exit_unit import prepare_confirmed_native_channel_exit_unit
from src.trading_runtime.native_channel_exit_journal_context import (
    NativeChannelExitJournalContext,channel_contexts_by_batch,
)
from src.trading_runtime.arte_journal_commit_v4 import prepare_commit_v4,load_verified_commit_v4
from src.trading_runtime.numbered_fixed_strategy import session_policy_payload
from src.trading_runtime.arte_journal_writer import _CONTRACTS,_wire_row,verify_typed_contract_row,typed_contract_row
from src.trading_runtime.arte_native_channel_exit_schema import TABLES,DECISION


def cold_case(installed,monkeypatch):
    from src.trading_runtime.numbered_fixed_strategy import numbered_fixed_strategy
    from src.trading_runtime.strategy_registry import initialize_numbered_fixed_strategies
    initialize_numbered_fixed_strategies()
    live=confirmed_unit_case(installed,monkeypatch)
    packet=prepare_confirmed_native_channel_exit_unit(**live)
    history=checkpoint_case(installed,monkeypatch,return_history=True)
    from tests.test_backtest_strategy_first_price_source import authority,Bars
    from src.backend.backtest_strategy_first_price_source import load_first_price_source
    from src.backend.backtest_strategy_certified_price_break import (
        CertifiedPriceReadbackAuthority,compile_certified_price_break_plan,
    )
    market,parent=authority()
    history['first_price_source']=CertifiedPriceReadbackAuthority(packet.base.run_id,
        compile_certified_price_break_plan(load_first_price_source(market,parent,client=Bars())))
    policy=history['policy']
    monkeypatch.setattr('src.trading_runtime.numbered_fixed_strategy.numbered_fixed_strategy',
        lambda number:SimpleNamespace(native_channel_management_policy=policy,
            policy_json=json.dumps({'session_policy':session_policy_payload()})) if number==1
            else numbered_fixed_strategy(number))
    context=NativeChannelExitJournalContext(packet,policy,history['contexts'],
        history['first_price_source'],**{key:live[key] for key in (
        'recorded_at','attempt_id','batch_id','parent_record_id','decision_record_id')})
    commit,inventory=prepare_commit_v4(run_id=packet.base.run_id,run_month=packet.base.run_month,
        attempt_id=packet.base.attempt_id,batch_id=packet.base.batch_id,
        prior_batch_id=packet.base.prior_batch_id,first_sequence=packet.base.first_sequence,
        last_sequence=packet.base.last_sequence,source_cursor=packet.base.source_cursor,status='running',
        sealed_families=packet.sealed_families(),committed_at=live['recorded_at'].astimezone(timezone.utc))
    contracts={t.name:t for t in TABLES}
    tables={name:[_wire_row(name,row) if name in _CONTRACTS else verify_typed_contract_row(contracts[name],row,stored_utc=True)
        for row in rows] for name,rows in packet.sealed_families() if rows}
    tables['trading_commit_v4']=[_wire_row('trading_commit_v4',commit)]
    tables['trading_commit_family_v4']=[_wire_row('trading_commit_family_v4',row) for row in inventory]
    sqls=[]
    def read(_client,sql):
        sqls.append(sql)
        if 'AS family_name' in sql:
            result=[]
            for name,projection in re.findall(r"SELECT '([^']+)' AS family_name, toJSONString\(tuple\((.*?)\)\) AS payload FROM arte\.",sql):
                columns=[column.removeprefix('toString(').removesuffix(')') for column in projection.split(',')]
                result.extend(dict(family_name=name,payload=json.dumps([row[column] for column in columns]))
                    for row in tables.get(name,[]))
            return result
        name=re.search(r'FROM arte\.([a-z0-9_]+)',sql).group(1)
        return [dict(row) for row in tables.get(name,[])]
    monkeypatch.setattr('src.trading_runtime.arte_journal_writer._rows',read)
    return history,context,tables,sqls


def load(scope,context):
    return load_verified_commit_v4(scope['client'],run_id=context.unit.base.run_id,
        batch_id=context.batch_id,verified_prior_prefix=scope['prefix'],
        first_price_source=scope['first_price_source'],native_channel_context=context)


@pytest.mark.parametrize('batched',[False,True])
def test_shared_commit_reader_reproves_complete_selected_channel_graph(installed,monkeypatch,batched):
    scope,context,_,sqls=cold_case(installed,monkeypatch)
    scope['client'].v4_batched_detail_readback=batched
    commit,inventory=load(scope,context)
    assert commit['last_sequence']==scope['prefix'].last_sequence+1
    assert len(inventory)==5
    assert any('toString(original_ask)' in sql for sql in sqls)
    assert all(t.name not in _CONTRACTS for t in TABLES)


@pytest.mark.parametrize('defect',['missing_context','missing_source','extra_source','source_hash',
    'root_hash','envelope','policy','naked'])
def test_shared_cold_reader_rejects_incomplete_or_foreign_channel_graph(installed,monkeypatch,defect):
    scope,context,tables,_=cold_case(installed,monkeypatch)
    if defect=='missing_context':
        with pytest.raises(ValueError):
            load_verified_commit_v4(scope['client'],run_id=context.unit.base.run_id,
                batch_id=context.batch_id,verified_prior_prefix=scope['prefix'],
                first_price_source=scope['first_price_source'])
        return
    if defect=='missing_source': tables['trading_native_channel_exit_source_v1']=[]
    elif defect=='extra_source': tables['trading_native_channel_exit_source_v1']*=2
    elif defect=='source_hash': tables['trading_native_channel_exit_source_v1'][0]['close_int']+=1
    elif defect=='root_hash':
        header={key:value for key,value in context.unit.decision.items() if key!='content_hash'}
        header['source_manager_snapshot_hash']='f'*64
        context=replace(context,unit=replace(context.unit,decision=typed_contract_row(DECISION,header)))
    elif defect=='envelope': context=replace(context,attempt_id='aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa')
    elif defect=='policy': scope['client'].native_channel_management_policy=None
    elif defect=='naked':
        base=context.unit.base
        families=tuple((name,rows) for name,rows in context.unit.sealed_families()
            if name not in {t.name for t in TABLES})
        commit,inventory=prepare_commit_v4(run_id=base.run_id,run_month=base.run_month,
            attempt_id=base.attempt_id,batch_id=base.batch_id,prior_batch_id=base.prior_batch_id,
            first_sequence=base.first_sequence,last_sequence=base.last_sequence,
            source_cursor=base.source_cursor,status=base.status,sealed_families=families,
            committed_at=context.recorded_at)
        tables['trading_commit_v4']=[_wire_row('trading_commit_v4',commit)]
        tables['trading_commit_family_v4']=[_wire_row('trading_commit_family_v4',row) for row in inventory]
        with pytest.raises(ValueError):
            load_verified_commit_v4(scope['client'],run_id=base.run_id,batch_id=base.batch_id,
                verified_prior_prefix=scope['prefix'],first_price_source=scope['first_price_source'])
        return
    with pytest.raises((ValueError,RuntimeError)): load(scope,context)


def test_context_index_is_complete_bounded_and_exclusive(installed,monkeypatch):
    _,context,_,_=cold_case(installed,monkeypatch)
    run=context.unit.base.run_id
    assert channel_contexts_by_batch(run,(context,),max_commits=1)=={context.batch_id:context}
    for contexts,limit in (([context],1),((context,),0),((context,context),2),((object(),),1)):
        with pytest.raises(ValueError): channel_contexts_by_batch(run,contexts,max_commits=limit)
    with pytest.raises(ValueError): channel_contexts_by_batch('foreign',(context,),max_commits=1)


@pytest.mark.parametrize('complete',[False,True])
def test_generic_publication_rejects_unadmitted_channel_before_any_io(installed,monkeypatch,complete):
    _,context,_,sqls=cold_case(installed,monkeypatch)
    from src.trading_runtime.arte_journal_commit_v4 import _publish_sealed_batch_v4
    from src.trading_runtime.arte_journal_writer import _sealed_families
    base=context.unit.base
    base_families=_sealed_families(base)
    families=context.unit.sealed_families() if complete else base_families
    sqls.clear()
    with pytest.raises(ValueError,match='dedicated fenced publication'):
        _publish_sealed_batch_v4(SimpleNamespace(),base,base_families,families)
    assert sqls==[]
