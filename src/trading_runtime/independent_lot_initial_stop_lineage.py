"""Declared initial acknowledged stop lineage for pending independent repairs."""
from math import isfinite
RULE = 'independent-lot-pending-initial-stop-lineage@1'


def selected_source(source, *, run_id, strategy_id, strategy_revision):
    if source is None:
        return False
    from src.backend.backtest_fixed_structural_lot_source import require_native_fixed_structural_lot_source
    require_native_fixed_structural_lot_source(source)
    payload = source.installed_payload
    if payload is None:
        return False
    rules = payload['strategy']['numbered_release']['contract']['rule_set_contracts']
    if RULE not in rules:
        return False
    if rules.count(RULE) != 1:
        raise ValueError('Initial stop lineage requires one exact declared rule')
    source.require_installed_admission()
    if (source.run_id,source._strategy_id,source._revision)!=(run_id,strategy_id,strategy_revision):
        raise ValueError('Initial stop lineage has foreign installed source')
    return True


def initial_metadata(group,order,metadata,proofs,*,source,run_id,strategy_id,strategy_revision,sequence,boundary):
    if not selected_source(source,run_id=run_id,strategy_id=strategy_id,strategy_revision=strategy_revision):
        return None
    if order.side!='SELL' or order.parentId or order.orderType not in {'LMT','STP','STOP_LIMIT'}:
        return None
    indexes=[i for i,v in enumerate(group.orders) if v.cOID==order.cOID]
    if len(indexes)!=1:
        raise ValueError('Initial stop lineage request identity is ambiguous')
    index=indexes[0];stop_index=index if order.orderType in {'STP','STOP_LIMIT'} else index+1
    slices=group.plan.order_slice_ids if hasattr(group,'plan') else group.order_slice_ids
    if stop_index>=len(group.orders) or slices[index]!=slices[stop_index]:
        raise ValueError('Initial stop lineage has foreign pair ownership')
    stop=group.orders[stop_index];lot=slices[stop_index]
    profile=group.intent.resolved_protection_profile()
    rules=[v for v in profile.slices if v.slice_id==lot] if profile else []
    if len(rules)!=1 or stop.orderType not in {'STP','STOP_LIMIT'}:
        raise ValueError('Initial stop lineage has foreign slice')
    initial=rules[0].stop.price
    if type(sequence) is not int or sequence<=0 or boundary is None:
        raise ValueError('Initial stop lineage lacks its causal snapshot fence')
    if any(v.acctId!=group.account_id or v.ticker!=group.intent.ticker for v in group.orders):
        raise ValueError('Initial stop lineage group account/ticker differs')
    bindings=group.broker_order_request_indexes
    unique={}
    for record in proofs.values():
        previous=unique.setdefault(record.sequence,record)
        if previous!=record:
            raise ValueError('Initial stop lineage source sequence is ambiguous')
    valid=[];amended=[]
    for record in unique.values():
        payload=record.payload
        matching=[i for i,v in enumerate(group.orders) if v.cOID==payload.get('client_order_id')]
        if len(matching)!=1 or slices[matching[0]]!=lot or payload.get('kind')!='stop' or payload.get('phase')!='effective':
            continue
        action=payload.get('action')
        if action not in {None,'enter_long','replace_protective_stop'}:
            continue
        price=payload.get('price');broker_id=payload.get('order_id');owned=group.orders[matching[0]]
        if (record.run_id!=run_id or record.account_id!=group.account_id
                or record.category!='protection' or record.entity_type!='protection_change'
                or not 0<record.sequence<sequence or record.event_time>boundary
                or payload.get('order_group_id')!=group.group_id
                or payload.get('source_intent_id')!=group.intent.intent_id
                or payload.get('strategy_id')!=strategy_id or payload.get('strategy_revision')!=strategy_revision
                or payload.get('ticker')!=group.intent.ticker
                or bindings.get(broker_id)!=matching[0] or owned.side!='SELL'
                or owned.orderType not in {'STP','STOP_LIMIT'}
                or type(price) not in {int,float} or not isfinite(price) or price<=0
                or not payload.get('intent_id')):
            raise ValueError('Initial stop lineage has foreign/future acknowledged source')
        if action=='replace_protective_stop':
            amended.append(record)
        else:
            # Effective ACK authorizes its price even while the original
            # bracket is inactive. It does not authorize active coverage.
            valid.append(record)
    # A higher earned stop remains the exact existing amendment path. An
    # initial source may never lower it or introduce a support-amendment marker.
    if amended or group.intent.metadata.get('confirmed_support_stop') is not None:
        return None
    if type(initial) not in {int,float} or not isfinite(initial) or stop.auxPrice!=initial:
        raise ValueError('Initial stop lineage requested price differs')
    if order.orderType=='LMT' and order.price!=rules[0].profit_target_price:
        raise ValueError('Initial stop lineage target price differs')
    own=[v for v in valid if v.payload.get('client_order_id')==stop.cOID]
    if stop_index in set(bindings.values()):
        if not own:
            raise ValueError('Initial stop lineage registered repair lacks its actual creation ACK')
        candidates=own
    else:
        candidates=[v for v in valid if group.orders[bindings[v.payload['order_id']]].parentId]
        if not candidates:
            raise ValueError('Initial stop lineage pending repair lacks its original ACK')
    identities={}
    for record in candidates:
        key=record.payload['client_order_id'];value=(record.payload['order_id'],record.payload['price'])
        if identities.setdefault(key,value)!=value:
            raise ValueError('Initial stop lineage original ACK is ambiguous')
    if any(v.payload['price']!=initial for v in candidates):
        raise ValueError('Initial stop lineage source price differs')
    return metadata
