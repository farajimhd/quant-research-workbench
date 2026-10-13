"""Bounded numeric pedigree; training-only selection and deterministic resume."""
from dataclasses import dataclass, asdict
from hashlib import sha256
import json
import numpy as np
from .genome import sample, mutate, crossover, effective_rules
from .complexity import simplify, count


def program_key(member):
    policy=asdict(member.policy)
    if policy['swing_left']:policy.pop('stop_fraction')
    value=dict(rules={s:simplify(p).payload() for s,p in effective_rules(member).items()},
               policy=policy,minimum_age=member.minimum_age)
    return sha256(json.dumps(value,sort_keys=True,separators=(',',':')).encode()).hexdigest()


@dataclass(frozen=True)
class Selection:
    ancestry_generations:int=5
    global_fraction:float=.5
    global_elite_fraction:float=.1
    global_fresh_fraction:float=.1
    local_fresh_fraction:float=.2
    champion_partners:int=5
    mate_attempts:int=8
    mutation_min:float=.20
    mutation_max:float=.85

    def validate(self):
        if not 1<=self.ancestry_generations<=8 or not 1<=self.mate_attempts<=32:raise ValueError('Invalid bounded ancestry/mating')
        if not 0<self.global_fraction<1 or not 0<=self.global_elite_fraction<1 or not 0<=self.global_fresh_fraction<1 or not 0<=self.local_fresh_fraction<1:raise ValueError('Invalid path proportions')
        if self.global_elite_fraction+self.global_fresh_fraction>=1:raise ValueError('Global reproduction budget empty')
        if not 0<=self.mutation_min<=self.mutation_max<=1 or not 0<=self.champion_partners<=30:raise ValueError('Invalid mutation/champion settings')
        return self


class Pedigree:
    def __init__(self,depth=5,records=None):
        self.depth=depth;self.records=records or []

    def birth(self,members,generation,parents,operations):
        """Batch numeric gather of parent metadata; only bounded DAG packing loops."""
        parents=np.asarray(parents,dtype=np.int64).reshape(-1,2)
        size=len(members);base=len(self.records)
        old=len(self.records)
        primary=np.where(parents[:,0]>=0,parents[:,0],old)
        mutations=np.asarray([r['mutations'] for r in self.records]+[0],dtype=np.int64)[primary]+np.asarray([('mutation' in str(o)) for o in operations],dtype=np.int64)
        crosses=np.asarray([r['crossovers'] for r in self.records]+[0],dtype=np.int64)[primary]+np.asarray([('crossover' in str(o)) for o in operations],dtype=np.int64)
        result=[]
        for j,(member,pair,op) in enumerate(zip(members,parents,operations)):
            ancestors={}
            for p in pair[pair>=0]:
                record=self.records[int(p)];ancestors[int(p)]=1
                for ancestor,distance in record['ancestors']:
                    ancestors[ancestor]=min(ancestors.get(ancestor,self.depth+1),distance+1)
            ancestors={i:distance for i,distance in ancestors.items() if distance<=self.depth}
            # Pedigree generations are parent-child links, not elite survival time.
            if len(ancestors)>2**(self.depth+1)-2:raise ValueError('Pedigree bound exceeded')
            key=program_key(member);serial=base+j
            origin=self.records[int(pair[0])].get("origin_generation",self.records[int(pair[0])]["generation"]) if pair[0]>=0 else generation
            record=dict(serial=serial,generation=generation,origin_generation=origin,parents=pair.tolist(),operation=str(op),
                mutations=int(mutations[j]),crossovers=int(crosses[j]),ancestors=sorted(ancestors.items()),
                program_sha256=key,complexity=count(member),
                label=f'G{origin:03d}-{serial:06d}-M{mutations[j]:03d}-X{crosses[j]:03d}-{key[:4]}')
            result.append(record)
        self.records.extend(result)
        return np.arange(base,base+size,dtype=np.int64)

    def relatives(self,one,others,generation):
        """Vectorized membership against one bounded lineage, never an NxN matrix."""
        def recent(i):
            r=self.records[int(i)]
            return [int(i)]+[a for a,distance in r['ancestors'] if distance<=self.depth]
        target=np.asarray(recent(one),dtype=np.int64)
        arrays=[recent(i) for i in others]
        if not arrays:return np.zeros(0,dtype=bool)
        widths=np.fromiter(map(len,arrays),dtype=np.int64)
        matches=np.isin(np.concatenate(arrays),target)
        return np.logical_or.reduceat(matches,np.cumsum(widths)-widths)


def session_order(values,eligible,ids):
    values=np.asarray(values,dtype=np.float64);eligible=np.asarray(eligible,dtype=bool)
    if values.ndim!=2 or values.shape!=eligible.shape or values.shape[1]!=len(ids) or not np.isfinite(values).all():
        raise ValueError('Invalid session selection panel')
    # Stable serial tie break, followed by vectorized independent session sorts.
    base=np.argsort(ids,kind='stable')
    order=base[np.argsort(-np.where(eligible[:,base],values[:,base],-np.inf),axis=1,kind='stable')]
    return order,np.take_along_axis(eligible,order,axis=1)


def balanced_shortlist(order,valid,size,generation):
    """Round-robin session ranks, unique candidates; no RNG/validation access."""
    days=order.shape[0];rotation=np.roll(np.arange(days),-(generation-1)%days)
    sequence=order[rotation].T.ravel();mask=valid[rotation].T.ravel()
    sequence=sequence[mask]
    _,first=np.unique(sequence,return_index=True)
    return sequence[np.sort(first)][:size].tolist()


class MateIndex:
    """Pack bounded ancestry once; vectorized membership, bounded row cache."""
    def __init__(self,pedigree,ids):
        self.ids=np.asarray(ids);self.pedigree=pedigree;self.cache={}
        rows=[[int(i)]+[a for a,d in pedigree.records[int(i)]['ancestors'] if d<=pedigree.depth] for i in ids]
        widths=np.asarray([len(r) for r in rows]);self.starts=np.cumsum(widths)-widths
        self.flat=np.concatenate(rows);self.rows=rows

    def unrelated(self,a):
        if a not in self.cache:
            row=~np.logical_or.reduceat(np.isin(self.flat,self.rows[a]),self.starts)
            if len(self.cache)>=128:self.cache.pop(next(iter(self.cache)))
            self.cache[a]=row
        return self.cache[a]


def training_shortlist(global_values,eligible,order,valid,size,generation,ids):
    """Half global/half local, deduplicated and frozen before heldout execution."""
    mask=eligible.any(0);global_order=np.lexsort((ids,-np.asarray(global_values)))
    global_order=global_order[mask[global_order]]
    local=balanced_shortlist(order,valid,len(ids),generation)
    chosen=list(global_order[:size//2]);seen=set(chosen)
    for i in local:
        if i not in seen:chosen.append(i);seen.add(i)
        if len(chosen)>=size:break
    return chosen[:size]


def next_population(rng,population,ids,pedigree,rank,generation,config=Selection(),*,session_values,session_eligible,global_values):
    from .variation import mutate_fields
    config.validate();n=len(population);global_n=int(n*config.global_fraction);local_n=n-global_n
    order,valid=session_order(session_values,session_eligible,ids);days=len(order)
    if local_n<days:raise ValueError('Local path must support all sessions')
    values=np.asarray(global_values,dtype=np.float64)
    if values.shape!=(n,) or not np.isfinite(values).all():raise ValueError('Finite global panel required')
    global_order=np.lexsort((ids,-values));global_order=global_order[session_eligible.any(0)[global_order]]
    mates=MateIndex(pedigree,ids);elites=[];elite_routes=[];keys=set();children=[];parents=[];ops=[];routes=[]
    failures=duplicates=0;uses=np.zeros(n,dtype=np.int64)
    def elite(a,path,session=None):
        key=program_key(population[a])
        if key in keys:return False
        keys.add(key);elites.append(a);elite_routes.append(dict(path=path,session=session));return True
    for a in global_order[:int(global_n*config.global_elite_fraction)]:elite(int(a),'global')
    global_elites=len(elites)
    local_fresh=int(local_n*config.local_fresh_fraction);local_budget=local_n-local_fresh
    active=valid.any(1);champions=order[:,0]
    for s in np.flatnonzero(active):elite(int(champions[s]),'local',int(s))
    local_elites=len(elites)-global_elites
    width=max(1,local_budget//days);pools=order[:,:width];pool_valid=valid[:,:width]
    cycles=[rng.permutation(pools[s,pool_valid[s]]).tolist() for s in range(days)];cursors=np.zeros(days,dtype=int)
    def append(a=None,b=None,path='global',session=None,partner=None,route='general'):
        nonlocal duplicates
        child=sample(rng,1)[0] if a is None else crossover(rng,population[a],population[b])
        key=program_key(child)
        if key in keys:duplicates+=1;return False
        keys.add(key);children.append(child);parents.append((-1,-1) if a is None else (int(ids[a]),int(ids[b])))
        ops.append('fresh' if a is None else 'crossover');routes.append(dict(path=path,source_session=session,partner_session=partner,selection_route=route))
        if a is not None:uses[a]+=1;uses[b]+=1
        return True
    def immigrant(path):
        for _ in range(32):
            if append(path=path,route='fresh'):return
        raise RuntimeError('Unique immigrant budget exhausted')
    global_fresh=int(np.ceil(global_n*config.global_fresh_fraction))
    for _ in range(global_fresh):immigrant('global')
    for _ in range(global_n-global_elites-global_fresh):
        accepted=False
        for attempt in range(config.mate_attempts):
            if not len(global_order):break
            a=int(global_order[int(rng.integers(len(global_order),size=3).min())])
            candidates=global_order[mates.unrelated(a)[global_order]]
            if not len(candidates):continue
            b=int(candidates[int(rng.integers(len(candidates),size=3).min())])
            if append(a,b,'global'):accepted=True;break
        if not accepted:failures+=1;immigrant('global')
    champion_count=0;champion_budget=min(days*config.champion_partners,local_budget-local_elites)
    attempted=0
    for s in np.roll(np.arange(days),-(generation-2)%days):
        if not active[s]:continue
        a=int(champions[s]);partners=np.flatnonzero(active & (np.arange(days)!=s) & mates.unrelated(a)[champions])
        for t in rng.permutation(partners)[:config.champion_partners]:
            if attempted>=champion_budget:break
            attempted+=1
            if append(a,int(champions[t]),'local',int(s),int(t),'champion'):champion_count+=1
    general_n=local_budget-local_elites-champion_count;intra=0
    for s in np.resize(np.roll(np.arange(days),-(generation-2)%days),general_n):
        accepted=False
        for attempt in range(config.mate_attempts):
            if not cycles[s]:break
            if cursors[s]>=len(cycles[s]):cycles[s]=rng.permutation(pools[s,pool_valid[s]]).tolist();cursors[s]=0
            a=int(cycles[s][cursors[s]]);cursors[s]+=1
            candidates=pools[s,pool_valid[s] & mates.unrelated(a)[pools[s]]]
            if not len(candidates):continue
            b=int(rng.choice(candidates))
            if append(a,b,'local',int(s),int(s),'intrasession'):accepted=True;intra+=1;break
        if not accepted:failures+=1;immigrant('local')
    for _ in range(local_fresh):immigrant('local')
    if len(elites)+len(children)!=n:raise RuntimeError('Path budgets did not reconcile')
    # Reproduction is complete. One vectorized draw now selects mutation across
    # BOTH paths. Preserved elites and fresh immigrants are never in this mask.
    eligible=np.flatnonzero(np.asarray(ops)=='crossover');rate=float(rng.uniform(config.mutation_min,config.mutation_max))
    chosen=rng.choice(eligible,size=int(round(rate*len(eligible))),replace=False)
    edit_counts=rng.integers(1,6,size=len(chosen));mutation_fields=[]
    for j,count in zip(chosen,edit_counts):
        old=children[int(j)];old_key=program_key(old);keys.remove(old_key)
        for attempt in range(config.mate_attempts):
            child,edits=mutate_fields(rng,old,int(count));key=program_key(child)
            if key not in keys:break
        else:raise RuntimeError('Mutation dedup budget exhausted; no silent lost edits')
        keys.add(key);children[int(j)]=child;ops[int(j)]='crossover_mutation'
        routes[int(j)].update(mutation_field_count=len(edits),mutation_fields=edits)
        mutation_fields.extend(edits)
    born=pedigree.birth(children,generation,parents,ops)
    for serial,route in zip(born,routes):pedigree.records[int(serial)].update(route)
    report=dict(elites=len(elites),global_elites=global_elites,local_champions=local_elites,global_slots=global_n,local_slots=local_n,
        fresh_candidates=ops.count('fresh'),crossovers=sum('crossover' in o for o in ops),mutations=len(chosen),mutation_eligible=len(eligible),
        mutation_rate=rate,mutation_requested_fields=int(edit_counts.sum()),mutation_applied_fields=len(mutation_fields),
        champion_crossovers=champion_count,intrasession_crossovers=intra,unrelated_mate_fallbacks=failures,rejected_duplicates=duplicates,
        session_parent_pool_width=width,empty_session_pools=int((~active).sum()),unique_parent_draws=int((uses>0).sum()),maximum_parent_draws=int(uses.max()))
    return [population[i] for i in elites]+children,np.concatenate((ids[elites],born)),report
