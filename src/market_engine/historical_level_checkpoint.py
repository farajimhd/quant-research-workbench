"""Finalized daily consolidation, independent of streaming restart checkpoints."""
from copy import deepcopy
from dataclasses import asdict,dataclass
from datetime import datetime
from hashlib import sha256
import json
from math import isfinite

from .historical_session_levels import Settings,encounter_evidence,role_timeline
from .reaction_center import annotate,update as update_center,CONFIG as CENTER_CONFIG

VERSION='historical-level-consolidation-1'


def digest(value):
    return sha256(json.dumps(value,sort_keys=True,separators=(',',':'),allow_nan=False).encode()).hexdigest()


@dataclass(frozen=True)
class Policy:
    maximum_union_width_multiple: float=1.5
    maximum_center_distance_multiple: float=.75
    weakening_window: int=3
    minimum_accepted_crossings: int=4
    weakening_acceptance_fraction: float=.75


def contribution(zone,session,source_hash):
    return dict(session=session,source_hash=source_hash,encounters=deepcopy(zone['encounters']),
        support_rejections=zone['support_rejections'],resistance_rejections=zone['resistance_rejections'],
        accepted_crossings=zone['accepted_crossings'],profile_volume=zone['profile_volume'])


def totals(row,policy):
    evidence=row['contributions']
    for key in ('support_rejections','resistance_rejections','accepted_crossings','profile_volume'):
        row[key]=sum(e[key] for e in evidence)
    row['evidence_role']='both' if row['support_rejections'] and row['resistance_rejections'] else 'support' if row['support_rejections'] else 'resistance'
    row['encounters']=[event for e in evidence for event in e['encounters']]
    touched=[e for e in evidence if e['encounters']][-policy.weakening_window:]
    wins=sum(e['support_rejections']+e['resistance_rejections'] for e in touched)
    losses=sum(e['accepted_crossings'] for e in touched)
    row['strength_status']='weakened' if losses>=policy.minimum_accepted_crossings and losses/max(1,wins+losses)>=policy.weakening_acceptance_fraction else 'qualified'
    row['strength_evidence']=dict(touched_sessions=[e['session'] for e in touched],rejections=wins,
        accepted_crossings=losses,acceptance_fraction=losses/max(1,wins+losses))


def seed(extraction,policy=Policy(),*,reaction_inputs=None):
    if reaction_inputs is not None and digest(dict(bars=reaction_inputs[0],profile=reaction_inputs[1]))!=extraction['input_sha256']:
        raise ValueError('Reaction seed input hash mismatch')
    rows=[]
    for level in extraction['levels']:
        row=deepcopy(level)
        if reaction_inputs is not None:
            row['encounters']=annotate(row['encounters'],reaction_inputs[0])
        row.update(origin_session=extraction['session'],origin_id=level['id'],ancestry=[level['id']],
            historical=False,matched_today=[],contributions=[contribution(row,extraction['session'],extraction['input_sha256'])])
        row['role_segments']=[dict(seg,lower=row['lower'],upper=row['upper'],price=row['price'],
            session=extraction['session'],historical=False) for seg in row['role_segments']]
        totals(row,policy);rows.append(row)
        if reaction_inputs is not None:
            update_center(row,extraction['session'],extraction['available_at'],extraction['settings']['tick'])
    result=dict(version=VERSION,ticker=extraction['ticker'],session=extraction['session'],available_at=extraction['available_at'],
        prior_checkpoint_hash=None,source_extraction_hash=digest(extraction),policy=asdict(policy),split_audit=[],
        counts=dict(prior=0,carried=0,matched_zones=0,new=len(rows),weakened=sum(r['strength_status']=='weakened' for r in rows)),levels=rows)
    if reaction_inputs is not None:result['reaction_center_config']=CENTER_CONFIG.copy()
    result['checkpoint_hash']=digest(result)
    return result


def consolidate(prior,extraction,bars,profile,*,split_factor=1.,split_evidence=(),policy=Policy()):
    """No cross-day backdating, prior mutation, or summation of merged encounters."""
    if prior.get('checkpoint_hash')!=digest({k:v for k,v in prior.items() if k!='checkpoint_hash'}):
        raise ValueError('Prior checkpoint integrity mismatch')
    if prior['version']!=VERSION or prior['ticker']!=extraction['ticker']:
        raise ValueError('Checkpoint identity mismatch')
    centers='reaction_center_config' in prior
    if centers and prior['reaction_center_config']!=CENTER_CONFIG:
        raise ValueError('Reaction center model version mismatch')
    if digest(dict(bars=bars,profile=profile))!=extraction['input_sha256']:
        raise ValueError('Session evidence does not match extraction input hash')
    if prior['session']>=extraction['session']:
        raise ValueError('Each session may be consolidated only once, in increasing order')
    start=datetime.fromisoformat(extraction['source']['start']).timestamp()
    if prior['available_at']>start or not isfinite(split_factor) or split_factor<=0:
        raise ValueError('Invalid prior availability or split factor')
    if split_factor!=1 and not split_evidence:
        raise ValueError('Price adjustment requires explicit corporate-action evidence')
    s=Settings(**extraction['settings']);end=extraction['available_at']
    if not bars or bars[0]['t']<start or bars[-1]['t']>end:
        raise ValueError('Evidence outside checkpoint session')
    rows=deepcopy(prior['levels']);matched={r['id']:[] for r in rows}
    for row in rows:
        for k in ('lower','upper','price'):row[k]*=split_factor
        if centers:
            for day in row['contributions']:
                day['reaction_price_factor']=day.get('reaction_price_factor',1.)*split_factor
        row['historical']=True
    # Each proposal joins at most one existing identity. Prior identities never
    # chain-merge through today's proposals, and historical geometry stays fixed.
    fresh=[]
    for zone in sorted(extraction['levels'],key=lambda z:(z['price'],z['id'])):
        candidates=[]
        for row in rows:
            members=matched[row['id']]
            width=max(row['upper']-row['lower'],zone['upper']-zone['lower'])
            union=max([row['upper'],zone['upper']]+[z['upper'] for z in members])-min([row['lower'],zone['lower']]+[z['lower'] for z in members])
            gap=max(0,row['lower']-zone['upper'],zone['lower']-row['upper'])
            if (gap<=min(width/4,extraction['geometry']['prominence']/4)
                    and union<=width*policy.maximum_union_width_multiple
                    and abs(row['price']-zone['price'])<=width*policy.maximum_center_distance_multiple):
                candidates.append(row)
        if candidates:
            winner=min(candidates,key=lambda r:(abs(r['price']-zone['price']),r['id']))
            matched[winner['id']].append(zone)
        else:
            fresh.append(zone)
    for row in rows:
        events=encounter_evidence(bars,row['lower'],row['upper'],extraction['geometry']['prominence'],
            max(s.tick,(row['upper']-row['lower'])/2),s)
        if centers:events=annotate(events,bars)
        day=dict(encounters=events,support_rejections=sum(e['role']=='support' and e['outcome']=='rejection' for e in events),
            resistance_rejections=sum(e['role']=='resistance' and e['outcome']=='rejection' for e in events),
            accepted_crossings=sum(e['outcome']=='acceptance' for e in events),
            profile_volume=sum(p['volume'] for p in profile if row['lower']<=p['price']<=row['upper']))
        row['contributions'].append(contribution(day,extraction['session'],extraction['input_sha256']))
        row['matched_today']=[z['id'] for z in matched[row['id']]]
        row['ancestry']=sorted(set(row['ancestry']+row['matched_today']))
        initial=row['role_segments'][-1]['role'] if row['role_segments'] else 'transition'
        row['role_segments'].extend(dict(seg,lower=row['lower'],upper=row['upper'],price=row['price'],
            session=extraction['session'],historical=True) for seg in role_timeline(events,end,initial_role=initial,session_start=start))
        row['closing_role']='support' if bars[-1]['close']>row['upper'] else 'resistance' if bars[-1]['close']<row['lower'] else 'within_band'
        row['available_at']=end
        totals(row,policy)
        if centers:update_center(row,extraction['session'],end,s.tick)
    added=seed(dict(extraction,levels=fresh),policy,reaction_inputs=(bars,profile) if centers else None)['levels'];rows.extend(added)
    result=dict(version=VERSION,ticker=extraction['ticker'],session=extraction['session'],available_at=end,
        prior_checkpoint_hash=prior['checkpoint_hash'],source_extraction_hash=digest(extraction),policy=asdict(policy),
        split_audit=[*prior['split_audit'],dict(session=extraction['session'],price_factor=split_factor,evidence=list(split_evidence))],
        counts=dict(prior=len(prior['levels']),carried=len(prior['levels']),matched_zones=sum(map(len,matched.values())),
            new=len(added),weakened=sum(r['strength_status']=='weakened' for r in rows)),levels=sorted(rows,key=lambda r:(r['price'],r['id'])))
    if centers:result['reaction_center_config']=CENTER_CONFIG.copy()
    result['checkpoint_hash']=digest(result)
    return result
