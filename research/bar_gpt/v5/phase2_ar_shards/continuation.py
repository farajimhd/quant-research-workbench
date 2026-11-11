"""Disjoint session-origin coverage for successive immutable sample plans."""

import json
from pathlib import Path

import numpy as np

from .contract import BLOCK, ORIGIN
from .storage import read_receipt
from ..shards.contract import digest
from ..quote_repair_v1 import verify_seal


def semantic_identity(plan):
    identity={k: plan[k] for k in (
        'version','geometry','parent_binding','ordinal_hash','authority',
        'source_query','quote_policy')}
    identity['producers']={k:v for k,v in plan['code'].items() if not k.startswith('phase2_ar_shards/')}
    return digest(identity)


def validate_parents(plan):
    """Every earlier plan is terminal and uses the exact same bar authority."""
    roots=[]
    for parent in plan.get('continuation',{}).get('parents',[]):
        root=Path(parent['root']); prior=json.loads((root/'plan.json').read_text())
        verify_seal(prior,'hash')
        if prior['hash']!=parent['binding'] or semantic_identity(prior)!=semantic_identity(plan):
            raise ValueError('Continuation changes bar/source/geometry authority')
        result=json.loads((root/'result.json').read_text())
        if result.get('binding')!=prior['hash'] or result.get('state') not in (
            'complete','inventory_exhausted','capacity_reached') or result.get('failure'):
            raise ValueError('Prior sample plan is not successfully terminal')
        roots.append((root,prior['hash']))
    return roots


def prior_ranges(ticker, day, count, parents):
    """Ranks in the unchanged full-session observed 1s sequence, not UTC duration."""
    ranges=[]
    for root,binding in parents:
        path=root/'shards'/digest(ticker)[:24]/day
        if not (path/'receipt.json').exists():continue
        receipt=read_receipt(path,binding)
        if receipt['ticker']!=ticker or receipt['session_date']!=day:
            raise ValueError('Prior session identity differs')
        if receipt['sampling']['session_origins']!=count:
            raise ValueError('Canonical session-origin count drifted')
        coverage=receipt.get('origin_coverage')
        if coverage is None:
            sampling,stats=receipt['sampling'],receipt['stats']
            start=sampling['unselected_prefix_origins']
            if stats['source_origins']!=count-start or stats['boundary_tail_deferred']:
                raise ValueError('Unknown original session coverage geometry')
            lo=start+stats['warmup_deferred']; hi=lo+receipt['blocks']*BLOCK
            coverage=dict(session_origins=count,ranges=[[lo,hi]] if hi>lo else [])
        if coverage['session_origins']!=count:
            raise ValueError('Prior coverage belongs to a different source sequence')
        spans=coverage['ranges']
        if sum(hi-lo for lo,hi in spans)!=receipt['blocks']*BLOCK:
            raise ValueError('Prior block coverage does not reconcile')
        ranges.extend(spans)
    merged=[]
    for lo,hi in sorted(ranges):
        if not 0<=lo<hi<=count or (hi-lo)%BLOCK:
            raise ValueError('Malformed prior session-origin range')
        if merged and lo<merged[-1][1]:
            raise ValueError('Existing plans contain overlapping published origins')
        if merged and lo==merged[-1][1]:merged[-1][1]=hi
        else:merged.append([lo,hi])
    return merged


def uncovered(compiled, current, ticker, day, parents, history=(64,64,64,64,64)):
    """Reblock disjoint remaining runs; never join across an old block or day."""
    count=len(current); excluded=prior_ranges(ticker,day,count,parents)
    origins=np.concatenate((compiled['blocks'].reshape(-1),compiled['pending']))
    ranks=np.searchsorted(current['end_us'],origins['end_us'])
    if len(origins) and (not np.array_equal(current['end_us'][ranks],origins['end_us'])
                         or np.any(np.diff(ranks)!=1)):
        raise ValueError('Full-session compiler origins are not consecutive')
    keep=np.ones(len(origins),dtype=bool)
    for lo,hi in excluded:keep&=(ranks<lo)|(ranks>=hi)
    remaining=origins[keep];positions=ranks[keep]
    boundaries=np.r_[0,np.flatnonzero(np.diff(positions)!=1)+1,len(positions)]
    blocks=[];spans=[];pending=np.empty(0,dtype=ORIGIN);fragments=0
    for left,right in zip(boundaries[:-1],boundaries[1:]):
        run=remaining[left:right]; n=len(run)//BLOCK; length=n*BLOCK
        if n:
            blocks.append(run[:length].reshape(n,BLOCK))
            spans.append([int(positions[left]),int(positions[left]+length)])
        tail=run[length:]
        if right==len(remaining):pending=tail.copy()
        else:fragments+=len(tail)
    excluded_count=sum(hi-lo for lo,hi in excluded)
    compiled['blocks']=np.concatenate(blocks) if blocks else np.empty((0,BLOCK),dtype=ORIGIN)
    split=int(day[:4]=='2026');compiled['splits']=[split]*len(compiled['blocks'])
    compiled['pending']=pending
    compiled['stats'].update(source_origins=count-excluded_count,
        boundary_tail_deferred=fragments,pending_origins=len(pending),
        previously_published_origins=excluded_count)
    stats=compiled['stats']
    if stats['source_origins']!=len(compiled['blocks'])*BLOCK+len(pending)+stats['warmup_deferred']+fragments:
        raise ValueError('Continuation source-origin accounting differs')
    compiled['origin_coverage']=dict(version='disjoint-session-origin-ranks-v1',
        session_origins=count,ranges=spans,excluded_ranges=excluded)
    # Keep exactly the history/rows referenced by each new contiguous run.
    # Earlier plans' intervening bars are not repeated as unused future payload.
    trim_pools(compiled,history)
    compiled['sampling']['first_eligible_origin_us']=(int(compiled['blocks'][0,0]['end_us'])
        if len(compiled['blocks']) else int(compiled['pending'][0]['end_us']) if len(compiled['pending']) else None)
    compiled['sampling']['origin_distribution']='remaining consecutive session origins; earlier published origins excluded'
    if not len(compiled['blocks']):compiled['sampling']['reason']='no_remaining_complete_session_block'
    return compiled


def trim_pools(compiled,histories):
    blocks=compiled['blocks'];pending=compiled['pending'].copy()
    if len(pending):pending['latest']-=np.asarray(compiled['pool_starts'])
    refs=np.concatenate((blocks.reshape(-1),pending))
    groups=[b for b in blocks]
    if len(pending):groups.append(pending)
    before=sum(a.nbytes for a in compiled['views'])
    for frame,history in enumerate(histories):
        spans=sorted((int(g['latest'][:,frame].min())-history+1,
                      int(g['latest'][:,frame].max())+1) for g in groups if len(g))
        merged=[]
        for lo,hi in spans:
            if lo<0 or hi>len(compiled['views'][frame]):raise ValueError('Continuation context bounds differ')
            if merged and lo<=merged[-1][1]:merged[-1][1]=max(hi,merged[-1][1])
            else:merged.append([lo,hi])
        indices=np.concatenate([np.arange(lo,hi,dtype=np.int64) for lo,hi in merged]) if merged else np.empty(0,dtype=np.int64)
        compiled['views'][frame]=compiled['views'][frame][indices].copy()
        refs['latest'][:,frame]=np.searchsorted(indices,refs['latest'][:,frame])
    length=len(blocks)*BLOCK
    compiled['blocks']=refs[:length].reshape(-1,BLOCK);compiled['pending']=refs[length:].copy()
    compiled['pool_starts']=[0]*5
    compiled['cache_slices']=[min(max(0,len(a)-h),int(compiled['pending']['latest'][0,i])-h+1)
        if len(compiled['pending']) else max(0,len(a)-h)
        for i,(a,h) in enumerate(zip(compiled['views'],histories))]
    compiled['stats']['unused_pool_bytes_avoided']=before-sum(a.nbytes for a in compiled['views'])
