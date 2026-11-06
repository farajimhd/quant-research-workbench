"""Training replay quality plus bounded-memory behavioral coverage selection."""
import numpy as np


def percentile(values):
    values=np.asarray(values);_,inverse,counts=np.unique(values,return_inverse=True,return_counts=True)
    return (np.cumsum(counts)-.5*counts)[inverse]/len(values)


def profit_concentration(pnl):
    positive=np.maximum(pnl,0.)
    total=positive.sum(1)
    # No positive session is not evidence of well-distributed profit.
    return np.divide(positive.max(1),total,out=np.ones(len(total)),where=total>0)


def session_descriptor(metrics):
    n=len(metrics['net_pnl']);entries=np.zeros((n,64),np.float64);exposure=np.zeros_like(entries)
    for i,(ids,starts,ends) in enumerate(zip(metrics['episode_listings'],metrics['episode_entry_clocks'],metrics['episode_exit_clocks'])):
        ids=np.asarray(ids,np.int64);starts=np.asarray(starts,np.int64);ends=np.asarray(ends,np.int64)
        entries[i]=np.bincount((ids*31+starts//300)%64,minlength=64)
        exposure[i]=np.bincount(ids%64,weights=np.maximum(ends-starts,0),minlength=64)
    return entries,exposure


def select(ordered,panel,size=1024):
    n=len(panel['score'])
    if n!=6*size or len(ordered)<2:raise ValueError('Complete sixfold training replay required')
    from .full_common import aggregate
    pnl=np.asarray([aggregate([m])['score'] for m in ordered],float).T
    trades=np.asarray([m['closed_positions'] for m in ordered],float).T
    if not np.isfinite(pnl).all():raise ValueError('Nonfinite training outcomes')
    blocks=np.stack([pnl[:,part].sum(1) for part in np.array_split(np.arange(pnl.shape[1]),min(5,pnl.shape[1]))],1)
    concentration=profit_concentration(pnl)
    support=(trades>0).sum(1)
    quality=(.5*percentile(panel['score'])+.2*percentile(np.quantile(blocks,.25,axis=1))
             +.15*percentile(support)+.15*percentile(-concentration))
    eligible=np.asarray(panel['eligible'],bool)&(support>=2)&(trades.sum(1)>=5)
    if eligible.sum()<size:raise ValueError(f'Only {eligible.sum()} supported founders; require {size}')
    # Compact real entry-time/ticker exposure descriptors; collisions soften
    # distances only, never replace exact genome or receipt identity.
    entries=np.zeros((n,64),np.float32);exposure=np.zeros_like(entries)
    for m in ordered:
        e,x=m['_founder_descriptor'] if '_founder_descriptor' in m else session_descriptor(m)
        entries+=e;exposure+=x
    def normalized(x):
        return x/np.maximum(np.linalg.norm(x,axis=1,keepdims=True),1e-12)
    descriptor=np.concatenate((normalized(pnl)*np.sqrt(.4),normalized(entries)*np.sqrt(.35),
                               normalized(exposure)*np.sqrt(.25)),1).astype(np.float32)
    descriptor=normalized(descriptor)
    ranked=np.flatnonzero(eligible);ranked=ranked[np.argsort(-quality[ranked],kind='stable')]
    # Greedy leaders group near-identical full-replay behavior without an NxN matrix.
    labels=np.full(n,-1,int);leaders=[]
    for i in ranked:
        similarity=descriptor[leaders]@descriptor[i] if leaders else np.empty(0)
        if len(similarity) and similarity.max()>=.98:labels[i]=int(similarity.argmax())
        else:labels[i]=len(leaders);leaders.append(int(i))
    # Explicit capacity chosen from observed group sizes; never silently drop
    # the 1024 target or relax support. Report the resulting limit.
    group_sizes=np.bincount(labels[ranked]);cap=1
    while np.minimum(group_sizes,cap).sum()<size:cap+=1
    counts=np.zeros(len(leaders),int);chosen=[];available=eligible.copy()
    quality_count=3*size//4
    from .full_common import aggregate
    nominations={}
    for day,m in enumerate(ordered):
        scores=aggregate([m])['score']
        for metric,values in (('score',scores),):
            winner=int(np.argmax(np.where(np.asarray(m['terminal_valid'],bool),values,-np.inf)))
            if eligible[winner]:nominations.setdefault(winner,[]).append(dict(session=day,metric=metric))
    champions=[]
    for i in sorted(nominations,key=lambda i:(-quality[i],-len(nominations[i]),i)):
        if counts[labels[i]]>=cap:continue
        champions.append(i);chosen.append(i);available[i]=False;counts[labels[i]]+=1
        if len(champions)>=size//4:break
    for round_index in range(cap):
        for i in ranked:
            if available[i] and counts[labels[i]]==round_index:
                chosen.append(int(i));available[i]=False;counts[labels[i]]+=1
                if len(chosen)==quality_count:break
        if len(chosen)==quality_count:break
    # Exploration among supported candidates: farthest from the selected set,
    # with a modest quality preference. No family or ancestry quota survives.
    distance=np.ones(n,np.float32)
    for i in chosen:distance=np.minimum(distance,1-descriptor@descriptor[i])
    while len(chosen)<size:
        candidates=np.flatnonzero(available)
        candidates=candidates[counts[labels[candidates]]<cap]
        if not len(candidates):raise ValueError('Founder diversity capacity exhausted')
        i=int(candidates[np.argmax(distance[candidates]+.1*quality[candidates])])
        chosen.append(i);available[i]=False;counts[labels[i]]+=1
        distance=np.minimum(distance,1-descriptor@descriptor[i])
    return chosen,dict(version='full-replay-sixfold-selection-v2-champions',replayed=n,selected=size,
        champion_nominees=len(nominations),champion_limit=size//4,champion_selected=champions,
        champion_wins={str(i):nominations[i] for i in champions},champions_are_ordinary_parents=True,
        supported=int(eligible.sum()),quality_selected=quality_count,exploration_selected=size-quality_count,
        behavior_groups=len(leaders),group_cap=cap,largest_selected_group=int(counts.max()),
        quality_weights=dict(fitness=.5,lower_quartile_block_score=.2,active_sessions=.15,score_dispersion=.15),
        minimum_trades=5,minimum_active_sessions=2,validation_used=False,reproduction_performed=False)
