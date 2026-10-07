"""Fresh, source-bound selected resume graph; no live execution authority.

Persisted entry companions are rederived before the ordinary V4 chain verifier
consumes them. The selected head and financial families must share that chain.
"""
from dataclasses import dataclass
from datetime import date
from types import MappingProxyType
from weakref import WeakKeyDictionary

_ISSUED = WeakKeyDictionary()


def load_persisted_fixed_lot_contexts(client, *, source, max_entries=4096):
    from src.backend.backtest_fixed_structural_lot_source import require_native_fixed_structural_lot_source
    from src.trading_runtime.fixed_structural_lot_entry_v4 import (
        FixedStructuralLotEntryRows, FixedStructuralLotPublicationContext,
        fixed_structural_lot_semantic_batch, _decode_proposal, COMMON)
    from src.trading_runtime.fixed_structural_lot_entry_schema import ENTRY, LOT, NODE
    from src.trading_runtime.strategy_one_configuration_tree import decode_nodes
    from src.trading_runtime.arte_journal_writer import _rows, _literal, _CONTRACTS, _canonical_typed_content
    from src.trading_runtime.arte_journal_reader import _journal_instant
    from src.trading_runtime.journal_contract import JournalRecord
    from src.backend.backtest_market_data import assert_select_only
    require_native_fixed_structural_lot_source(source)
    source.require_installed_admission()
    if type(max_entries) is not int or not 1 <= max_entries <= 4096:
        raise ValueError('Selected resume entry cardinality is unbounded')
    def read(table, predicate, count, exact=True):
        if type(count) is not int or not 0<=count<=30000:
            raise ValueError('Selected resume child cardinality is unbounded')
        columns=','.join(n for n,_ in table.columns)
        rows=_rows(client,assert_select_only(f'SELECT {columns} FROM arte.{table.name} '
            f'WHERE run_id={_literal(source.run_id)} AND {predicate} LIMIT {count+1} FORMAT JSONEachRow'))
        if len(rows)>count or (exact and len(rows)!=count):
            raise ValueError('Selected resume companion cardinality differs: '+table.name)
        return tuple(MappingProxyType({**_canonical_typed_content(table.name,
            {k:v for k,v in r.items() if k!='content_hash'},stored_utc=True),
            'content_hash':r['content_hash']}) for r in rows)
    roots=read(ENTRY,'1',max_entries,False)
    contexts=[];seen=set()
    for root in sorted(roots,key=lambda r:r['sequence']):
        if root['parent_record_id'] in seen:
            raise ValueError('Selected resume duplicates an original entry')
        seen.add(root['parent_record_id'])
        predicate='root_record_id=toUUID('+_literal(root['root_record_id'])+')'
        nodes=read(NODE,predicate,sum(root[k] for k in ('parent_node_count','selected_node_count','proposal_node_count')))
        nodes=tuple(sorted(nodes,key=lambda r:(('parent_configuration','selected_configuration','proposal').index(r['tree_kind']),r['node_id'])))
        lots=tuple(sorted(read(LOT,predicate,root['lot_count']),key=lambda r:r['ordinal']))
        packet=FixedStructuralLotEntryRows(root,lots,nodes)
        tree=tuple({k:v for k,v in r.items() if k not in {n for n,_ in COMMON}|{'tree_kind','content_hash'}}
                   for r in nodes if r['tree_kind']=='proposal')
        request=source.request(_decode_proposal(decode_nodes(tree)))
        event=read(_CONTRACTS['trading_event_v1'],'record_id IN (toUUID('+_literal(root['parent_record_id'])+'))',1)[0]
        commit=read(_CONTRACTS['trading_commit_v4'],'batch_id=toUUID('+_literal(root['batch_id'])+')',1)[0]
        if (commit['status']!='running' or commit['first_sequence']!=root['sequence']
                or commit['last_sequence']!=root['sequence'] or event['batch_id']!=root['batch_id']):
            raise ValueError('Selected resume entry differs from committed envelope')
        record=JournalRecord(event['record_id'],source.run_id,event['sequence'],
            _journal_instant(event['event_time']),_journal_instant(event['recorded_at']),
            event['category'],event['entity_type'],event['entity_id'],event['account_id'],
            {**request.intent.payload(),'strategy_id':request.strategy_id,'strategy_revision':request.revision})
        unit=fixed_structural_lot_semantic_batch(record,request,run_month=date.fromisoformat(commit['run_month']),
            attempt_id=commit['attempt_id'],batch_id=commit['batch_id'],prior_batch_id=commit['prior_batch_id'],
            source_cursor=commit['source_cursor'])
        context=FixedStructuralLotPublicationContext(unit,record,source)
        context.verify_admission()
        if unit.packet!=packet:
            raise ValueError('Selected resume persisted companion differs from fresh source')
        contexts.append(context)
    return tuple(contexts)


@dataclass(frozen=True,slots=True,eq=False,weakref_slot=True)
class FixedStructuralLotResumeBinding:
    prepared: object
    contexts: tuple
    image: object

    @property
    def source(self):return self.prepared.operation.source

    def require(self, *, run_id):
        binding=_ISSUED.get(self)
        if binding is None or binding!=(self.prepared,self.contexts,self.image) or self.source.run_id!=run_id:
            raise ValueError('Selected resume binding is unissued, copied or foreign')
        self.prepared._require_issued()
        from src.trading_runtime.fixed_structural_lot_manager_snapshot import require_cold_manager_image
        require_cold_manager_image(self.image,source=self.source)
        return self

    def prefix(self,client,run_id):
        self.require(run_id=run_id)
        from src.trading_runtime.arte_journal_commit_v4 import load_verified_v4_prefix
        prefix=load_verified_v4_prefix(client,run_id,first_price_source=self.source.price_authority,
            fixed_lot_contexts=self.contexts,_fixed_lot_cold_source=self.source,
            _cold_recovery_context_sink=[])
        if prefix is None or (prefix.last_sequence,prefix.last_batch_id)!=(self.image.sequence,self.image.batch_id):
            raise ValueError('Selected resume committed frontier moved')
        return prefix


def prepare_fixed_structural_lot_resume(client,keeper_session,*,prepared):
    from .backtest_fixed_structural_lot_execution import PreparedFixedStructuralLotSession
    from src.trading_runtime.fixed_structural_lot_manager_snapshot import load_cold_manager_image
    if type(prepared) is not PreparedFixedStructuralLotSession:
        raise ValueError('Selected resume lacks fresh installed session')
    prepared._require_issued()
    contexts=load_persisted_fixed_lot_contexts(client,source=prepared.operation.source)
    image=load_cold_manager_image(client,keeper_session,source=prepared.operation.source,fixed_lot_contexts=contexts)
    result=FixedStructuralLotResumeBinding(prepared,contexts,image)
    _ISSUED[result]=(prepared,contexts,image)
    result.prefix(client,result.source.run_id)
    return result


def require_fixed_structural_lot_resume(binding,run_id):
    if type(binding) is not FixedStructuralLotResumeBinding:
        raise ValueError('Selected resume requires exact issued binding')
    return binding.require(run_id=run_id)
