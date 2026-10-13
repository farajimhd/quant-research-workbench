"""Certified per-frame session state, including price-empty frame continuity."""
from uuid import uuid4,UUID
from datetime import date
import re
from .contracts import digest,literal
from .publication import Publisher,Scope,evidence_sql
from .schema import TABLES
from .indicators import STATE_FIELDS,validate_state
from pipelines.market_sip.events.market_day_sql import FRAMES


def scope_for(request,technical):
    base=request.unit
    parent=digest(dict(version='continuous-indicator-state-v1',recipe=request.dataset_hash,
        technical=technical['output_hash'],predecessor=request.prior_indicator_hash))
    return Scope(base.dataset_id,base.day,base.listing_key,'technical_state',parent)


def load(query,database,claim,scope,recipe_hash,predecessor_hash,*,source_hash=None,rules_hash=None,frames=FRAMES):
    """Verify physical output/receipt before exposing at most eight state rows."""
    if scope.product!='technical_state':raise ValueError('Indicator state scope required')
    if (source_hash is None or rules_hash is None) and not database.startswith('certified_research_test_'):
        raise ValueError('Production indicator state requires current source/rule bindings')
    from .market import selected_frames
    frames=selected_frames(frames)
    publisher=Publisher(query,database,claim);receipt=publisher.receipt(scope)
    if receipt is None:return None
    rows=query(f'SELECT * FROM {database}.research_indicator_state_v1 WHERE '+scope.where(receipt['attempt_id']))
    if (len(rows)!=len(frames) or {int(r['resolution_ms']) for r in rows}!=set(frames)
            or any(r['recipe_hash']!=recipe_hash or r['predecessor_hash']!=predecessor_hash
                or (source_hash is not None and r['source_hash']!=source_hash)
                or (rules_hash is not None and r['rules_hash']!=rules_hash) for r in rows)):
        raise ValueError('Indicator state recipe, predecessor or frame coverage differs')
    state={int(r['resolution_ms']):{n:int(r[n]) if n=='sample_count' else float(r[n]) for n in STATE_FIELDS} for r in rows}
    validate(state,frames)
    if publisher.receipt(scope)!=receipt:raise ValueError('Indicator state authority changed during load')
    return dict(receipt=receipt,state=state)


def discover(query,database,claim,listing_key,before,recipe_hash,rules_hash,source_hashes,*,dataset_id=None,frames=FRAMES):
    """Find the latest compatible receipt; never resolve competing seeds by recency of insertion.

    Caller supplies frozen current source hashes, including earlier context.
    Returned session determines replay context; this does not skip a gap.
    """
    if (type(listing_key) is not int or not 0<listing_key<2**64 or type(before) is not date
            or any(not re.fullmatch('[0-9a-f]{64}',h) for h in (recipe_hash,rules_hash))):
        raise ValueError('Invalid indicator predecessor discovery')
    if dataset_id is not None:UUID(dataset_id)
    Publisher(query,database,claim)
    where=f' AND dataset_id=toUUID({literal(dataset_id)})' if dataset_id is not None else ''
    candidates=query(f"SELECT r.dataset_id dataset_id,r.session_date session_date,r.parent_hash parent_hash,"
        f"s.predecessor_hash predecessor_hash FROM {database}.research_unit_receipt_v1 r INNER JOIN "
        f"(SELECT dataset_id,session_date,listing_key,attempt_id,min(recipe_hash) recipe_hash,min(rules_hash) rules_hash,"
        f"min(predecessor_hash) predecessor_hash FROM {database}.research_indicator_state_v1 WHERE listing_key={listing_key} "
        f"AND session_date<toDate({literal(before)}){where} GROUP BY dataset_id,session_date,listing_key,attempt_id) s "
        "USING (dataset_id,session_date,listing_key,attempt_id) WHERE r.product='technical_state' "
        f"AND s.recipe_hash={literal(recipe_hash)} AND s.rules_hash={literal(rules_hash)} "
        "ORDER BY r.session_date DESC LIMIT 2")
    if not candidates:return None
    if len(candidates)>1 and candidates[0]['session_date']==candidates[1]['session_date']:
        raise ValueError('Competing certified indicator predecessors; select an explicit dataset')
    row=candidates[0];day=date.fromisoformat(str(row['session_date']))
    source_hash=source_hashes.get(day,source_hashes.get(str(day)))
    if source_hash is None:raise ValueError('Discovered indicator seed requires frozen source context')
    scope=Scope(str(row['dataset_id']),day,listing_key,'technical_state',row['parent_hash'])
    loaded=load(query,database,claim,scope,recipe_hash,row['predecessor_hash'],source_hash=source_hash,rules_hash=rules_hash,frames=frames)
    if loaded is None:raise ValueError('Discovered indicator receipt disappeared')
    dependency=dict(loaded['receipt'],state_receipt=loaded['receipt'],state_context=dict(
        recipe_hash=recipe_hash,source_hash=source_hash,predecessor_hash=row['predecessor_hash'],rules_hash=rules_hash))
    return dict(scope=scope,session=day,dependency=dependency,**loaded)


def validate(state,frames=FRAMES):
    from .market import selected_frames
    frames=selected_frames(frames)
    if set(state)!=set(frames):raise ValueError('Indicator state needs every declared frame')
    validate_state(state,frames)


def combined(technical,state_receipt,request):
    return dict(technical,output_hash=digest(dict(technical=technical['output_hash'],state=state_receipt['output_hash'])),
        technical_receipt=technical,state_receipt=state_receipt,
        state_context=dict(recipe_hash=request.dataset_hash,source_hash=request.source_hash,
            predecessor_hash=request.prior_indicator_hash,rules_hash=digest(request.rules)))


def authority(request,bars,query,database,claim):
    base=request.unit;publisher=Publisher(query,database,claim)
    technical=publisher.receipt(Scope(base.dataset_id,base.day,base.listing_key,'technical',
        request.parent('technical',(bars['output_hash'],))))
    if technical is None:return None
    loaded=load(query,database,claim,scope_for(request,technical),request.dataset_hash,request.prior_indicator_hash,
        source_hash=request.source_hash,rules_hash=digest(request.rules),frames=request.frames)
    return combined(technical,loaded['receipt'],request) if loaded else None


def publish(request,bars,technical,query,database,claim,source_guard):
    from .producer import indicator_state
    scope=scope_for(request,technical);publisher=Publisher(query,database,claim)
    base=request.unit
    bar_scope=Scope(base.dataset_id,base.day,base.listing_key,'bars',request.parent('bars'))
    technical_scope=Scope(base.dataset_id,base.day,base.listing_key,'technical',request.parent('technical',(bars['output_hash'],)))
    if publisher.receipt(bar_scope)!=bars or publisher.receipt(technical_scope)!=technical:
        raise ValueError('Indicator state requires its exact certified bar/technical parents')
    loaded=load(query,database,claim,scope,request.dataset_hash,request.prior_indicator_hash,
        source_hash=request.source_hash,rules_hash=digest(request.rules),frames=request.frames)
    if loaded:return combined(technical,loaded['receipt'],request)
    state=indicator_state(query,database,request.unit,technical['attempt_id'],request.prior_indicators,
        bar_attempt=bars['attempt_id'],frames=request.frames)
    if not state:state={f:{n:0 if n=='sample_count' else 0. for n in STATE_FIELDS} for f in request.frames}
    validate(state,request.frames);attempt=str(uuid4());table=next(t for t in TABLES if t.name=='research_indicator_state_v1')
    selects=[]
    for frame in request.frames:
        values=dict(dataset_id=f'toUUID({literal(scope.dataset_id)})',session_date=f'toDate({literal(scope.session_date)})',
            listing_key=f'toUInt64({scope.listing_key})',attempt_id=f'toUUID({literal(attempt)})',
            resolution_ms=f'toUInt32({frame})',recipe_hash=literal(request.dataset_hash),
            predecessor_hash=literal(request.prior_indicator_hash),source_hash=literal(request.source_hash),rules_hash=literal(digest(request.rules)))
        for name,value in state[frame].items():
            values[name]=f'toUInt64({value})' if name=='sample_count' else f'toFloat64({value!r})'
        selects.append('SELECT '+','.join(values[c.name]+' AS '+c.name for c in table.columns))
    projection=' UNION ALL '.join(selects)
    expected={k:int(v) for k,v in query(evidence_sql(table,projection,'tuple(resolution_ms)'))[0].items()}
    claim.check();source_guard()
    query(f'INSERT INTO {database}.research_indicator_state_v1 '+projection,read=False)
    claim.check();source_guard()
    if publisher.receipt(bar_scope)!=bars or publisher.receipt(technical_scope)!=technical:
        raise ValueError('Indicator state parents changed during publication')
    receipt=publisher.publish(scope,attempt,expected=expected,empty_proof=scope.parent_hash)
    load(query,database,claim,scope,request.dataset_hash,request.prior_indicator_hash,
        source_hash=request.source_hash,rules_hash=digest(request.rules),frames=request.frames)
    return combined(technical,receipt,request)
