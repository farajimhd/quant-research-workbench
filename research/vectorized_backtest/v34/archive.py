"""Positive-profit archives; preserved selection semantics."""
import numpy as np
from .lineage import program_key

def reservoir(records,capacity):
    """Bounded hash-priority sample of historical positives plus profit leader."""
    if not 1<=capacity<=4096:raise ValueError('Bounded reservoir required')
    unique={}
    for r in records:
        if not np.isfinite(r['net_profit']) or r['net_profit']<=0:continue
        key=r['canonical_sha256']
        if key not in unique or r['serial']<unique[key]['serial']:unique[key]=r
    if not unique:return []
    best=min(unique.values(),key=lambda r:(-r['net_profit'],r['canonical_sha256']))
    others=sorted((r for k,r in unique.items() if k!=best['canonical_sha256']),key=lambda r:r['canonical_sha256'])
    return [best]+others[:capacity-1]


def select_archive(records,capacity=64,shortlist=1024,*,seed=0):
    """Positive-profit bands. No score gate or expensive pairwise descriptors."""
    if not 1<=capacity<=shortlist<=4096:raise ValueError('Bounded archive required')
    pool=sorted({r['canonical_sha256']:r for r in records if r['net_profit']>0}.values(),key=lambda r:(-r['net_profit'],r['serial']))
    if len(pool)<=capacity:return pool
    rng=np.random.default_rng(seed);n=len(pool);edges=[0,int(np.ceil(.1*n)),int(np.ceil(.3*n)),int(np.ceil(.6*n)),n]
    quotas=np.asarray([.5,.25,.15625,.09375])*capacity;slots=np.floor(quotas).astype(int)
    for i in np.argsort(-(quotas-slots),kind='stable')[:capacity-int(slots.sum())]:slots[i]+=1
    selected=[0]
    for band,(lo,hi) in enumerate(zip(edges[:-1],edges[1:])):
        options=np.arange(max(lo,1),hi);need=int(slots[band])-(band==0)
        if need>0 and len(options):selected.extend(rng.choice(options,min(need,len(options)),replace=False).tolist())
    present=set(selected)
    for i in range(n):
        if len(selected)>=capacity:break
        if i not in present:selected.append(i);present.add(i)
    return [pool[i] for i in selected]


def update_archive(previous,members,ids,values,eligible,pedigree,day,generation,capacity=64,shortlist=1024,*,profits,return_reservoir=False):
    records=list(previous)
    for i in np.flatnonzero(np.asarray(eligible)&(np.asarray(profits)>0)):
        member=members[int(i)];record=pedigree.records[int(ids[i])]
        records.append(dict(day=day,generation=generation,serial=int(ids[i]),score=float(values[i]),net_profit=float(profits[i]),payload=member.payload(),
            canonical_sha256=program_key(member),ancestry=record['ancestors']))
    retained=reservoir(records,shortlist)
    selected=select_archive(retained,capacity,shortlist,seed=np.random.SeedSequence([int(day.replace('-','')),generation]))
    return (selected,retained) if return_reservoir else selected


def balanced_experts(archives,slots):
    """Round-robin sessions, canonical dedup; excess archive stays available."""
    days=sorted(archives);positions={d:0 for d in days};chosen=[];seen=set()
    while len(chosen)<slots:
        advanced=False
        for day in days:
            records=archives[day]
            while positions[day]<len(records):
                r=records[positions[day]];positions[day]+=1;advanced=True
                if r['canonical_sha256'] in seen:continue
                seen.add(r['canonical_sha256']);chosen.append(r);break
            if len(chosen)==slots:break
        if not advanced:break
    return chosen
