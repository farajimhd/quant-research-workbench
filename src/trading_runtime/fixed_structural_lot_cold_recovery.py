"""Cold-only reconstruction of committed independent-lot readback lineage.

This capability cannot execute, publish, begin recovery or attest a live ACK.
It is issued inside the full V4 verification walk after row hashes, against
that walk's exact independently verified predecessor, never a caller prefix.
"""
from dataclasses import dataclass
from weakref import WeakKeyDictionary
from .journal_contract import canonical_json


@dataclass(frozen=True,slots=True,eq=False,weakref_slot=True)
class _ColdWalk:
    client: object
    source: object
    entries: tuple


_WALKS=WeakKeyDictionary()
_CONTEXTS=WeakKeyDictionary()


@dataclass(frozen=True,slots=True,eq=False,weakref_slot=True)
class _VerifiedFrontierSeal:
    pass


_FRONTIER_SEALS=WeakKeyDictionary()


def _start_walk(client,source,entries):
    from src.backend.backtest_fixed_structural_lot_source import require_native_fixed_structural_lot_source
    require_native_fixed_structural_lot_source(source)
    source.require_installed_admission()
    if type(entries) is not tuple or len(entries)>100_000 or any(v.source is not source for v in entries):
        raise ValueError('Cold recovery walk lacks its complete fresh own source')
    for entry in entries:entry.verify_admission()
    walk=_ColdWalk(client,source,entries)
    _WALKS[walk]=None
    return walk


def _advance_walk(walk,prefix):
    if type(walk) is not _ColdWalk or walk not in _WALKS:
        raise ValueError('Unissued cold recovery verification walk')
    if _WALKS[walk] is not prefix:
        raise ValueError('Cold walk cannot accept a substituted committed frontier')


def _walk_prefix(walk):
    if type(walk) is not _ColdWalk or walk not in _WALKS:
        raise ValueError('Unissued cold recovery verification walk')
    return _WALKS[walk]


def _accept_verified_commit(walk,seal):
    """Consume an identity seal minted only after full V4 cold verification."""
    from .arte_journal_commit_v4 import V4CommittedPrefix
    from uuid import UUID
    import json
    binding=_FRONTIER_SEALS.pop(seal,None) if type(seal) is _VerifiedFrontierSeal else None
    if binding is None or binding[0] is not walk:
        raise ValueError('Cold frontier requires a genuine full-verifier-issued seal')
    commit=json.loads(binding[1])
    prior=_walk_prefix(walk)
    if (commit['run_id']!=walk.source.run_id
            or str(commit['prior_batch_id'])!=(prior.last_batch_id if prior else str(UUID(int=0)))
            or commit['first_sequence']!=(prior.last_sequence+1 if prior else 1)
            or prior is not None and prior.status!='running'):
        raise ValueError('Cold walk verified commit does not extend its genuine frontier')
    _WALKS[walk]=V4CommittedPrefix(commit['run_id'],commit['last_sequence'],str(commit['batch_id']),
        commit['source_cursor'],commit['status'],(*prior.batch_ids,str(commit['batch_id'])) if prior else (str(commit['batch_id']),))


def _graph(parents,actions,replies,events):
    from .arte_journal_writer import _canonical_typed_content
    from .arte_protection_reconciliation_v4 import RECONCILIATION,ACTION,REPLY
    return canonical_json([[name,sorted((_canonical_typed_content(name,
        {k:v for k,v in row.items() if k!='content_hash'},stored_utc=True) for row in values),key=canonical_json)]
        for name,values in ((RECONCILIATION.name,parents),(ACTION.name,actions),(REPLY.name,replies),('trading_event_v1',events))])


def _approved_command(client,prefix,old,proposal,entry_admission,entry_decision,requested):
    """Join the executed stop request to its exact normalized Portfolio approval."""
    from decimal import Decimal
    from .arte_oms_projection import _verified_rows,_rows,_CONTRACTS,_literal,_committed_batch_filter
    from .arte_journal_reader import _journal_instant
    from .portfolio import _intent_correlation
    def read(name,predicate):
        columns=','.join(k for k,_ in _CONTRACTS[name].columns)
        rows=_verified_rows(name,_rows(client,f'SELECT {columns} FROM arte.{name} '
            f'WHERE run_id={_literal(prefix.run_id)} AND {predicate} {_committed_batch_filter(prefix)}'
            'LIMIT 2 FORMAT JSONEachRow'))
        if len(rows)!=1:raise ValueError('Cold recovery command lacks one exact committed Portfolio approval')
        return rows[0]
    reservation=read('trading_portfolio_reservation_event_v1',
        f"intent_id IN ({_literal(old.intent.intent_id)}) AND event='reservation_created'")
    decision=read('trading_portfolio_decision_v1',f"decision_id IN ({_literal(reservation['decision_id'])})")
    correlation=_intent_correlation(prefix.run_id,old.intent)
    for name,row,kind,entity,causation in (
            ('reservation',reservation,'portfolio_reservation',reservation['reservation_id'],decision['decision_id']),
            ('decision',decision,'portfolio_decision',decision['decision_id'],old.intent.intent_id)):
        event=read('trading_event_v1',f"record_id IN (toUUID({_literal(str(row['record_id']))}))")
        if (event['batch_id']!=row['batch_id'] or event['account_id']!=proposal.account_id
                or event['entity_id']!=entity or event['category']!='portfolio_management'
                or event['entity_type']!=kind or event['correlation_id']!=correlation
                or event['causation_id']!=causation or not old.sequence<int(event['sequence'])<requested.sequence
                or _journal_instant(event['event_time'])!=old.intent.event_time):
            raise ValueError('Cold recovery command Portfolio event/source/correlation differs')
    quantity=Decimal(str(old.intent.quantity))
    if (reservation['account_id']!=proposal.account_id or reservation['assignment_id']!=proposal.assignment_id
            or reservation['strategy_id']!=requested.payload['strategy_id']
            or reservation['intent_id']!=old.intent.intent_id or reservation['ticker']!=proposal.ticker
            or reservation['action']!=old.intent.action or reservation['status']!='reserved'
            or reservation['account_key']!=entry_admission['account_key']
            or Decimal(str(reservation['quantity']))!=quantity
            or Decimal(str(reservation['reference_price']))!=Decimal(str(old.intent.reference_price))
            or decision['account_id']!=proposal.account_id or decision['request_id']!=old.intent.intent_id
            or decision['reservation_id']!=reservation['reservation_id']
            or decision['account_key']!=reservation['account_key'] or decision['ticker']!=proposal.ticker
            or decision['action']!=old.intent.action or decision['status'] not in {'approved','resized'}
            or (decision['policy_id'],int(decision['policy_revision']))!=(entry_decision['policy_id'],int(entry_decision['policy_revision']))
            or Decimal(str(decision['requested_quantity']))!=quantity
            or Decimal(str(decision['approved_quantity']))!=quantity
            or _journal_instant(decision['decided_at'])!=old.intent.event_time):
        raise ValueError('Cold recovery command differs from actual executed Portfolio approval')
    return reservation,decision


@dataclass(frozen=True,slots=True,eq=False,weakref_slot=True)
class FixedStructuralLotColdRecoveryContext:
    source: object
    batch_id: str
    before_sequence: int
    graph_json: str

    @property
    def owner(self):return self

    def verify_recovery_graph(self,context,parents,actions,replies,events,*,run_id,batch_id):
        require_cold_recovery_context(context,run_id=run_id,batch_id=batch_id,_allow_pending=True)
        if context is not self or _graph(parents,actions,replies,events)!=self.graph_json:
            raise ValueError('Cold recovery graph differs from independently verified persisted lineage')

    def verify_recovery_record(self,*args):
        raise ValueError('Cold recovery evidence cannot authorize live record projection')


def require_cold_recovery_context(context,*,run_id,batch_id,_allow_pending=False):
    if type(context) is not FixedStructuralLotColdRecoveryContext:
        raise ValueError('Exact cold recovery verification context required')
    binding=_CONTEXTS.get(context)
    if binding is None or binding[:4]!=(context.source,context.batch_id,context.before_sequence,context.graph_json) or (not _allow_pending and binding[4] is not True):
        raise ValueError('Cold recovery verification context is unissued or mutated')
    if context.source.run_id!=run_id or context.batch_id!=batch_id:
        raise ValueError('Cold recovery context has foreign run/batch/source')
    context.source.require_installed_admission()
    return context


def _issue_graph(walk,prefix,*,batch_id,parents,actions,replies,events):
    from .arte_intent_projection import load_committed_strategy_intent_page
    from .arte_journal_reader import load_complete_typed_protection_history,_journal_instant
    from .arte_oms_projection import load_latest_committed_oms_groups,load_committed_oms_admission_page,load_committed_oms_decision_page
    from .fixed_structural_lot_management import load_fixed_structural_lot_stop_ceiling
    if type(walk) is not _ColdWalk or walk not in _WALKS or _WALKS[walk] is not prefix or prefix is None:
        raise ValueError('Cold recovery requires the genuine independently verified preceding frontier')
    source=walk.source;source.require_installed_admission()
    if prefix.run_id!=source.run_id:
        raise ValueError('Cold recovery predecessor has foreign run')
    own=tuple(row for row in replies if row['reply_kind']=='broker_readback')
    if not own:raise ValueError('Cold recovery issuer needs genuine readback rows')
    entry_by_intent={v.base.intents[0]['intent_id']:v for v in walk.entries}
    groups=load_latest_committed_oms_groups(walk.client,prefix,
        allowed_accounts=frozenset(v.record.account_id for v in walk.entries),
        strategy_identity=(source._strategy_id,source._revision),require_tactic=True,fixed_lot_contexts=walk.entries)
    by_group={v.group['group_id']:v for v in groups}
    history=load_complete_typed_protection_history(walk.client,prefix,fixed_lot_contexts=walk.entries)
    by_action={row['record_id']:row for row in actions};by_parent={row['record_id']:row for row in parents}
    by_event={row['record_id']:row for row in events}
    for reply in own:
        action=by_action[reply['parent_record_id']];parent=by_parent[action['parent_record_id']];event=by_event[parent['record_id']]
        group=by_group.get(parent['order_group_id'])
        if group is None:raise ValueError('Cold recovery has unknown original acquisition group')
        entry=entry_by_intent.get(group.group['strategy_intent_id'])
        if entry is None:raise ValueError('Cold recovery lacks actual own entry source')
        request=entry.verify_source();proposal=request.entry.proposal
        admission=load_committed_oms_admission_page(walk.client,prefix,(group,))[group.sequence]
        if (parent['account_id'],parent['ticker'],parent['strategy_id'],parent['strategy_revision'],admission['assignment_id'])!=(
                proposal.account_id,proposal.ticker,request.strategy_id,request.revision,proposal.assignment_id):
            raise ValueError('Cold recovery has foreign strategy/account/Portfolio assignment')
        old,=load_committed_strategy_intent_page(walk.client,prefix,record_ids=(event['causation_id'],),limit=1)
        observed_at=_journal_instant(event['event_time'])
        desired=float(action['to_stop'])
        if (old.intent.action!='replace_protective_stop' or old.account_id!=proposal.account_id
                or old.intent.ticker!=proposal.ticker or old.intent.invalidation_price!=desired
                or old.intent.profit_target_price is not None or old.sequence>=event['sequence']
                or old.intent.event_time>=observed_at or event['sequence']<=prefix.last_sequence
                or action['action']!='readback_protective_stop' or float(action['stop_price'])!=desired
                or reply['account_id']!=proposal.account_id or reply['broker_order_id']!=action['broker_order_id']):
            raise ValueError('Cold recovery substituted original command/readback price or clock')
        bindings=[v for v in group.broker_bindings if v['broker_order_id']==reply['broker_order_id']
            and v['role']=='protective_stop' and v['terminal']==0]
        if len(bindings)!=1:raise ValueError('Cold readback lacks exact live stop binding')
        binding=bindings[0];order=group.orders[binding['request_index']]
        if reply['local_order_id']!=order.cOID or reply['conid']!=order.conid:
            raise ValueError('Cold readback substituted owned order identity')
        from decimal import Decimal
        residual=Decimal(str(order.quantity))-Decimal(str(binding['filled_quantity']))
        if residual<=0 or Decimal(str(action['quantity']))!=residual or Decimal(str(parent['required_quantity']))!=residual or Decimal(str(parent['protected_quantity']))!=residual:
            raise ValueError('Cold readback quantity differs from actual bound protective order')
        records=[r for r in history.records if r.account_id==proposal.account_id
            and r.payload.get('order_group_id')==group.group['group_id']
            and r.payload.get('client_order_id')==order.cOID and r.payload.get('order_id')==reply['broker_order_id']
            and r.payload.get('source_intent_id')==request.intent.intent_id
            and r.payload.get('strategy_id')==request.strategy_id and r.payload.get('strategy_revision')==request.revision
            and r.payload.get('intent_id')==old.intent.intent_id and r.payload.get('kind')=='stop'
            and r.payload.get('phase')=='requested' and r.payload.get('action')=='replace_protective_stop'
            and r.payload.get('price')==desired and r.event_time==old.intent.event_time
            and old.sequence<r.sequence<=prefix.last_sequence]
        if len(records)!=1:raise ValueError('Cold readback lacks exact original requested-leg lineage')
        entry_decision=load_committed_oms_decision_page(walk.client,prefix,(group,),{group.sequence:admission})[group.sequence]
        _approved_command(walk.client,prefix,old,proposal,admission,entry_decision,records[0])
        roster=load_fixed_structural_lot_stop_ceiling(walk.client,prefix,entry=request.entry,
            intervals=source.intervals,intent=request.intent,group_id=group.group['group_id'],
            strategy_identity=(request.strategy_id,request.revision),entry_request=request,fixed_lot_contexts=walk.entries)
        if roster.ceiling is None or not desired<roster.ceiling or binding['slice_id'] not in {v for v,q in roster.remaining if q>0}:
            raise ValueError('Cold readback differs from original complete active lot roster')
    result=FixedStructuralLotColdRecoveryContext(source,batch_id,prefix.last_sequence,_graph(parents,actions,replies,events))
    _CONTEXTS[result]=(source,batch_id,prefix.last_sequence,result.graph_json,False)
    return result


def _finalize_graph(context,*,run_id,batch_id):
    require_cold_recovery_context(context,run_id=run_id,batch_id=batch_id,_allow_pending=True)
    _CONTEXTS[context]=(*_CONTEXTS[context][:4],True)
