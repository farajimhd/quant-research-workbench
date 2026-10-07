from dataclasses import replace
from datetime import date
from types import MappingProxyType
from uuid import uuid4, UUID

import pytest

from test_fixed_structural_lot_source import prepared
from src.backend.backtest_journal_memory import BacktestMemoryJournal
from src.trading_runtime.fixed_structural_lot_entry_v4 import (
    fixed_structural_lot_semantic_batch, restore_fixed_structural_lot_entry,
    FixedStructuralLotEntryRows, V4FixedStructuralLotEntryBatch, _hash,
)
from src.trading_runtime.arte_journal_writer import _sealed_families
from src.trading_runtime.fixed_structural_lot_entry_schema import TABLES


def packet(monkeypatch):
    source,proposal,calls=prepared(monkeypatch)
    request=source.request(proposal)
    journal=BacktestMemoryJournal(run_id=source.run_id)
    record=journal.append_fixed_structural_lot_entry(request=request)
    unit=fixed_structural_lot_semantic_batch(record,request,run_month=source.session_date.replace(day=1),
        attempt_id=str(uuid4()),batch_id=str(uuid4()),prior_batch_id=str(UUID(int=0)),source_cursor='initial')
    return source,request,journal,record,unit


def test_complete_original_own_record_and_normalized_roundtrip(monkeypatch):
    source,request,journal,record,unit=packet(monkeypatch)
    try:
        rows=unit.packet
        result=restore_fixed_structural_lot_entry(rows,record=record,batch=unit.base,source=source)
        assert result.entry==request.entry and result.original==request.original and result.intent==request.intent
        assert record.entity_type==unit.base.events[0]['entity_type']=='fixed_structural_lot_entry_intent'
        assert record.sequence==unit.base.first_sequence==unit.base.last_sequence==1
        assert record.record_id==rows.root['parent_record_id']
        assert [r['fixed_target'] for r in rows.lots]==[12.,13.,14.]
        assert {r['tree_kind'] for r in rows.nodes}=={'parent_configuration','selected_configuration','proposal'}
        with pytest.raises(ValueError,match='no typed contract'):
            _sealed_families(unit.base)
        assert _sealed_families(unit.base,fixed_lot_unit=unit)
        with pytest.raises(ValueError,match='complete exact'):
            _sealed_families(unit.base,fixed_lot_unit=replace(unit,base=replace(unit.base)))
        assert journal.append_fixed_structural_lot_entry(request=request) is record
        assert journal.latest_sequence(journal.run_id)==1
        journal.mark_fenced(1)
        assert journal.fixed_structural_lot_entry_for_record(record.record_id) is None
        assert journal.append_fixed_structural_lot_entry(request=request) is record
    finally:
        journal.close()


def reseal(row,**changes):
    data={**row,**changes}
    data.pop('content_hash')
    return MappingProxyType({**data,'content_hash':_hash(data)})


@pytest.mark.parametrize('change',['lot_order','lot_missing','lot_foreign','target','confirmed_future',
    'node_missing','node_duplicate','node_order','unknown_kind','unknown_class','node_alias','root_alias','config','quote'])
def test_resealed_mutations_reject_structurally_or_fresh_replay(monkeypatch,change):
    source,request,journal,record,unit=packet(monkeypatch)
    rows=unit.packet
    try:
        root,lots,nodes=rows.root,list(rows.lots),list(rows.nodes)
        if change=='lot_order': lots.reverse()
        elif change=='lot_missing': lots.pop()
        elif change=='lot_foreign': lots[1]=reseal(lots[1],parent_record_id=str(uuid4()))
        elif change=='target': lots[1]=reseal(lots[1],fixed_target=13.01)
        elif change=='confirmed_future': lots[1]=reseal(lots[1],confirmed_at_ms=lots[1]['confirmed_at_ms']+100000)
        elif change=='node_missing': nodes.pop()
        elif change=='node_duplicate': nodes.append(nodes[-1])
        elif change=='node_order': nodes.reverse()
        elif change=='unknown_kind': nodes[-1]=reseal(nodes[-1],tree_kind='approval')
        elif change=='unknown_class':
            index=next(i for i,r in enumerate(nodes) if r['tree_kind']=='proposal' and r['child_key']=='type')
            nodes[index]=reseal(nodes[index],text_value='ArbitraryClass')
        elif change=='node_alias':
            index=next(i for i,r in enumerate(nodes) if r['float_value'] is not None)
            nodes[index]=reseal(nodes[index],float_value=int(nodes[index]['float_value']))
        elif change=='root_alias': root=reseal(root,revision=42.)
        elif change=='config': root=reseal(root,selected_configuration_hash='f'*64)
        elif change=='quote': root=reseal(root,ask_int=root['ask_int']+1)
        root=reseal(root,lot_hash=_hash([dict(r) for r in lots]),configuration_nodes_hash=_hash([dict(r) for r in nodes]))
        with pytest.raises(ValueError):
            changed=FixedStructuralLotEntryRows(root,tuple(lots),tuple(nodes))
            restore_fixed_structural_lot_entry(changed,record=record,batch=unit.base,source=source)
    finally:
        journal.close()


@pytest.mark.parametrize('change',['payload','entity','sequence','correlation','foreign_batch','slices'])
def test_original_semantic_lineage_has_no_mutable_or_foreign_fallback(monkeypatch,change):
    source,request,journal,record,unit=packet(monkeypatch)
    try:
        if change=='payload': record=replace(record,payload={**record.payload,'quantity':3.})
        elif change=='entity': record=replace(record,entity_type='strategy_intent')
        elif change=='sequence': record=replace(record,sequence=2)
        elif change=='correlation':
            unit=replace(unit,base=replace(unit.base,events=({**unit.base.events[0],'correlation_id':str(uuid4())},)))
        elif change=='foreign_batch':
            with pytest.raises(ValueError,match='detached'):
                replace(unit,base=replace(unit.base,batch_id=str(uuid4())))
            return
        else:
            unit=replace(unit,base=replace(unit.base,intent_slices=unit.base.intent_slices[:-1]))
        with pytest.raises(ValueError):
            restore_fixed_structural_lot_entry(unit.packet,record=record,batch=unit.base,source=source)
    finally:
        journal.close()


def test_schemas_are_normalized_and_do_not_install_anything():
    assert len(TABLES)==3
    for table in TABLES:
        assert "storage_policy = 'live_market_ssd'" in table.ddl()
        assert all('Array' not in kind and 'JSON' not in kind for _,kind in table.columns)


def test_fenced_selected_entry_readback_is_source_bound_and_installed_gate_closed(monkeypatch):
    from src.trading_runtime.fixed_structural_lot_entry_v4 import (
        FixedStructuralLotPublicationContext, publish_fixed_structural_lot_entry_v4)
    from src.backend.backtest_fixed_structural_lot_source import PreparedFixedStructuralLotSource
    from src.trading_runtime.arte_journal_commit_v4 import load_verified_commit_v4
    from test_arte_journal_commit_v4 import attached_v4_client
    source,request,journal,record,unit=packet(monkeypatch)
    context=FixedStructuralLotPublicationContext(unit,record,source)
    client=attached_v4_client()
    try:
        with pytest.raises(ValueError,match='installed'):
            publish_fixed_structural_lot_entry_v4(client,context)
        assert not client.inserts and not client.typed_insert_dispatch.reserved
        # Explicit test-only missing installed-release/financial predecessor seam.
        # All real source replay and generic fenced transport validators remain.
        monkeypatch.setattr(PreparedFixedStructuralLotSource,'require_installed_admission',lambda self: None)
        # Uninstalled controlled transport fixture; no real selected profile exists.
        from src.trading_runtime import fixed_structural_lot_profile as profile_module
        monkeypatch.setattr(profile_module,'require_fixed_structural_lot_client_context',lambda *args:None)
        assert publish_fixed_structural_lot_entry_v4(client,context)==unit.base.batch_id
        assert client.inserts[-1]=='trading_commit_v4'
        load_verified_commit_v4(client,run_id=source.run_id,batch_id=unit.base.batch_id,fixed_lot_context=context)
        count=len(client.inserts)
        assert publish_fixed_structural_lot_entry_v4(client,context)==unit.base.batch_id
        assert len(client.inserts)==count
        with pytest.raises(ValueError,match='fresh complete'):
            load_verified_commit_v4(client,run_id=source.run_id,batch_id=unit.base.batch_id)
    finally:
        journal.close()


@pytest.mark.parametrize('change',['account','revision','revision_alias','clock','intent','event'])
def test_actual_runtime_selected_rejects_before_append_or_admission(monkeypatch,change):
    from types import SimpleNamespace
    from datetime import timedelta
    import asyncio
    from src.trading_runtime.runtime import TradingRuntime
    from src.trading_runtime.signals import StrategyEvaluation
    from src.trading_runtime.runtime import RunMode
    source,proposal,_=prepared(monkeypatch)
    request=source.request(proposal)
    journal=BacktestMemoryJournal(run_id=source.run_id)
    runtime=object.__new__(TradingRuntime)
    runtime.config=SimpleNamespace(mode=RunMode.BACKTEST,strategy_id=request.strategy_id,
        strategy_revision=request.revision,anchor_date=source.session_date,account_ids=(proposal.account_id,))
    runtime.run_id=source.run_id;runtime.journal=journal;runtime.last_event_time=request.intent.event_time
    account=proposal.account_id;intent=request.intent;event=None
    if change=='account':account='foreign-account'
    elif change=='revision':runtime.config.strategy_revision+=1
    elif change=='revision_alias':runtime.config.strategy_revision=float(request.revision)
    elif change=='clock':runtime.last_event_time+=timedelta(milliseconds=1)
    elif change=='intent':intent=replace(intent,reference_price=intent.reference_price+.01)
    elif change=='event':event=object()
    try:
        with pytest.raises(ValueError,match='exclusive exact'):
            asyncio.run(runtime._execute_intents(StrategyEvaluation(intents=(intent,)),account,event,
                fixed_structural_lot_request=request))
        assert journal.latest_sequence(source.run_id)==0
    finally:journal.close()


@pytest.mark.parametrize('change',['missing_context','foreign_run','foreign_batch','omitted_companion','mutated_companion','duplicate_context','rebadged_event'])
def test_complete_cold_graph_cannot_be_bypassed_by_context_or_entity(monkeypatch,change):
    from src.trading_runtime.fixed_structural_lot_entry_v4 import (
        FixedStructuralLotPublicationContext,sealed_fixed_structural_lot_families,
        verify_fixed_structural_lot_cold_graph,fixed_lot_contexts_by_batch)
    from src.backend.backtest_fixed_structural_lot_source import PreparedFixedStructuralLotSource
    source,request,journal,record,unit=packet(monkeypatch)
    try:
        monkeypatch.setattr(PreparedFixedStructuralLotSource,'require_installed_admission',lambda self:None)
        # Uninstalled controlled transport fixture; no real selected profile exists.
        from src.trading_runtime import fixed_structural_lot_profile as profile_module
        monkeypatch.setattr(profile_module,'require_fixed_structural_lot_client_context',lambda *args:None)
        context=FixedStructuralLotPublicationContext(unit,record,source)
        _,families=sealed_fixed_structural_lot_families(unit)
        from src.trading_runtime.arte_journal_writer import _canonical_typed_content
        rows={name:tuple({**_canonical_typed_content(name,{k:v for k,v in r.items() if k!='content_hash'}),
            'content_hash':r['content_hash']} for r in values) for name,values in families}
        commit=dict(run_id=unit.base.run_id,batch_id=unit.base.batch_id,
                    first_sequence=unit.base.first_sequence,last_sequence=unit.base.last_sequence)
        verify_fixed_structural_lot_cold_graph(rows,context,commit)
        if change=='duplicate_context':
            with pytest.raises(ValueError,match='duplicate'):
                fixed_lot_contexts_by_batch(source.run_id,(context,context),max_commits=10)
            return
        if change=='missing_context':context=None
        elif change=='foreign_run':commit['run_id']=str(uuid4())
        elif change=='foreign_batch':commit['batch_id']=str(uuid4())
        elif change=='omitted_companion':rows[TABLES[1].name]=()
        elif change=='mutated_companion':
            altered=dict(rows[TABLES[1].name][0]);altered['target_price']=99.
            altered['content_hash']=_hash({k:v for k,v in altered.items() if k!='content_hash'})
            rows[TABLES[1].name]=(altered,*rows[TABLES[1].name][1:])
        elif change=='rebadged_event':
            event=dict(rows['trading_event_v1'][0]);event['entity_type']='strategy_intent'
            rows['trading_event_v1']=(event,)
        with pytest.raises(ValueError):verify_fixed_structural_lot_cold_graph(rows,context,commit)
    finally:journal.close()
