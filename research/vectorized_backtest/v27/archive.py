"""Bounded parameter-distance archives; no validation or trading traces."""
from dataclasses import asdict
import numpy as np
from .genome import STAGES,effective_rules
from .program import Op,MAX_NODES
from .features import CATALOG
from .complexity import simplify
from .lineage import program_key


def leaf_descriptor(member):
    """Categorical feature/op sets, DAG coordinates and unit-scaled parameters."""
    units={}
    for f in CATALOG:
        lo,hi=units.get(f.unit,(f.lower,f.upper));units[f.unit]=(min(lo,f.lower),max(hi,f.upper))
    from .semantics import identity,disabled
    effective=identity(member);parts=[]
    for stage in STAGES:
        p=simplify(effective_rules(member)[stage]);features=np.zeros(len(CATALOG));ops=np.zeros(24);nodes=np.zeros((MAX_NODES,6))
        reachable=not disabled(p) and not (stage=='add' and member.policy.maximum_adds==0)
        for i,n in (enumerate(p.nodes) if reachable else []):
            ops[int(n.op)]=1
            if n.op==Op.FEATURE:features[n.feature]=1
            lo,hi=units.get(n.unit,(-1.,1.));constant=np.clip((n.value-lo)/max(hi-lo,1e-12),0,1) if n.op==Op.CONSTANT else 0
            nodes[i]=[int(n.op)/23,max(n.a,0)/(MAX_NODES-1),max(n.b,0)/(MAX_NODES-1),np.log1p(n.window)/np.log(121),constant,n.op==Op.CONSTANT]
        weight=2. if stage=='entry' else 1.
        parts.extend([features*weight,ops*weight,nodes.ravel()*.25*weight])
    policy=effective['policy']
    limits={'stop_fraction':1.,'target_fraction':1.,'reduce_fraction':1.,'trail_fraction':1.,'add_minimum_profit':1.,'reduce_minimum_profit':1.,'maximum_adds':20.,'cooldown':300.,'swing_left':60.,'swing_right':60.}
    parts.append(np.asarray([policy.get(k,0)/limits[k] for k in sorted(limits)]))
    parts.append(np.asarray([effective['minimum_age'].get(s,0)/3600 for s in STAGES]))
    return np.concatenate(parts).astype(np.float32)


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


def descriptor(member):
    from .composition import MODES
    if member.routing is None:
        base=leaf_descriptor(member);return np.r_[base,np.zeros_like(base),np.zeros(len(MODES)+1,dtype=np.float32)]
    r=member.routing;mode=np.zeros(len(MODES)+1,dtype=np.float32);mode[MODES.index(r["mode"])]=1
    if r.get("condition") is not None:mode[-1]=len(r["condition"].nodes)/128
    return np.r_[leaf_descriptor(r["left"]),leaf_descriptor(r["right"]),mode]
