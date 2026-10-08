"""Declared selected checkpoint products; historical images grant no execution."""
from dataclasses import dataclass
from weakref import WeakKeyDictionary
import json
from .strategy_registry import SELECTED_CHECKPOINT_PRODUCT_RULE as RULE


def selected(source):
    if source is None or source.installed_payload is None:
        return False
    rules = source.installed_payload['strategy']['numbered_release']['contract']['rule_set_contracts']
    if RULE not in rules:
        return False
    if type(rules) is not list or rules.count(RULE) != 1:
        raise ValueError('Checkpoint products require one exact declared rule')
    from src.backend.backtest_fixed_structural_lot_source import require_native_fixed_structural_lot_source
    require_native_fixed_structural_lot_source(source)
    source.require_installed_admission()
    return True


def managed_exit_metadata(group, order, metadata, *, source, run_id,
                          strategy_id, strategy_revision):
    """Keep actual managed exits outside acknowledged protective-pair routing."""
    if not selected(source) or group.intent.action != 'exit':
        return None
    if (source.run_id, source._strategy_id, source._revision) != (
            run_id, strategy_id, strategy_revision):
        raise ValueError('Managed exit metadata has foreign installed source')
    if (order.parentId or order.side != 'SELL'
            or metadata.get('action') != 'exit'
            or metadata.get('execution_role') != 'managed_exit'
            or [value for value in group.orders if value.cOID == order.cOID] != [order]
            or order.acctId != group.account_id or order.ticker != group.intent.ticker):
        raise ValueError('Managed exit metadata differs from exact typed order role')
    return metadata


_HISTORICAL_READ_ISSUER = object()
_OMS_READ_SCOPES = WeakKeyDictionary()
_CLOSING_READS = WeakKeyDictionary()


@dataclass(frozen=True,slots=True,eq=False,weakref_slot=True)
class _SourceOmsReadScope:
    source: object
    prefix: object
    contexts: tuple
    recoveries: tuple


def _read_prefix_identity(prefix):
    return (prefix.run_id,prefix.last_sequence,prefix.last_batch_id,
            prefix.source_cursor,prefix.status,prefix.batch_ids)


def _read_recovery_inventory(client,source,prefix):
    from src.backend.backtest_fixed_structural_lot_management import recovery_contexts_by_batch
    recoveries=tuple(getattr(client,'fixed_lot_recovery_contexts',()))
    recovery_contexts_by_batch(source.run_id,recoveries,max_commits=100_000)
    for batch,context in recoveries:
        recovery_source=getattr(context,'source',None)
        if recovery_source is None:
            recovery_source=context.request.entry_request.source
        if batch not in prefix.batch_ids or recovery_source is not source:
            raise ValueError('Selected OMS read has foreign recovery source inventory')
    return recoveries


def require_source_oms_read_scope(scope, client, prefix):
    from .arte_journal_commit_v4 import V4CommittedPrefix
    if (type(scope) is not _SourceOmsReadScope
            or type(scope.prefix) is not V4CommittedPrefix
            or _OMS_READ_SCOPES.get(scope) !=
            (client,scope.source,scope.prefix,scope.contexts,scope.recoveries,_read_prefix_identity(scope.prefix))
            or type(prefix) is not V4CommittedPrefix or prefix != scope.prefix
            or source_for_client(client) is not scope.source
            or prefix.run_id != scope.source.run_id):
        raise ValueError('Selected OMS source read scope is unissued or foreign')
    scope.source.require_installed_admission()
    from .fixed_structural_lot_entry_v4 import fixed_lot_contexts_by_batch
    fixed_lot_contexts_by_batch(prefix.run_id,scope.contexts,max_commits=100_000)
    for context in scope.contexts:
        if context.source is not scope.source or context.base.batch_id not in prefix.batch_ids:
            raise ValueError('Selected OMS read has foreign original source inventory')
        context.verify_admission()
    if _read_recovery_inventory(client,scope.source,prefix)!=scope.recoveries:
        raise ValueError('Selected OMS read recovery inventory changed')
    return scope


def source_bound_oms_lineages(client,prefix,*,source,contexts,allowed_accounts):
    """Read all actual entry and exit groups without issuing resume authority."""
    from .arte_journal_commit_v4 import V4CommittedPrefix
    from src.backend.backtest_fixed_structural_lot_source import require_native_fixed_structural_lot_source
    require_native_fixed_structural_lot_source(source)
    if (not selected(source) or source_for_client(client) is not source
            or type(prefix) is not V4CommittedPrefix or prefix.run_id!=source.run_id
            or type(prefix.last_sequence) is not int or prefix.last_sequence<=0
            or prefix.status not in {'running','completed','stopped','failed'}
            or type(prefix.batch_ids) is not tuple or not 1<=len(prefix.batch_ids)<=100_000
            or prefix.last_batch_id!=prefix.batch_ids[-1]
            or len(set(prefix.batch_ids))!=len(prefix.batch_ids)
            or type(contexts) is not tuple):
        raise ValueError('Selected OMS read requires its exact issued source')
    from .fixed_structural_lot_entry_v4 import fixed_lot_contexts_by_batch
    fixed_lot_contexts_by_batch(source.run_id,contexts,max_commits=100_000)
    if any(value.source is not source or value.base.batch_id not in prefix.batch_ids for value in contexts):
        raise ValueError('Selected OMS read has foreign or missing fresh source context')
    recoveries=_read_recovery_inventory(client,source,prefix)
    from .arte_journal_writer import _CONTRACTS,_literal,_rows,canonical_json
    from hashlib import sha256
    columns=','.join(name for name,_ in _CONTRACTS['trading_commit_v4'].columns)
    headers=_rows(client,f'SELECT {columns} FROM arte.trading_commit_v4 WHERE run_id={_literal(source.run_id)} AND batch_id=toUUID({_literal(prefix.last_batch_id)}) LIMIT 2 FORMAT JSONEachRow')
    if len(headers)!=1:
        raise ValueError('Selected OMS read lacks its exact committed header')
    header=headers[0]
    if ((header['run_id'],str(header['batch_id']),header['last_sequence'],header['source_cursor'],header['status']) !=
            (prefix.run_id,prefix.last_batch_id,prefix.last_sequence,prefix.source_cursor,prefix.status)
            or sha256(canonical_json({k:v for k,v in header.items() if k not in {'committed_at','content_hash'}}).encode()).hexdigest()!=header['content_hash']):
        raise ValueError('Selected OMS read has mutated committed scope')
    chain=_rows(client,f'SELECT batch_id,prior_batch_id,first_sequence,last_sequence FROM arte.trading_commit_v4 WHERE run_id={_literal(source.run_id)} AND first_sequence>=1 AND last_sequence<={prefix.last_sequence} ORDER BY first_sequence LIMIT {len(prefix.batch_ids)+1} FORMAT JSONEachRow')
    if len(chain)!=len(prefix.batch_ids) or tuple(str(v['batch_id']) for v in chain)!=prefix.batch_ids:
        raise ValueError('Selected OMS read has an incomplete committed chain')
    for index,value in enumerate(chain):
        if (value['first_sequence']!=(1 if index==0 else chain[index-1]['last_sequence']+1)
                or value['last_sequence']<value['first_sequence']
                or index==0 and str(value['prior_batch_id'])!='00000000-0000-0000-0000-000000000000'
                or index and str(value['prior_batch_id'])!=prefix.batch_ids[index-1]):
            raise ValueError('Selected OMS read committed chain is forked or discontinuous')
    scope=_SourceOmsReadScope(source,prefix,contexts,recoveries)
    _OMS_READ_SCOPES[scope]=(client,source,prefix,contexts,recoveries,_read_prefix_identity(prefix))
    require_source_oms_read_scope(scope,client,prefix)
    from .arte_oms_projection import load_recovered_strategy_one_oms_lineage
    return load_recovered_strategy_one_oms_lineage(client,prefix,
        allowed_accounts=allowed_accounts,strategy_number=source._revision,
        first_price_source=source.price_authority,fixed_lot_read_scope=scope)


@dataclass(frozen=True,slots=True,eq=False,weakref_slot=True)
class ClosingCheckpointRead:
    """Historical acquisition facts plus a distinct aggregate closing balance.

    This capability authorizes checkpoint reads only. It is never a stop ceiling
    or an instruction to allocate aggregate exit fills among independent lots.
    """
    source: object
    prefix: object
    entry_intent_id: str
    acquisition_group_id: str
    exit_group_id: str
    aggregate_remaining: object
    closing_filled: object
    cancellation_pending: bool
    exit_pending: bool
    observed_boundary_ms: int


def _holdings_at_exit_issuance(client,prefix,acquisition,exit_lineage,*,prior_exits=()):
    """Reconcile every owned fill, then select the causal exit issuance cut."""
    from decimal import Decimal
    from .arte_journal_writer import _rows,_literal,_committed_batch_filter,_CONTRACTS,load_committed_execution_page
    from .arte_oms_projection import _verified_rows
    from .arte_journal_reader import _journal_instant
    owned_lineages=(acquisition,exit_lineage,*prior_exits)
    groups=tuple(value.state for value in owned_lineages)
    source_sequences={id(value.state):value.source_intent.sequence for value in owned_lineages}
    orders={binding['broker_order_id']:group.orders[binding['request_index']]
            for group in groups for binding in group.broker_bindings}
    owners={binding['broker_order_id']:group for group in groups for binding in group.broker_bindings}
    if len(orders)!=sum(len(group.broker_bindings) for group in groups):
        raise ValueError('Closing issuance has duplicate acquired broker ownership')
    columns=','.join(name for name,_ in _CONTRACTS['trading_execution_v1'].columns)
    rows=_rows(client,f'SELECT {columns} FROM arte.trading_execution_v1 WHERE run_id={_literal(prefix.run_id)} '
        +'AND broker_order_id IN ('+','.join(_literal(value) for value in orders)+') '
        +_committed_batch_filter(prefix)+'ORDER BY source_event_time,record_id LIMIT 100001 FORMAT JSONEachRow')
    if len(rows)>100000:
        raise ValueError('Closing issuance execution inventory exceeds bound')
    _verified_rows('trading_execution_v1',rows)
    ids=tuple(row['execution_id'] for row in rows)
    if len(set(ids))!=len(ids):
        raise ValueError('Closing issuance execution inventory repeats')
    envelopes={}
    for offset in range(0,len(ids),999):
        for envelope in load_committed_execution_page(client,prefix,
                execution_ids=ids[offset:offset+999],limit=1000):
            if envelope['execution_id'] in envelopes:
                raise ValueError('Closing issuance execution envelope repeats')
            envelopes[envelope['execution_id']]=envelope
    totals={identity:Decimal(0) for identity in orders}
    causal=Decimal(0)
    issued_sequence=exit_lineage.source_intent.sequence
    issued_time=exit_lineage.source_intent.intent.event_time
    latest=max(_journal_instant(group.group['updated_at']) for group in groups)
    latest=max(latest,issued_time)
    for row in rows:
        envelope=envelopes.get(row['execution_id'])
        order=orders.get(row['broker_order_id'])
        group=owners.get(row['broker_order_id'])
        source_sequence=source_sequences.get(id(group),prefix.last_sequence)
        quantity=Decimal(str(row['quantity']))
        if (envelope is None or order is None or not quantity.is_finite() or quantity<=0
                or any(envelope.get(key)!=value for key,value in row.items())
                or not source_sequence<envelope['sequence']<=prefix.last_sequence
                or row['run_id']!=prefix.run_id or str(row['batch_id']) not in prefix.batch_ids
                or row['account_id']!=group.group['account_id']
                or row['strategy_id']!=group.group['strategy_id']
                or int(row['strategy_revision'])!=int(group.group['strategy_revision'])
                or row['ticker']!=order.ticker or row['client_order_id']!=order.cOID
                or int(row['conid'])!=order.conid
                or row['side'] not in ({'B','BUY'} if order.side=='BUY' else {'S','SELL'})):
            raise ValueError('Closing issuance execution differs from exact committed ownership')
        totals[row['broker_order_id']]+=quantity
        latest=max(latest,_journal_instant(row['source_event_time']))
        if group is not exit_lineage.state and envelope['sequence']<issued_sequence:
            if _journal_instant(row['source_event_time'])>issued_time:
                raise ValueError('Closing issuance observes a future acquired execution')
            causal+=quantity if order.side=='BUY' else -quantity
    for group in groups:
        for binding in group.broker_bindings:
            expected=Decimal(str(binding['filled_quantity'])) if binding['has_filled_quantity'] else Decimal(0)
            if totals[binding['broker_order_id']]!=expected:
                raise ValueError('Closing issuance lacks complete owned execution quantities')
    if causal<0:
        raise ValueError('Closing issuance acquired holdings are negative')
    return causal,latest


def closing_checkpoint_read(client,prefix,*,entry_request,contexts):
    from decimal import Decimal
    from .order_management import OrderManagementState,TERMINAL_MANAGEMENT_STATES
    source=entry_request.source
    if not selected(source):
        return None
    entry_request.verify()
    lineages=source_bound_oms_lineages(client,prefix,source=source,contexts=contexts,
        allowed_accounts=frozenset((entry_request.entry.proposal.account_id,)))
    acquisitions=[v for v in lineages if v.state.group['strategy_intent_id']==entry_request.intent.intent_id]
    if len(acquisitions)!=1:
        raise ValueError('Closing checkpoint lacks its unique certified acquisition')
    acquisition=acquisitions[0]
    if acquisition.state.group['protection_delegated']==0:
        return None
    exits=[v for v in lineages if v.source_entry_intent_id==entry_request.intent.intent_id]
    if len(exits)!=1:
        raise ValueError('Delegated acquisition lacks one exact typed closing owner')
    exit_lineage=exits[0]
    for lineage in (acquisition,exit_lineage):
        state=lineage.state
        OrderManagementState(state.group['state'])
        if state.group['state'] in {'outcome_unknown','rejected','policy_blocked'}:
            raise ValueError('Closing checkpoint has unresolved financial ownership')
        if (state.group['account_id']!=entry_request.entry.proposal.account_id
                or state.group['strategy_revision']!=source._revision
                or lineage.approved_intent.metadata.get('assignment_id')!=entry_request.entry.proposal.assignment_id
                or lineage.approved_intent.ticker!=entry_request.entry.proposal.ticker):
            raise ValueError('Closing checkpoint has foreign assignment ownership')
    if (exit_lineage.state.group['protection_delegated']!=0
            or exit_lineage.approved_intent.action!='exit'
            or exit_lineage.state.first_sequence is None
            or acquisition.state.first_sequence is None
            or exit_lineage.state.first_sequence<=acquisition.state.first_sequence):
        raise ValueError('Closing checkpoint lacks causal independent exit ownership')
    source_ids={v['broker_order_id'] for v in acquisition.state.broker_bindings}
    exit_ids={v['broker_order_id'] for v in exit_lineage.state.broker_bindings}
    if (not source_ids or not exit_ids or source_ids&exit_ids
            or len(source_ids)!=len(acquisition.state.broker_bindings)
            or len(exit_ids)!=len(exit_lineage.state.broker_bindings)):
        raise ValueError('Closing checkpoint requires complete distinct fresh exit bindings')
    def filled(state):
        result=Decimal(0)
        bought=Decimal(0)
        indexes=set()
        for binding in state.broker_bindings:
            index=binding['request_index']
            if (type(index) is not int or not 0<=index<len(state.orders)
                    or index in indexes or binding['has_filled_quantity'] not in (0,1)
                    or binding['terminal'] not in (0,1)):
                raise ValueError('Closing checkpoint has malformed broker fill inventory')
            indexes.add(index)
            order=state.orders[index]
            quantity=Decimal(str(binding['filled_quantity'])) if binding['has_filled_quantity'] else Decimal(0)
            if not quantity.is_finite() or not 0<=quantity<=Decimal(str(order.quantity)):
                raise ValueError('Closing checkpoint fill exceeds exact owned request')
            result+=quantity if order.side=='BUY' else -quantity
            if order.side=='BUY':
                bought+=quantity
        if indexes!=set(range(len(state.orders))):
            raise ValueError('Closing checkpoint lacks complete broker request coverage')
        return result,bought
    acquired,gross_acquired=filled(acquisition.state)
    exit_balance,exit_bought=filled(exit_lineage.state)
    exited=-exit_balance
    exit_requested=sum(Decimal(str(order.quantity)) for order in exit_lineage.orders)
    issuance_holdings,latest_execution=_holdings_at_exit_issuance(client,prefix,acquisition,exit_lineage)
    if (acquired<0 or exited<0 or exited>acquired
            or exit_bought or exit_requested>gross_acquired
            or exit_requested>issuance_holdings
            or any(order.side!='SELL' or order.parentId for order in exit_lineage.orders)
            or exit_requested<exited):
        raise ValueError('Closing checkpoint violates aggregate quantity conservation')
    if (exit_lineage.state.group['state']=='cancelled' and exited<acquired):
        raise ValueError('Cancelled exit leaves delegated acquired holdings unresolved')
    cancellation_pending=any(v['terminal']==0 for v in acquisition.state.broker_bindings)
    exit_pending=(any(v['terminal']==0 for v in exit_lineage.state.broker_bindings)
        or OrderManagementState(exit_lineage.state.group['state']) not in TERMINAL_MANAGEMENT_STATES)
    from .fixed_structural_lot_causal_clock import observed_clock
    observed=observed_clock(latest_execution.strftime('%Y-%m-%d %H:%M:%S.%f'),
        session_date=entry_request.entry.session_date,
        entry_boundary_ms=entry_request.entry.proposal.boundary_ms)
    product=ClosingCheckpointRead(source,prefix,entry_request.intent.intent_id,
        acquisition.state.group['group_id'],exit_lineage.state.group['group_id'],
        acquired-exited,exited,cancellation_pending,exit_pending,observed)
    _CLOSING_READS[product]=(client,entry_request,contexts,lineages,repr(lineages),
                            _read_prefix_identity(prefix),
                            (product.source,product.prefix,product.entry_intent_id,
                             product.acquisition_group_id,product.exit_group_id,
                             product.aggregate_remaining,product.closing_filled,product.cancellation_pending,product.exit_pending,product.observed_boundary_ms))
    return product


def require_closing_checkpoint_read(product,*,client,prefix,entry_request,state):
    if type(product) is not ClosingCheckpointRead or product not in _CLOSING_READS:
        raise ValueError('Closing checkpoint read was not issued')
    bound=_CLOSING_READS[product]
    if (bound[0] is not client or bound[1] is not entry_request
            or bound[4]!=repr(bound[3]) or bound[5]!=_read_prefix_identity(prefix)
            or bound[6]!=(product.source,product.prefix,product.entry_intent_id,
                product.acquisition_group_id,product.exit_group_id,
                product.aggregate_remaining,product.closing_filled,product.cancellation_pending,product.exit_pending,product.observed_boundary_ms)
            or prefix!=product.prefix or product.source is not entry_request.source
            or state.group['group_id']!=product.acquisition_group_id
            or state!=next(v.state for v in bound[3] if v.state.group['group_id']==product.acquisition_group_id)):
        raise ValueError('Closing checkpoint read has foreign or mutated ownership')
    entry_request.verify()
    return product


def checkpoint_roster(client,prefix,*,entry_request=None,fixed_lot_contexts=(),**arguments):
    """Declared checkpoint protocol preserves historical per-lot acquisition.

    Aggregate closing holdings come from ClosingCheckpointRead and matching
    financial roots. LOT.remaining retains its acquisition/protection meaning;
    it is never a fabricated distribution of a fresh aggregate SELL fill.
    """
    from .fixed_structural_lot_management import load_fixed_structural_lot_stop_ceiling
    closing=None
    if entry_request is not None and selected(entry_request.source):
        closing=closing_checkpoint_read(client,prefix,entry_request=entry_request,
            contexts=fixed_lot_contexts)
    roster=load_fixed_structural_lot_stop_ceiling(client,prefix,
        entry_request=entry_request,fixed_lot_contexts=fixed_lot_contexts,
        _checkpoint_closing=closing,**arguments)
    if closing is not None:
        from dataclasses import replace
        roster=replace(roster,observed_boundary_ms=max(roster.observed_boundary_ms,closing.observed_boundary_ms))
    return roster


def checkpoint_acquisitions(client,prefix,*,source,contexts,allowed_accounts):
    """Verify every group; only certified acquisitions have independent lots."""
    lineages=source_bound_oms_lineages(client,prefix,source=source,contexts=contexts,
        allowed_accounts=allowed_accounts)
    entries={v.unit.base.intents[0]['intent_id']:v for v in contexts}
    acquisitions=[]
    for lineage in lineages:
        identity=lineage.state.group['strategy_intent_id']
        if identity in entries:
            if lineage.source_entry_intent_id is not None or lineage.approved_intent.action!='enter_long':
                raise ValueError('Checkpoint acquisition differs from exact native source')
            acquisitions.append(lineage.state)
        elif lineage.source_entry_intent_id not in entries:
            raise ValueError('Checkpoint contains an unowned typed OMS group')
    return tuple(acquisitions)


def checkpoint_aggregate_quantity(client,prefix,*,entry_request,contexts,roster):
    from decimal import Decimal
    closing=closing_checkpoint_read(client,prefix,entry_request=entry_request,contexts=contexts)
    return (closing.aggregate_remaining if closing is not None
            else sum((quantity for _,quantity in roster.remaining),Decimal(0)))


def checkpoint_acquisition_active(client,prefix,*,entry_request,contexts,roster):
    closing=closing_checkpoint_read(client,prefix,entry_request=entry_request,contexts=contexts)
    if closing is not None:
        return bool(closing.aggregate_remaining or closing.cancellation_pending or closing.exit_pending)
    return bool(any(quantity>0 for _,quantity in roster.remaining) or roster.acquiring)


class _HistoricalReads:
    """Historical graph reads carry no exclusive writer or execution authority."""
    def __init__(self, client, source, prefix, contexts, recoveries, *, issuer):
        from .arte_journal_commit_v4 import V4CommittedPrefix
        if (issuer is not _HISTORICAL_READ_ISSUER or not selected(source)
                or type(prefix) is not V4CommittedPrefix or prefix.run_id != source.run_id
                or not prefix.batch_ids or prefix.last_batch_id != prefix.batch_ids[-1]
                or type(contexts) is not tuple or type(recoveries) is not tuple
                or any(value.source is not source or value.base.batch_id not in prefix.batch_ids
                       for value in contexts)):
            raise ValueError('Historical reader lacks exact issued source/prefix scope')
        from .fixed_structural_lot_profile import require_fixed_structural_lot_profile
        from src.backend.backtest_fixed_structural_lot_management import recovery_contexts_by_batch
        if require_fixed_structural_lot_profile(client.fixed_structural_lot_profile).operation.source is not source:
            raise ValueError('Historical reader has foreign issued profile source')
        recovery_contexts_by_batch(source.run_id,recoveries,max_commits=100_000)
        for batch,context in recoveries:
            recovery_source=getattr(context,'source',None)
            if recovery_source is None:
                recovery_source=context.request.entry_request.source
            if batch not in prefix.batch_ids or recovery_source is not source:
                raise ValueError('Historical reader has foreign recovery source')
        self._client = client
        self._source = source
        self._prefix = prefix
        self.fixed_structural_lot_profile = client.fixed_structural_lot_profile
        self.fixed_structural_lot_contexts = contexts
        self.fixed_lot_recovery_contexts = recoveries
        self.backtest_v4_lease = None
        self.backtest_v4_dispatch_gate = None
        self.v4_batched_detail_readback = bool(getattr(client,'v4_batched_detail_readback',False))
        self._scope = (source,prefix,contexts,recoveries,self.fixed_structural_lot_profile,
                       _read_prefix_identity(prefix))

    def execute(self, sql, *args, **kwargs):
        from src.backend.backtest_market_data import assert_select_only
        from .arte_journal_commit_v4 import V4CommittedPrefix
        if type(self._prefix) is not V4CommittedPrefix or self._scope != (self._source,self._prefix,self.fixed_structural_lot_contexts,
                           self.fixed_lot_recovery_contexts,self.fixed_structural_lot_profile,
                           _read_prefix_identity(self._prefix)):
            raise ValueError('Historical reader issued scope changed')
        if args or kwargs or type(sql) is not str or not sql.lstrip().startswith('SELECT '):
            raise ValueError('Historical reader requires exact SELECT transport')
        return self._client.execute(assert_select_only(sql))

def source_for_client(client):
    profile = getattr(client, 'fixed_structural_lot_profile', None)
    if profile is None:
        return None
    from .fixed_structural_lot_profile import require_fixed_structural_lot_profile
    source = require_fixed_structural_lot_profile(profile).operation.source
    return source if selected(source) else None


def terminal_scope(client, run_id):
    source=source_for_client(client)
    if source is None:
        return {}
    contexts=tuple(getattr(client,'fixed_structural_lot_contexts',()))
    recoveries=tuple(getattr(client,'fixed_lot_recovery_contexts',()))
    require_terminal_scope(client,run_id,source,contexts,recoveries)
    return dict(fixed_lot_source=source,fixed_lot_contexts=contexts,fixed_lot_recovery_contexts=recoveries)


def require_terminal_scope(client,run_id,source,contexts,recoveries):
    if (not selected(source) or source_for_client(client) is not source or source.run_id!=run_id
            or type(contexts) is not tuple or type(recoveries) is not tuple
            or contexts!=tuple(getattr(client,'fixed_structural_lot_contexts',()))
            or recoveries!=tuple(getattr(client,'fixed_lot_recovery_contexts',()))
            or any(value.source is not source for value in contexts)):
        raise ValueError('Terminal selected contexts differ from exact issued source inventory')
    from .fixed_structural_lot_entry_v4 import fixed_lot_contexts_by_batch
    from src.backend.backtest_fixed_structural_lot_management import recovery_contexts_by_batch
    fixed_lot_contexts_by_batch(run_id,contexts,max_commits=100_000)
    recovery_contexts_by_batch(run_id,recoveries,max_commits=100_000)
    for _,context in recoveries:
        recovery_source=getattr(context,'source',None)
        if recovery_source is None:
            recovery_source=context.request.entry_request.source
        if recovery_source is not source:
            raise ValueError('Terminal recovery context has foreign source')


class CheckpointReader:
    """Read transport carrying the actual owner's issued immutable contexts."""
    def __init__(self, client, owner):
        source = owner.operation.source
        if not selected(source):
            raise ValueError('Selected checkpoint reader lacks installed declaration')
        self._client = client
        self.fixed_structural_lot_profile = owner.client.fixed_structural_lot_profile
        from .fixed_structural_lot_profile import require_fixed_structural_lot_profile
        if require_fixed_structural_lot_profile(self.fixed_structural_lot_profile).operation.source is not source:
            raise ValueError('Checkpoint reader has foreign issued profile source')
        self.fixed_structural_lot_contexts = tuple(getattr(owner.client, 'fixed_structural_lot_contexts', ()))
        self.fixed_lot_recovery_contexts = tuple(getattr(owner.client, 'fixed_lot_recovery_contexts', ()))
        if any(value.source is not source for value in self.fixed_structural_lot_contexts):
            raise ValueError('Checkpoint reader has foreign entry source')
        from .fixed_structural_lot_entry_v4 import fixed_lot_contexts_by_batch
        from src.backend.backtest_fixed_structural_lot_management import recovery_contexts_by_batch
        fixed_lot_contexts_by_batch(source.run_id,self.fixed_structural_lot_contexts,max_commits=100_000)
        recovery_contexts_by_batch(source.run_id,self.fixed_lot_recovery_contexts,max_commits=100_000)
    def __getattr__(self, name):
        return getattr(self._client, name)


def manager_head_reader(session, source):
    from .strategy_one_management_snapshot import ManagedManagerSnapshotHeadReader
    if not selected(source):
        return ManagedManagerSnapshotHeadReader(session)
    from .fixed_structural_lot_manager_snapshot import selected_manager_head_path
    class Reader(ManagedManagerSnapshotHeadReader):
        path = staticmethod(selected_manager_head_path)
    return Reader(session)


def current_checkpoint(client, keeper, *, run_id, sequence, first_price_source=None):
    source = source_for_client(client)
    if source is None:
        return None
    if source.run_id != run_id or source.price_authority is not first_price_source:
        raise ValueError('Selected checkpoint has foreign run or price source')
    from .fixed_structural_lot_manager_snapshot import load_cold_manager_image, require_cold_manager_image
    image = load_cold_manager_image(client,keeper._session,source=source,
        fixed_lot_contexts=client.fixed_structural_lot_contexts,
        recovery_contexts=())
    require_cold_manager_image(image,source=source)
    if image.sequence != sequence:
        raise ValueError('Selected checkpoint differs from requested receipt')
    return image


def checkpoint_root(client, *, source, sequence):
    from .fixed_structural_lot_manager_schema import PARENT
    from .arte_journal_writer import _literal
    from .fixed_structural_lot_manager_snapshot import _digest
    from src.backend.backtest_market_data import assert_select_only
    import json
    if not selected(source) or type(sequence) is not int or sequence <= 0:
        raise ValueError('Selected checkpoint root lacks source and exact sequence')
    columns=','.join(name for name,_ in PARENT.columns)
    sql=assert_select_only(f'SELECT {columns} FROM arte.{PARENT.name} WHERE run_id={_literal(source.run_id)} AND checkpoint_sequence={sequence} LIMIT 2 FORMAT JSONEachRow')
    rows=tuple(json.loads(line) for line in client.execute(sql).splitlines() if line.strip())
    if len(rows)!=1:
        raise ValueError('Selected checkpoint root is missing or ambiguous')
    root=rows[0]
    if (root['run_id']!=source.run_id or root['checkpoint_sequence']!=sequence
            or root['selected_configuration_hash']!=source.selected_configuration_hash
            or root['session_date']!=source.session_date.isoformat()
            or root['content_hash']!=_digest({k:v for k,v in root.items() if k!='content_hash'})):
        raise ValueError('Selected checkpoint root differs from source/content')
    return root


@dataclass(frozen=True,slots=True,eq=False,weakref_slot=True)
class HistoricalSelectedCheckpointImage:
    source: object
    run_id: str
    sequence: int
    batch_id: str
    inherited: object
    selected_positions: tuple
    financial_roots: tuple
    prefix: object
    contexts: tuple


_HISTORICAL_IMAGES=WeakKeyDictionary()


def require_historical_checkpoint(image, *, source):
    from .fixed_structural_lot_manager_snapshot import _canonical
    from src.backend.backtest_fixed_structural_lot_management import _small_tree
    if type(image) is not HistoricalSelectedCheckpointImage or not selected(source):
        raise ValueError('Exact issued historical selected checkpoint required')
    binding=(source,image.run_id,image.sequence,image.batch_id,
        _canonical(_small_tree((image.inherited,image.selected_positions,image.financial_roots))),image.prefix,image.contexts)
    if image.source is not source or _HISTORICAL_IMAGES.get(image)!=binding:
        raise ValueError('Historical checkpoint is unissued, changed or foreign')
    for context in image.contexts:
        if context.source is not source:
            raise ValueError('Historical checkpoint has foreign entry inventory')
        context.verify_admission()
    return image


def original_entry(client,run_id,intent_id,*,prior_batch_id,exit_batch_id,verified_prefix,first_price_source):
    """Resolve the actual selected companion and preserve strict ancestry."""
    source=source_for_client(client)
    if source is None:
        return None
    from .arte_journal_commit_v4 import V4CommittedPrefix,load_verified_commit_v4,verified_batch_predecessor
    from .arte_followthrough_failure_v4 import _verify_source_ancestor_interval
    from .arte_journal_writer import _rows,_literal,_CONTRACTS
    from .fixed_structural_lot_entry_schema import ENTRY
    if (type(verified_prefix) is not V4CommittedPrefix or verified_prefix.run_id!=run_id
            or source.run_id!=run_id or source.price_authority is not first_price_source):
        raise ValueError('Selected original entry lacks exact source/prefix/price')
    contexts=tuple(getattr(client,'fixed_structural_lot_contexts',()))
    matches=[]
    for context in contexts:
        if context.source is not source:
            raise ValueError('Selected original entry has foreign context')
        request=context.verify_source()
        root=context.unit.packet.root
        if intent_id in (str(root['intent_id']),str(root['original_intent_id'])):
            if (root['original_intent_id']!=request.original.intent_id
                    or root['intent_id']!=request.intent.intent_id):
                raise ValueError('Selected original entry link differs from issued source')
            matches.append(context)
    if len(matches)!=1:
        raise ValueError('Selected original entry is missing or ambiguous')
    context=matches[0];context.verify_admission()
    batch_id=context.base.batch_id
    if batch_id not in verified_prefix.batch_ids:
        raise ValueError('Selected original entry is outside committed ancestry')
    def read(name,predicate):
        columns=','.join(k for k,_ in _CONTRACTS[name].columns)
        rows=_rows(client,f'SELECT {columns} FROM arte.{name} WHERE run_id={_literal(run_id)} AND {predicate} LIMIT 2 FORMAT JSONEachRow')
        if len(rows)!=1:
            raise ValueError('Selected original entry readback is missing or ambiguous')
        return rows[0]
    native_intent_id=str(context.unit.packet.root['intent_id'])
    intent=read('trading_strategy_intent_v1',f'intent_id IN ({_literal(native_intent_id)})')
    event=read('trading_event_v1',f'record_id IN (toUUID({_literal(str(intent["record_id"]))}))')
    child=read(ENTRY.name,f'parent_record_id IN (toUUID({_literal(str(intent["record_id"]))}))')
    if (str(child['intent_id'])!=native_intent_id or child['original_intent_id']!=context.unit.packet.root['original_intent_id']
            or event['sequence']>verified_prefix.last_sequence or event['entity_type']!='fixed_structural_lot_entry_intent'
            or str(intent['batch_id'])!=batch_id):
        raise ValueError('Selected original entry companion differs from source/event')
    preceding=verified_batch_predecessor(client,verified_prefix,batch_id)
    commit,_=load_verified_commit_v4(client,run_id=run_id,batch_id=batch_id,
        first_price_source=first_price_source,fixed_lot_context=context,
        **({'verified_prior_prefix':preceding} if preceding is not None else {}))
    cursor=prior_batch_id
    if cursor is None:
        cursor=str(read('trading_commit_v4',f'batch_id=toUUID({_literal(exit_batch_id)})')['prior_batch_id'])
    if cursor not in verified_prefix.batch_ids:
        raise ValueError('Selected original entry has foreign exit predecessor')
    predecessor=commit if cursor==batch_id else read('trading_commit_v4',f'batch_id=toUUID({_literal(cursor)})')
    _verify_source_ancestor_interval(client,run_id,commit,predecessor)
    return intent,event,{**child,'strategy_number':child['revision']}


def load_historical_checkpoint(client, verified_prefix, *, source, sequence):
    """Fresh source + complete committed cursor and normalized state, SELECT only.

    No live owner/checkpoint registry is consulted. Recovery batches require
    independently issued cold graph contexts; absence remains fail-closed.
    """
    from .fixed_structural_lot_manager_snapshot import _digest,_canonical
    from .arte_journal_projection import load_latest_backtest_cursor
    from .arte_journal_writer import _literal,_verify_run_identity
    from .strategy_one_management_snapshot import (ManagedManagerSnapshotHeadReader,
        ManagerSnapshotRows,SOURCE,BREAK,HIGH,CLOSED,FIRST_HELD,_restore_manager_snapshot_scalar)
    from .strategy_one_protection_snapshot import _load_protection_snapshot_rows
    from .fixed_structural_lot_manager_schema import PARENT
    from .fixed_structural_lot_snapshot import ROOT,LOT,RESISTANCE,FixedStructuralLotSnapshotRows,restore_fixed_structural_lot_snapshot
    from .arte_portfolio_snapshot import load_portfolio_snapshot
    from .strategy_one_broker_match_snapshot import ManagedBrokerMatchHeadReader,load_unattested_broker_match_snapshot,float64_from_bits
    from .strategy_one_oms_observation_snapshot import ManagedOmsObservationHeadReader,load_unattested_oms_observation_snapshot
    from .arte_oms_projection import load_latest_committed_oms_groups
    from src.backend.backtest_market_data import assert_select_only
    from src.backend.backtest_fixed_structural_lot_source import require_native_fixed_structural_lot_source
    require_native_fixed_structural_lot_source(source)
    source.require_installed_admission()
    from dataclasses import replace
    from .arte_journal_commit_v4 import V4CommittedPrefix
    from .fixed_structural_lot_entry_v4 import fixed_lot_contexts_by_batch
    if (not selected(source) or type(verified_prefix) is not V4CommittedPrefix
            or verified_prefix.run_id!=source.run_id or not verified_prefix.batch_ids
            or verified_prefix.last_batch_id!=verified_prefix.batch_ids[-1]
            or type(sequence) is not int or not 0<sequence<=verified_prefix.last_sequence):
        raise ValueError('Historical selected checkpoint lacks verified committed scope')
    cursor=load_latest_backtest_cursor(client,replace(verified_prefix,last_sequence=sequence))
    if (type(cursor) is not dict or cursor.get('run_id')!=source.run_id
            or cursor.get('event_sequence')!=sequence
            or str(cursor.get('batch_id')) not in verified_prefix.batch_ids):
        raise ValueError('Historical checkpoint lacks exact committed cursor')
    batches=verified_prefix.batch_ids[:verified_prefix.batch_ids.index(str(cursor['batch_id']))+1]
    from .arte_journal_writer import _CONTRACTS,_rows,canonical_json
    from hashlib import sha256
    columns=','.join(name for name,_ in _CONTRACTS['trading_commit_v4'].columns)
    def committed_header(identity):
        rows=_rows(client,f'SELECT {columns} FROM arte.trading_commit_v4 WHERE run_id={_literal(source.run_id)} AND batch_id=toUUID({_literal(identity)}) LIMIT 2 FORMAT JSONEachRow')
        if len(rows)!=1:
            raise ValueError('Historical checkpoint commit is missing or ambiguous')
        row=rows[0]
        content={k:v for k,v in row.items() if k not in {'committed_at','content_hash'}}
        if (row['run_id']!=source.run_id or str(row['batch_id'])!=identity
                or sha256(canonical_json(content).encode()).hexdigest()!=row['content_hash']):
            raise ValueError('Historical checkpoint commit seal differs')
        return row
    latest=committed_header(verified_prefix.last_batch_id)
    if (latest['last_sequence'],latest['source_cursor'],latest['status'])!=(verified_prefix.last_sequence,verified_prefix.source_cursor,verified_prefix.status):
        raise ValueError('Historical checkpoint has mutated verified frontier')
    header=committed_header(batches[-1])
    if header['last_sequence']!=sequence or header['status']!='running':
        raise ValueError('Historical checkpoint must select a complete running batch')
    prefix=V4CommittedPrefix(source.run_id,sequence,batches[-1],header['source_cursor'],header['status'],batches)
    fixed_lot_contexts=tuple(v for v in getattr(client,'fixed_structural_lot_contexts',()) if v.base.batch_id in batches)
    if any(v.source is not source for v in fixed_lot_contexts):
        raise ValueError('Historical checkpoint has foreign original entry source')
    fixed_lot_contexts_by_batch(source.run_id,fixed_lot_contexts,max_commits=100_000)
    recoveries=tuple(value for value in getattr(client,'fixed_lot_recovery_contexts',())
                     if value[0] in batches)
    client=_HistoricalReads(client,source,prefix,fixed_lot_contexts,recoveries,
                            issuer=_HISTORICAL_READ_ISSUER)
    cursor=load_latest_backtest_cursor(client,prefix)
    if prefix is None or type(cursor) is not dict or cursor['event_sequence']!=prefix.last_sequence or cursor['batch_id']!=prefix.last_batch_id:
        raise ValueError('Cold selected manager lacks complete committed cursor')
    def read(contract,predicate,count):
        if type(count) is not int or not 0<=count<=100_000:
            raise ValueError('Cold manager child cardinality is unbounded')
        columns=','.join(f'toString({k}) AS {k}' if 'Decimal(' in t else k for k,t in contract.columns)
        sql=assert_select_only(f'SELECT {columns} FROM arte.{contract.name} WHERE run_id={_literal(source.run_id)} AND {predicate} LIMIT {count+1} FORMAT JSONEachRow')
        values=tuple(json.loads(v) for v in client.execute(sql).splitlines() if v.strip())
        if len(values)!=count:raise ValueError('Cold selected manager exact inventory differs: '+contract.name)
        from .decimal_snapshot_readback import declared_decimal_rows
        return declared_decimal_rows(source,contract,values)
    seal=read(PARENT,f'checkpoint_sequence={prefix.last_sequence}',1)[0]
    if (seal['content_hash']!=_digest({k:v for k,v in seal.items() if k!='content_hash'})
            or seal['boundary_ms']!=cursor['boundary_ms'] or seal['session_date']!=source.session_date.isoformat()
            or seal['selected_configuration_hash']!=source.selected_configuration_hash):
        raise ValueError('Cold manager seal differs from source/cursor/head')
    own=read(ROOT,f'through_sequence={prefix.last_sequence}',seal['selected_position_count'])
    entries={v.base.intents[0]['intent_id']:v for v in fixed_lot_contexts}
    selected_rows=[];states={};inventory=[]
    for root in sorted(own,key=lambda v:(v['account_id'],v['assignment_id'],v['ticker'])):
        key=(root['account_id'],root['assignment_id'],root['ticker'])
        context=entries.get(root['intent_id'])
        if context is None:raise ValueError('Cold manager position lacks verified original entry')
        request=context.verify_source()
        predicate='snapshot_id=toUUID('+_literal(root['snapshot_id'])+')'
        rows=FixedStructuralLotSnapshotRows(root,read(LOT,predicate,root['lot_count']),read(RESISTANCE,predicate,root['resistance_count']))
        state=restore_fixed_structural_lot_snapshot(rows,entry=request.entry,client=client,prefix=prefix,
            intervals=source.intervals,intent=request.intent,strategy_identity=(request.strategy_id,request.revision),
            entry_request=request,fixed_lot_contexts=fixed_lot_contexts)
        if key in states:raise ValueError('Cold selected manager duplicates position')
        states[key]=state;selected_rows.append((key,rows))
        inventory.append([*key,root['snapshot_id'],root['content_hash']])
    if _digest(inventory)!=seal['selected_position_hash']:
        raise ValueError('Cold selected manager inventory hash differs')
    predicate='snapshot_id=toUUID('+_literal(seal['snapshot_id'])+')'
    inherited_seal={k:v for k,v in seal.items() if k not in {'selected_configuration_hash','selected_position_count','selected_position_hash','content_hash'}}
    if seal['source_count']==0 and seal['first_held_count']==0:
        inherited_seal.pop('first_held_count');inherited_seal.pop('first_held_hash')
    inherited_seal['content_hash']=_digest(inherited_seal)
    protection=_load_protection_snapshot_rows(client,run_id=source.run_id,checkpoint_sequence=prefix.last_sequence,
        _selected_positions={(key[0],key[2],key[1]):state.protection for key,state in states.items()})
    inherited_rows=ManagerSnapshotRows(inherited_seal,read(SOURCE,predicate,seal['source_count']),
        read(BREAK,predicate,seal['pending_break_count']),protection,read(HIGH,predicate,seal['position_high_count']),
        read(CLOSED,predicate,seal['closed_position_count']),read(FIRST_HELD,predicate,seal['first_held_count']))
    inherited=_restore_manager_snapshot_scalar(inherited_rows,_selected_positions={
        (key[0],key[2],key[1]):state.protection for key,state in states.items()})
    from dataclasses import replace
    submitted=[]
    for key,proposal in inherited.submitted:
        matches=[v.verify_source().entry.proposal for v in fixed_lot_contexts
            if (v.unit.packet.root['account_id'],v.unit.packet.root['assignment_id'],v.unit.packet.root['ticker'])==key
            and v.unit.packet.root['boundary_ms']==proposal.boundary_ms]
        if len(matches)!=1:
            raise ValueError('Cold manager lacks complete original submitted entry source')
        submitted.append((key,matches[0]))
    inherited=replace(inherited,submitted=tuple(submitted))
    from .strategy_one_management_snapshot import _project_manager_snapshot_scalar
    if _project_manager_snapshot_scalar(run_id=source.run_id,session_date=source.session_date,
            checkpoint_sequence=prefix.last_sequence,state=inherited,_protection_rows=protection)!=inherited_rows:
        raise ValueError('Cold original entry source differs from inherited manager rows')
    groups=load_latest_committed_oms_groups(client,prefix,allowed_accounts=frozenset(_verify_run_identity(client,source.run_id)['account_ids']),
        strategy_identity=(source._strategy_id,source._revision),require_tactic=True,fixed_lot_contexts=fixed_lot_contexts)
    groups=checkpoint_acquisitions(client,prefix,source=source,contexts=fixed_lot_contexts,
        allowed_accounts=frozenset(_verify_run_identity(client,source.run_id)['account_ids']))
    active=set()
    load_fixed_structural_lot_stop_ceiling=checkpoint_roster
    for group in groups:
        context=entries.get(group.group['strategy_intent_id'])
        if context is None:raise ValueError('Cold manager has unexpected OMS source inventory')
        request=context.verify_source();p=request.entry.proposal
        roster=load_fixed_structural_lot_stop_ceiling(client,prefix,entry=request.entry,intervals=source.intervals,
            intent=request.intent,group_id=group.group['group_id'],strategy_identity=(request.strategy_id,request.revision),
            entry_request=request,fixed_lot_contexts=fixed_lot_contexts)
        if checkpoint_acquisition_active(client,prefix,entry_request=request,
                contexts=fixed_lot_contexts,roster=roster):
            key=(p.account_id,p.assignment_id,p.ticker)
            if key in active or key not in states or states[key].roster!=roster:
                raise ValueError('Cold manager complete active OMS roster differs')
            active.add(key)
    if active!=set(states):raise ValueError('Cold manager omits active/acquiring OMS lot')
    from datetime import datetime,timedelta,timezone
    from zoneinfo import ZoneInfo
    instant=(datetime.combine(source.session_date,datetime.min.time(),ZoneInfo('America/New_York'))
        +timedelta(hours=4,milliseconds=seal['boundary_ms'])).astimezone(timezone.utc)
    financial=[]
    for account in _verify_run_identity(client,source.run_id)['account_ids']:
        loaded=load_portfolio_snapshot(client,run_id=source.run_id,account_id=account,state_revision=prefix.last_sequence)
        if loaded is None or datetime.fromisoformat(loaded['snapshot_at'])!=instant:
            raise ValueError('Cold manager lacks same-cursor Portfolio root/time')
        financial.append(('portfolio',account,loaded['state_hash'],loaded['snapshot_at']))
    for reader_type,loader,root_field in ((ManagedBrokerMatchHeadReader,load_unattested_broker_match_snapshot,'snapshot'),
            (ManagedOmsObservationHeadReader,load_unattested_oms_observation_snapshot,'root')):
        rows=loader(client,run_id=source.run_id,checkpoint_sequence=prefix.last_sequence);root=getattr(rows,root_field)
        if (root['run_id']!=source.run_id or root['checkpoint_sequence']!=sequence
                or root['boundary_ms']!=seal['boundary_ms'] or root['session_date']!=source.session_date.isoformat()):
            raise ValueError('Cold manager financial root/head changed or has foreign cursor')
        if root_field=='snapshot':
            held={(v['account_id'],v['ticker']):float64_from_bits(v['quantity_f64_bits'],'quantity') for v in rows.positions if float64_from_bits(v['quantity_f64_bits'],'quantity')!=0}
            expected={(key[0],key[2]):float(sum(q for _,q in state.roster.remaining)) for key,state in states.items() if any(q>0 for _,q in state.roster.remaining)}
            quantities={key:checkpoint_aggregate_quantity(client,prefix,
                entry_request=entries[state.roster.intent_id].verify_source(),
                contexts=fixed_lot_contexts,roster=state.roster) for key,state in states.items()}
            expected={(key[0],key[2]):float(quantity) for key,quantity in quantities.items() if quantity}
            if len(held)!=sum(float64_from_bits(v['quantity_f64_bits'],'quantity')!=0 for v in rows.positions) or held!=expected:
                raise ValueError('Cold manager broker inventory differs from complete lots')
        financial.append((root_field,root['content_hash']))
    result=HistoricalSelectedCheckpointImage(source,source.run_id,prefix.last_sequence,prefix.last_batch_id,inherited,tuple(selected_rows),tuple(financial),prefix,fixed_lot_contexts)
    from src.backend.backtest_fixed_structural_lot_management import _small_tree
    _HISTORICAL_IMAGES[result]=(source,result.run_id,result.sequence,result.batch_id,
        _canonical(_small_tree((result.inherited,result.selected_positions,result.financial_roots))),prefix,fixed_lot_contexts)
    return require_historical_checkpoint(result,source=source)


def observe_owner_stage(name):
    """Inclusive monotonic diagnostics only; never an execution authority."""
    from functools import wraps
    from inspect import iscoroutinefunction
    from time import perf_counter
    def decorate(method):
        def record(owner,started):
            elapsed=max(0.,perf_counter()-started)
            stages=getattr(owner,'_checkpoint_product_timings',None)
            if stages is None:
                stages={};owner._checkpoint_product_timings=stages
            row=stages.setdefault(name,dict(calls=0,wall_seconds=0.,max_call_seconds=0.))
            row['calls']+=1;row['wall_seconds']+=elapsed
            row['max_call_seconds']=max(row['max_call_seconds'],elapsed)
        if iscoroutinefunction(method):
            @wraps(method)
            async def asynchronous(owner,*args,**kwargs):
                if not selected(owner.operation.source):return await method(owner,*args,**kwargs)
                started=perf_counter()
                try:return await method(owner,*args,**kwargs)
                finally:record(owner,started)
            return asynchronous
        @wraps(method)
        def synchronous(owner,*args,**kwargs):
            if not selected(owner.operation.source):return method(owner,*args,**kwargs)
            started=perf_counter()
            try:return method(owner,*args,**kwargs)
            finally:record(owner,started)
        return synchronous
    return decorate


def original_link_matches(client,stored_intent,companion,witness_intent_id):
    """Bind a verified native stored parent to its issued original semantic ID."""
    source=source_for_client(client)
    if source is None:
        return str(stored_intent['intent_id'])==str(witness_intent_id)
    contexts=tuple(getattr(client,'fixed_structural_lot_contexts',()))
    matches=[]
    for context in contexts:
        if context.source is not source:
            raise ValueError('Original semantic link has foreign source context')
        request=context.verify_source()
        root=context.unit.packet.root
        if str(stored_intent['intent_id'])==request.intent.intent_id:
            context.verify_admission()
            if (str(root['intent_id'])!=request.intent.intent_id
                    or str(root['original_intent_id'])!=request.original.intent_id
                    or str(companion['intent_id'])!=request.intent.intent_id
                    or str(companion['original_intent_id'])!=request.original.intent_id
                    or str(companion['parent_record_id'])!=str(stored_intent['record_id'])
                    or str(companion['batch_id'])!=context.base.batch_id
                    or str(stored_intent['batch_id'])!=context.base.batch_id
                    or str(companion['run_id'])!=source.run_id):
                raise ValueError('Original semantic link differs from verified native parent')
            matches.append(request)
    if len(matches)!=1:
        raise ValueError('Original semantic link is missing or ambiguous')
    return str(witness_intent_id) in (matches[0].original.intent_id,matches[0].intent.intent_id)


def observe_runtime_submission(owner):
    """Inclusive monotonic runtime submit timing for the declared owner."""
    from contextlib import contextmanager
    from time import perf_counter
    @contextmanager
    def scope():
        if not selected(owner.operation.source):
            yield
            return
        started=perf_counter()
        try:yield
        finally:
            elapsed=max(0.,perf_counter()-started)
            stages=getattr(owner,'_checkpoint_product_timings',None)
            if stages is None:stages={};owner._checkpoint_product_timings=stages
            row=stages.setdefault('selected_runtime_submit',dict(calls=0,wall_seconds=0.,max_call_seconds=0.))
            row['calls']+=1;row['wall_seconds']+=elapsed
            row['max_call_seconds']=max(row['max_call_seconds'],elapsed)
    return scope()

_COLD_PRODUCT_READS=WeakKeyDictionary()

@dataclass(frozen=True,slots=True,eq=False,weakref_slot=True)
class _ColdProductReads:
    _client: object
    _walk: object
    _prefix: object
    _contexts: tuple
    _recoveries: tuple
    _profile: object
    _batched: bool

    @property
    def fixed_structural_lot_profile(self):return self._profile
    @property
    def fixed_structural_lot_contexts(self):return self._contexts
    @property
    def fixed_lot_recovery_contexts(self):return self._recoveries
    @property
    def v4_batched_detail_readback(self):return self._batched
    backtest_v4_lease=None
    typed_insert_dispatch=None

    def execute(self,sql,*args,**kwargs):
        from .fixed_structural_lot_cold_recovery import _walk_prefix
        binding=_COLD_PRODUCT_READS.get(self)
        if (binding is None or binding!=(self._client,self._walk,self._prefix,self._contexts,
                self._recoveries,self._profile,self._batched,_read_prefix_identity(self._prefix))
                or _walk_prefix(self._walk) is not self._prefix
                or self._walk.client is not self._client or self._walk.entries is not self._contexts
                or getattr(self._client,'fixed_structural_lot_profile',None) is not self._profile
                or self._profile.operation.source is not self._walk.source):
            raise ValueError('Cold product reader has foreign or mutated verified scope')
        from .fixed_structural_lot_profile import require_fixed_structural_lot_profile
        if require_fixed_structural_lot_profile(self._profile).operation.source is not self._walk.source:
            raise ValueError('Cold product reader has foreign issued profile')
        self._walk.source.require_installed_admission()
        for context in self._contexts:
            if context.source is not self._walk.source:
                raise ValueError('Cold product reader has foreign entry source')
            context.verify_admission()
        _read_recovery_inventory(self,self._walk.source,self._prefix)
        if type(sql) is not str or not sql.startswith('SELECT ') or args or kwargs:
            raise ValueError('Cold product reader requires exact SELECT without arguments')
        return self._client.execute(sql)


def cold_checkpoint_product_reader(client,*,walk,verified_prefix,recovery_contexts=()):
    """Carry already-issued cold contexts into nested product verification.

    This private transport cannot issue a checkpoint, prefix, resume or dispatch.
    The enclosing walk must independently finish every committed graph seal.
    """
    if walk is None:return client
    from .fixed_structural_lot_cold_recovery import _walk_prefix
    if _walk_prefix(walk) is not verified_prefix or walk.client is not client:
        raise ValueError('Cold product reader lacks its exact verified walk predecessor')
    if not selected(walk.source) or verified_prefix is None:return client
    from .fixed_structural_lot_profile import require_fixed_structural_lot_profile
    profile=require_fixed_structural_lot_profile(getattr(client,'fixed_structural_lot_profile',None))
    if profile.operation.source is not walk.source:
        raise ValueError('Cold product reader has foreign installed profile')
    if type(recovery_contexts) is not tuple:
        raise ValueError('Cold product reader requires immutable preceding recovery inventory')
    for context in walk.entries:
        if context.source is not walk.source:raise ValueError('Cold product reader has foreign entry source')
        context.verify_admission()

    reader=_ColdProductReads(client,walk,verified_prefix,walk.entries,recovery_contexts,profile,
        bool(getattr(client,'v4_batched_detail_readback',False)))
    _read_recovery_inventory(reader,walk.source,verified_prefix)
    _COLD_PRODUCT_READS[reader]=(client,walk,verified_prefix,walk.entries,recovery_contexts,profile,
        reader._batched,_read_prefix_identity(verified_prefix))
    return reader
def require_batched_product_read_scope(scope,client,filters):
    """Authorize only the SQL envelope for an exact owned committed batch."""
    if type(scope) is not _SourceOmsReadScope:
        raise ValueError('Batched product read scope is unissued')
    require_source_oms_read_scope(scope,client,scope.prefix)
    from .strategy_registry import BATCHED_DETAIL_SELECT_RULE
    rules=scope.source.installed_payload['strategy']['numbered_release']['contract']['rule_set_contracts']
    if type(rules) is not list or rules.count(BATCHED_DETAIL_SELECT_RULE)!=1:
        raise ValueError('Batched product read requires one declared envelope rule')
    import re
    from uuid import UUID
    from .arte_journal_writer import _literal
    match=re.fullmatch(r"WHERE run_id='([^']+)' AND batch_id=toUUID\('([^']+)'\) ",filters)
    if match is None:
        raise ValueError('Batched product read has foreign exact filters')
    batch=str(UUID(match[2]))
    if (match[1]!=scope.source.run_id or batch not in scope.prefix.batch_ids
            or filters!=f'WHERE run_id={_literal(scope.source.run_id)} AND batch_id=toUUID({_literal(batch)}) '):
        raise ValueError('Batched product read has foreign run or batch ancestry')
    return scope


def source_batch_read_contexts(scope,client,prefix,batch_id,first_price_source):
    require_source_oms_read_scope(scope,client,prefix)
    if (batch_id not in prefix.batch_ids or first_price_source is not scope.source.price_authority):
        raise ValueError('Nested batch read has foreign source price or ancestry')
    from .fixed_structural_lot_entry_v4 import fixed_lot_contexts_by_batch
    from src.backend.backtest_fixed_structural_lot_management import recovery_contexts_by_batch
    entries=fixed_lot_contexts_by_batch(prefix.run_id,scope.contexts,max_commits=100_000)
    recoveries=recovery_contexts_by_batch(prefix.run_id,scope.recoveries,max_commits=100_000)
    result={'fixed_lot_read_scope':scope}
    if batch_id in entries:result['fixed_lot_context']=entries[batch_id]
    if batch_id in recoveries:result['fixed_lot_recovery_context']=recoveries[batch_id]
    return result
