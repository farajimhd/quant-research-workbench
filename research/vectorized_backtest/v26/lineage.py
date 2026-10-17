"""Bounded numeric pedigree; training-only selection and deterministic resume."""
from dataclasses import dataclass, asdict
from hashlib import sha256
import json
import numpy as np
from .genome import sample, mutate, crossover, effective_rules
from .complexity import simplify, count


def program_key(member):
    from .semantics import identity
    value=identity(member)
    return sha256(json.dumps(value,sort_keys=True,separators=(',',':')).encode()).hexdigest()


@dataclass(frozen=True)
class Selection:
    ancestry_generations:int=5
    global_fraction:float=1.
    global_elite_fraction:float=.1
    global_fresh_fraction:float=.2
    local_fresh_fraction:float=.2
    champion_partners:int=0
    mate_attempts:int=8
    mutation_min:float=.20
    mutation_max:float=.85

    def validate(self):
        if (self.global_fraction,self.global_elite_fraction,self.global_fresh_fraction,self.local_fresh_fraction,self.champion_partners)!=(1.,.1,.2,.2,0):raise ValueError('V26 requires approved global-only reproduction budgets')
        if not 1<=self.ancestry_generations<=8 or not 1<=self.mate_attempts<=32:raise ValueError('Invalid bounded ancestry/mating')
        if not 0<self.global_fraction<=1 or not 0<=self.global_elite_fraction<1 or not 0<=self.global_fresh_fraction<1 or not 0<=self.local_fresh_fraction<1:raise ValueError('Invalid path proportions')
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


def training_shortlist(global_values,eligible,order,valid,size,generation,ids,*,global_eligible):
    """Half global/half local, deduplicated and frozen before heldout execution."""
    mask=np.asarray(global_eligible,dtype=bool);global_order=np.lexsort((ids,-np.asarray(global_values)))
    global_order=global_order[mask[global_order]]
    local=balanced_shortlist(order,valid,len(ids),generation)
    chosen=list(global_order[:size//2]);seen=set(chosen)
    for i in local:
        if i not in seen:chosen.append(i);seen.add(i)
        if len(chosen)>=size:break
    return chosen[:size]


def next_population(rng,population,ids,pedigree,rank,generation,config=Selection(),*,session_values,session_eligible,global_values,global_eligible,target_size=None,behavior=None):
    """Fixed target budget, three parent routes; mutate every non-survivor category."""
    from .composition import mutation,cross
    from .sampling_report import snapshot,delta
    sampling_before=snapshot()
    from .semantics import SemanticError
    config.validate();n=len(population);target=n if target_size is None else int(target_size)
    if not 10<=target<=65536:raise ValueError('Bounded target required')
    values=np.asarray(global_values,dtype=np.float64);valid=np.asarray(global_eligible,dtype=bool)
    if values.shape!=(n,) or valid.shape!=(n,) or not np.isfinite(values).all():raise ValueError('Invalid score panel')
    order=np.lexsort((ids,-values));order=order[valid[order]]
    mates=MateIndex(pedigree,ids);keys=set();elites=[]
    elite_budget=int(target*.1);fresh_budget=int(np.ceil(target*.2))
    for a in order:
        key=program_key(population[int(a)])
        if key not in keys:elites.append(int(a));keys.add(key)
        if len(elites)>=elite_budget:break
    children=[];parents=[];ops=[];routes=[];failures=duplicates=0
    behavior_route='random';similarity=None
    def append(a=None,b=None,route='fresh'):
        nonlocal duplicates
        operation='fresh'
        try:
            if a is None:child=sample(rng,1)[0]
            elif rng.random()<.15:
                child,edits=mutation(rng,population[a],int(rng.integers(1,6)),False);operation='mutation_only';b=None
            else:child,operation=cross(rng,population[a],population[b])
        except (SemanticError,ValueError):return False
        key=program_key(child)
        if key in keys:duplicates+=1;return False
        keys.add(key);children.append(child);parents.append((-1,-1) if a is None else (int(ids[a]),int(ids[b]) if b is not None else -1))
        ops.append('fresh' if a is None else 'mutation' if b is None else 'crossover');routes.append(dict(path='global',selection_route=route,composition_operator=operation,behavior_route=behavior_route if a is not None else None,parent_similarity=similarity if a is not None else None));return True
    def fresh(route='fresh'):
        for _ in range(32):
            if append(route=route):return
        raise RuntimeError('Unique immigrant budget exhausted')
    for _ in range(fresh_budget):fresh()
    fractions=np.asarray([.5,.3,.2])*(target-len(elites)-fresh_budget);budgets=np.floor(fractions).astype(int)
    for k in np.argsort(-(fractions-budgets),kind='stable')[:target-len(elites)-fresh_budget-int(budgets.sum())]:budgets[k]+=1
    elite_pool=np.asarray(elites,dtype=int)
    def draw(pool,tournament):
        return int(pool[int(rng.integers(len(pool),size=3).min())]) if tournament else int(rng.choice(pool))
    for route,budget in zip(('score','random','elite'),budgets):
        for _ in range(int(budget)):
            accepted=False
            for pool in ([elite_pool,order] if route=='elite' else [order]):
                if len(pool)<2:continue
                for _ in range(config.mate_attempts):
                    a=draw(pool,route=='score');options=pool[mates.unrelated(a)[pool]]
                    if not len(options):continue
                    behavior_route=('similar','dissimilar','random')[int(rng.integers(3))]
                    if behavior is not None:
                        b,similarity=behavior.partner(rng,a,options,behavior_route)
                        if b is None:continue
                    else:b,similarity=draw(options,route=='score'),None
                    if append(a,b,route):accepted=True;break
                if accepted:break
            if not accepted:failures+=1;fresh(route+'-fresh-fallback')
    if len(children)+len(elites)!=target:raise RuntimeError('Target budget mismatch')
    rate=float(rng.uniform(config.mutation_min,config.mutation_max));chosen=rng.choice(len(children),int(round(rate*len(children))),replace=False)
    edits_total=0
    for j in chosen:
        j=int(j);old=children[j];oldkey=program_key(old);keys.remove(oldkey);count=int(rng.integers(1,6))
        for _ in range(config.mate_attempts):
            try:child,edits=mutation(rng,old,count,ops[j].startswith("crossover"))
            except (RuntimeError,ValueError):continue
            key=program_key(child)
            if key!=oldkey and key not in keys:break
        else:raise RuntimeError('Distinct mutation retry budget exhausted')
        keys.add(key);children[j]=child;ops[j]+='_mutation';routes[j].update(mutation_fields=edits,mutation_field_count=len(edits));edits_total+=len(edits)
    born=pedigree.birth(children,generation,parents,ops)
    for serial,route in zip(born,routes):pedigree.records[int(serial)].update(route)
    report=dict(elites=len(elites),global_elites=len(elites),local_champions=0,global_slots=target,local_slots=0,
        fresh_candidates=sum(o.startswith('fresh') for o in ops),crossovers=sum(o.startswith('crossover') for o in ops),mutations=len(chosen),
        mutation_eligible=len(children),mutation_rate=rate,mutation_requested_fields=edits_total,mutation_applied_fields=edits_total,
        champion_crossovers=0,intrasession_crossovers=0,unrelated_mate_fallbacks=failures,rejected_duplicates=duplicates,
        behavior_available=bool(behavior is not None and behavior.available),behavior_exact_queries=behavior.exact_queries if behavior else 0,
        behavior_route_counts={r:sum(x.get('behavior_route')==r for x in routes) for r in ('similar','dissimilar','random')},
        operator_counts={o:sum(x.get('composition_operator')==o for x in routes) for o in set(x.get('composition_operator') for x in routes)},
        sampling=delta(sampling_before),source_population=n,target_population=target,route_budgets=dict(zip(('score','random','elite'),map(int,budgets))))
    return [population[i] for i in elites]+children,np.concatenate((ids[elites],born)),report
