"""Bounded parameter-distance archives; no validation or trading traces."""
from dataclasses import asdict
import numpy as np
from .genome import STAGES,effective_rules
from .program import Op,MAX_NODES
from .features import CATALOG
from .complexity import simplify
from .lineage import program_key


def descriptor(member):
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


def select_archive(records,capacity=64,shortlist=256,quality_weight=.7):
    """Canonical dedup, bounded score shortlist, greedy quality/distance selection."""
    if not 1<=capacity<=shortlist<=1024 or not 0<=quality_weight<=1:raise ValueError('Bounded archive required')
    unique={}
    for r in records:
        key=r['canonical_sha256']
        if key not in unique or (r['score'],-r['serial'])>(unique[key]['score'],-unique[key]['serial']):unique[key]=r
    pool=sorted(unique.values(),key=lambda r:(-r['score'],r['canonical_sha256']))[:shortlist]
    if len(pool)<=capacity:return pool
    x=np.stack([r['descriptor'] for r in pool]);scores=np.asarray([r['score'] for r in pool]);quality=(scores-scores.min())/max(float(np.ptp(scores)),1e-12)
    chosen=[0];distance=np.full(len(pool),np.inf);available=np.ones(len(pool),bool)
    for _ in range(capacity-1):
        distance=np.minimum(distance,np.square(x-x[chosen[-1]]).sum(1));available[chosen[-1]]=False
        novelty=distance/max(float(distance[available].max()),1e-12)
        merit=quality_weight*quality+(1-quality_weight)*novelty;merit[~available]=-np.inf
        chosen.append(int(np.argmax(merit)))
    return [pool[i] for i in chosen]


def update_archive(previous,members,ids,values,eligible,pedigree,day,generation,capacity=64,shortlist=256):
    records=list(previous)
    indices=np.flatnonzero(eligible)
    # Exact dedup before descriptor construction; compute distances only for
    # the bounded quality shortlist, never all 1024 members on every generation.
    ranked=indices[np.lexsort((ids[indices],-np.asarray(values)[indices]))];seen={}
    for i in ranked:
        key=program_key(members[int(i)])
        if key not in seen:seen[key]=int(i)
        if len(seen)>=shortlist:break
    for i in seen.values():
        member=members[int(i)];record=pedigree.records[int(ids[i])]
        records.append(dict(day=day,generation=generation,serial=int(ids[i]),score=float(values[i]),payload=member.payload(),
                            canonical_sha256=program_key(member),descriptor=descriptor(member).tolist(),ancestry=record['ancestors']))
    return select_archive(records,capacity,shortlist)


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
