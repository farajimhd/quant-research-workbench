"""Typed, boundary-scoped V7 exposure in ClickHouse; no level disk persistence.

Full PM/REGULAR/AH engine state remains in research_v7_checkpoint_v1. These
additional tables expose geometry and observations without decoding that blob.
Publication requires independently restored, bar-bound certified checkpoints.
"""
from datetime import date
from math import isfinite

from .contracts import digest, identifier, literal, TRADE_POLICY, REPORTING_REVISION
from .publication import Publisher
from .checkpoints import restore

VERSION = 'strict-normalized-v7-boundaries-v1'
COMMON = (('dataset_id','UUID'),('session_date','Date'),('listing_key','UInt64'),
          ('attempt_id','UUID'),('boundary','UInt8'),('state_hash','FixedString(64)'))
LAYOUTS = {
 'research_v7_level_v1': COMMON + (
    ('row_index','UInt32'),('level_id','String'),('parent_level_id','String'),
    ('origin_session','Date'),('qualified','UInt8'),('historical','UInt8'),
    ('role','String'),('transition_from','String'),('price','Float64'),
    ('lower','Float64'),('upper','Float64'),('association_radius','Float64'),
    ('fit_status','String'),('fit_distribution','String'),('fit_count','UInt32'),
    ('fit_center','Nullable(Float64)'),('fit_scale','Nullable(Float64)'),
    ('fit_lower','Nullable(Float64)'),('fit_upper','Nullable(Float64)'),
    ('fit_resolution','Nullable(Float64)'),('fit_coverage','Nullable(Float64)'),
    ('fit_degrees_of_freedom','Nullable(Float64)'),('fit_scale_at_floor','UInt8')),
 'research_v7_observation_v1': COMMON + (
    ('row_index','UInt32'),('level_id','String'),('observation_id','FixedString(64)'),
    ('price','Float64'),('resolution','Float64'),('at_us','UInt64'),
    ('resolved_at_us','UInt64'),('role','String'),('origin_session','Date')),
 'research_v7_normalized_receipt_v1': COMMON + (
    ('version','String'),('checkpoint_output_hash','FixedString(64)'),
    ('checkpoint_parent_hash','FixedString(64)'),('recipe_hash','FixedString(64)'),
    ('predecessor_hash','FixedString(64)'),('input_prefix_hash','FixedString(64)'),
    ('bars_dataset_id','UUID'),('bars_attempt_id','UUID'),
    ('bars_parent_hash','FixedString(64)'),('bars_output_hash','FixedString(64)'),
    ('boundary_us','UInt64'),('trade_policy','String'),('reporting_revision','String'),
    ('level_rows','UInt32'),('level_hash','FixedString(64)'),
    ('observation_rows','UInt32'),('observation_hash','FixedString(64)')),
}


def preflight(query, database, claim, *, create=False):
    """Explicit additive schema, SSD-only policy and actual-part verification."""
    identifier(database); claim.check()
    policy=query("SELECT disks FROM system.storage_policies WHERE policy_name='live_market_ssd'")
    if not policy or any(r['disks'] != ['live_market_ssd'] for r in policy):
        raise ValueError('Normalized V7 requires SSD-only live_market_ssd')
    names=','.join(literal(n) for n in LAYOUTS)
    existing=query(f'SELECT name,storage_policy FROM system.tables WHERE database={literal(database)} AND name IN ({names})')
    if any(r['storage_policy']!='live_market_ssd' for r in existing):
        raise ValueError('Normalized V7 table storage policy differs')
    misplaced=query(f'SELECT table,disk_name FROM system.parts WHERE active AND database={literal(database)} '
        f"AND table IN ({names}) AND disk_name!='live_market_ssd' LIMIT 1")
    if misplaced: raise ValueError('Normalized V7 parts are outside required SSD')
    if create:
        for name,columns in LAYOUTS.items():
            claim.check()
            key='dataset_id,session_date,listing_key,attempt_id,boundary,state_hash'
            if name!='research_v7_normalized_receipt_v1': key+=',row_index'
            query(f'CREATE TABLE IF NOT EXISTS {database}.{name} ('+
                ','.join(f'{n} {t}' for n,t in columns)+') ENGINE=MergeTree '
                f'PARTITION BY toYYYYMM(session_date) ORDER BY ({key}) '
                "SETTINGS storage_policy='live_market_ssd'",read=False)
        return preflight(query,database,claim)
    if {r['name'] for r in existing}!=set(LAYOUTS):
        raise ValueError('Normalized V7 schema is not installed')
    for name,columns in LAYOUTS.items():
        actual=query(f'SELECT name,type FROM system.columns WHERE database={literal(database)} '
                     f'AND table={literal(name)} ORDER BY position')
        if tuple((r['name'],r['type']) for r in actual)!=columns:
            raise ValueError('Normalized V7 schema differs: '+name)
    claim.check()


def _number(value):
    value=float(value)
    if not isfinite(value): raise ValueError('Nonfinite V7 geometry/observation')
    return value


def project(engine, common, boundary_us, *, maximum_levels=10000, maximum_observations=1000000):
    """Normalize the complete restored state, including unqualified candidates."""
    if len(engine.rows)>maximum_levels: raise ValueError('V7 level projection exceeds explicit bound')
    levels=[]; observations=[]; ids=set()
    for row in engine.rows:
        identity=str(row['id'])
        if not identity or identity in ids: raise ValueError('Duplicate/missing V7 level identity')
        ids.add(identity); fit=row.get('fit') or {}
        geometry={k:_number(row[k]) for k in ('price','lower','upper','association_radius')}
        if not 0<geometry['lower']<=geometry['price']<=geometry['upper'] or geometry['association_radius']<0:
            raise ValueError('Invalid normalized V7 geometry')
        level=dict(common,row_index=len(levels),level_id=identity,parent_level_id=str(row.get('parent_id') or ''),
            origin_session=str(date.fromisoformat(row['origin_session'])),qualified=int(bool(row['qualified'])),
            historical=int(bool(row['historical'])),role=str(row['role']),
            transition_from=str(row.get('transition_from') or ''),**geometry,
            fit_status=str(fit.get('status') or 'unknown'),fit_distribution=str(fit.get('distribution') or ''),
            fit_count=int(fit.get('count') or len(row['observations'])),
            fit_scale_at_floor=int(bool(fit.get('scale_at_floor'))))
        for key in ('center','scale','lower','upper','resolution','coverage','degrees_of_freedom'):
            level['fit_'+key]=None if fit.get(key) is None else _number(fit[key])
        levels.append(level)
        for ordinal,observation in enumerate(row['observations']):
            stamp=round(_number(observation['at'])*1000000)
            resolved=round(_number(observation['resolved_at'])*1000000)
            price,resolution=_number(observation['price']),_number(observation['resolution'])
            if not 0<=stamp<=resolved<=boundary_us or price<=0 or resolution<=0:
                raise ValueError('Future or invalid normalized V7 observation')
            observations.append(dict(common,row_index=len(observations),level_id=identity,
                observation_id=digest(dict(level=identity,ordinal=ordinal,observation=observation)),
                price=price,resolution=resolution,at_us=stamp,resolved_at_us=resolved,
                role=str(observation['role']),origin_session=str(date.fromisoformat(observation['session']))))
            if len(observations)>maximum_observations:
                raise ValueError('V7 observation projection exceeds explicit bound')
    return levels,observations


def _coerce(actual, expected):
    return {k:(actual[k] if value is None else type(value)(actual[k])) for k,value in expected.items()}


def _materialize(query,database,claim,name,where,rows,*,allow_insert=True):
    """Reconcile bounded chunks, insert only absent keys, then compare all fields."""
    import json
    count=int(query(f'SELECT count() n FROM {database}.{name} WHERE {where}')[0]['n'])
    if count>len(rows): raise ValueError('Normalized V7 contains unexpected/duplicate rows')
    for offset in range(0,len(rows),256):
        packet=rows[offset:offset+256]
        bounds=f' AND row_index>={offset} AND row_index<{offset+len(packet)}'
        present=query(f'SELECT * FROM {database}.{name} WHERE {where}{bounds} ORDER BY row_index')
        by_index={int(r['row_index']):r for r in present}
        if len(by_index)!=len(present): raise ValueError('Duplicate normalized V7 row keys')
        missing=[]
        for expected in packet:
            old=by_index.get(expected['row_index'])
            if old is None: missing.append(expected)
            elif _coerce(old,expected)!=expected: raise ValueError('Existing normalized V7 row differs')
        if missing:
            if not allow_insert:
                raise ValueError('Published normalized V7 rows are missing; authority changed')
            claim.check()
            query(f'INSERT INTO {database}.{name} FORMAT JSONEachRow\n'+
                '\n'.join(json.dumps(r,separators=(',',':'),allow_nan=False) for r in missing),read=False)
        actual=query(f'SELECT * FROM {database}.{name} WHERE {where}{bounds} ORDER BY row_index')
        if len(actual)!=len(packet) or any(_coerce(a,e)!=e for a,e in zip(actual,packet)):
            raise ValueError('Normalized V7 readback differs from restored checkpoint')
    if int(query(f'SELECT count() n FROM {database}.{name} WHERE {where}')[0]['n'])!=len(rows):
        raise ValueError('Normalized V7 final row count differs')
    claim.check()


def publish_from_bars(query,database,claim,scope,bar_scope,recipe_hash,predecessor_hash,ticker,boundary,*,regular_close=None):
    """Publish only a checkpoint verified against its independently frozen bars."""
    from .v7 import load_from_bars
    preflight(query,database,claim)
    receipt=Publisher(query,database,claim).receipt(scope)
    if receipt is None: raise ValueError('Normalized V7 requires a certified checkpoint receipt')
    bars=Publisher(query,database,claim).receipt(bar_scope)
    if bars is None: raise ValueError('Normalized V7 requires certified bar provenance')
    checkpoint,engine=load_from_bars(query,database,claim,scope,bar_scope,recipe_hash,
        predecessor_hash,ticker,boundary,regular_close=regular_close,
        allow_foreign_bars=bar_scope.dataset_id!=scope.dataset_id)
    # Revalidate payload binding immediately before deriving the typed exposure.
    engine=restore(checkpoint['payload'],checkpoint['state_hash'],checkpoint['binding'],
                   expected_boundary_us=checkpoint['boundary_us'])
    common=dict(dataset_id=scope.dataset_id,session_date=str(scope.session_date),listing_key=scope.listing_key,
        attempt_id=str(receipt['attempt_id']),boundary={'PM':1,'REGULAR':2,'AH':3}[boundary],state_hash=checkpoint['state_hash'])
    where=scope.where(receipt['attempt_id'])+f" AND boundary={common['boundary']} AND state_hash={literal(common['state_hash'])}"
    levels,observations=project(engine,common,checkpoint['boundary_us'])
    result=dict(common,version=VERSION,checkpoint_output_hash=receipt['output_hash'],
        checkpoint_parent_hash=scope.parent_hash,recipe_hash=recipe_hash,
        predecessor_hash=predecessor_hash,input_prefix_hash=checkpoint['binding'].input_prefix_hash,
        bars_dataset_id=bar_scope.dataset_id,bars_attempt_id=str(bars['attempt_id']),
        bars_parent_hash=bar_scope.parent_hash,bars_output_hash=bars['output_hash'],
        boundary_us=checkpoint['boundary_us'],trade_policy=TRADE_POLICY,reporting_revision=REPORTING_REVISION,
        level_rows=len(levels),level_hash=digest(levels),observation_rows=len(observations),observation_hash=digest(observations))
    old=query(f'SELECT * FROM {database}.research_v7_normalized_receipt_v1 WHERE {where}')
    if len(old)>1 or old and _coerce(old[0],result)!=result: raise ValueError('Normalized V7 receipt differs')
    _materialize(query,database,claim,'research_v7_level_v1',where,levels,allow_insert=not old)
    _materialize(query,database,claim,'research_v7_observation_v1',where,observations,allow_insert=not old)
    if Publisher(query,database,claim).receipt(scope)!=receipt: raise ValueError('V7 checkpoint changed during normalization')
    if Publisher(query,database,claim).receipt(bar_scope)!=bars: raise ValueError('V7 source bars changed during normalization')
    if not old:
        import json
        claim.check()
        query(f'INSERT INTO {database}.research_v7_normalized_receipt_v1 FORMAT JSONEachRow\n'+json.dumps(result),read=False)
    saved=query(f'SELECT * FROM {database}.research_v7_normalized_receipt_v1 WHERE {where}')
    if len(saved)!=1 or _coerce(saved[0],result)!=result: raise ValueError('Normalized V7 receipt readback failed')
    claim.check();return result


def rebuild_requirement(listing_key, ticker, before, *, verified_ah=None, first_valid_session=None,
                        origin_evidence_hash=None):
    """Metadata-only queue decision; missing seeds never imply an empty reset.

    verified_ah is an output of v7.discover/verify_seed, never a historical
    JSON book. Later IPO origins require separately certified first validity.
    """
    if type(listing_key) is not int or listing_key<=0 or type(before) is not date or not ticker:
        raise ValueError('Invalid V7 rebuild requirement')
    if verified_ah is not None:
        locator,checkpoint=verified_ah
        if locator['product']!='v7' or int(locator['listing_key'])!=listing_key or checkpoint['boundary']!='AH':
            raise ValueError('Wrong verified AH queue seed')
        if date.fromisoformat(str(locator['session_date']))>=before:
            raise ValueError('AH queue seed does not precede target')
        return dict(listing_key=listing_key,ticker=ticker,status='ready_from_verified_ah',
                    before=str(before),predecessor_hash=checkpoint['state_hash'],seed=locator)
    origin=date(2025,1,1)
    if first_valid_session is not None:
        import re
        if (type(first_valid_session) is not date or first_valid_session>=before
                or not isinstance(origin_evidence_hash,str) or not re.fullmatch('[0-9a-f]{64}',origin_evidence_hash)):
            raise ValueError('Invalid certified listing origin')
        origin=max(origin,first_valid_session)
    return dict(listing_key=listing_key,ticker=ticker,status='blocked_required_context_bars',
        before=str(before),required_start=str(origin),required_end_exclusive=str(before),
        origin_evidence_hash=origin_evidence_hash,
        reason='No compatible strict AH. Certify canonical bars and dated identity from origin, then build AH in ClickHouse; no legacy seed or implicit reset.')


def plan_queue(query,database,claim,items,recipe_hash,rules,*,maximum_items=256,maximum_candidates=128):
    """Bounded metadata queue; only v7.discover may admit a reusable AH.

    Each item's day_contexts must come from frozen certified source plans. No
    context is synthesized from old book filenames or current ticker identity.
    Returned documents contain locators/requirements, never level geometry or
    checkpoint payloads, and may therefore be retained as job metadata.
    """
    from .v7 import discover
    if not 0<len(items)<=maximum_items or maximum_items>4096:
        raise ValueError('V7 queue packet exceeds explicit bound')
    result=[];seen=set()
    for item in items:
        listing=int(item['listing_key']);before=date.fromisoformat(str(item['before']))
        identity=(listing,before)
        if identity in seen: raise ValueError('Duplicate V7 queue unit')
        seen.add(identity);claim.check()
        contexts=item.get('day_contexts',{})
        found=discover(query,database,claim,listing,before,recipe_hash,rules,contexts,
            dataset_id=item.get('seed_dataset_id'),maximum_candidates=maximum_candidates) if contexts else None
        verified=(found['dependency'],found['checkpoint']) if found else None
        requirement=rebuild_requirement(listing,item['ticker'],before,verified_ah=verified,
            first_valid_session=date.fromisoformat(item['first_valid_session']) if item.get('first_valid_session') else None,
            origin_evidence_hash=item.get('origin_evidence_hash'))
        requirement['frozen_context_hash']=digest(contexts)
        requirement['inspected_candidates']=found['inspected_candidates'] if found else 0
        result.append(requirement)
    return dict(version=VERSION,recipe_hash=recipe_hash,items=result,
        ready=sum(r['status']=='ready_from_verified_ah' for r in result),
        blocked=sum(r['status']=='blocked_required_context_bars' for r in result),
        queue_hash=digest(result))
