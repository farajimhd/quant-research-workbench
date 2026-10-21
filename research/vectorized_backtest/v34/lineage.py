"""Bounded numeric pedigree; training-only selection and deterministic resume."""
from dataclasses import dataclass, asdict
from hashlib import sha256
import json
import numpy as np
from .genome import sample, mutate, crossover
from .complexity import count


def program_key(member):
    from .identity_cache import program_key as cached_key
    cached=cached_key(member)
    if cached is not None:return cached
    from .semantics import identity
    value=identity(member)
    return sha256(json.dumps(value,sort_keys=True,separators=(',',':')).encode()).hexdigest()


@dataclass(frozen=True)
class Selection:
    ancestry_generations:int=3
    global_fraction:float=1.
    global_elite_fraction:float=.1
    global_fresh_fraction:float=.2
    local_fresh_fraction:float=.2
    champion_partners:int=0
    mate_attempts:int=8
    mutation_min:float=.20
    mutation_max:float=.85

    behavior_pairing:bool=True

    def validate(self):
        if type(self.behavior_pairing) is not bool:raise ValueError('Invalid pairing settings')
        if (self.global_fraction,self.global_elite_fraction,self.global_fresh_fraction,self.local_fresh_fraction,self.champion_partners)!=(1.,.1,.2,.2,0):raise ValueError('V34 requires approved global-only reproduction budgets')
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
            from .family_stats import annotate
            annotate(record,[self.records[int(p)] for p in pair if p>=0])
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




def next_population(rng,population,ids,pedigree,rank,generation,config=Selection(),*,session_values,session_eligible,global_values,global_eligible,target_size=None,behavior=None,species=None,ordered=None):
    """Net-profit species allocation, strict ancestry, exact reproducible budgets."""
    from .species import Species
    from .composition import cross,mutation
    config.validate();n=len(population);target=n if target_size is None else int(target_size)
    if not 10<=target<=65536:raise ValueError('Bounded population required')
    values=np.asarray(global_values);valid=np.asarray(global_eligible,dtype=bool)
    if values.shape!=(n,) or not np.isfinite(values).all() or not valid.any():raise ValueError('Finite training profits required')
    if ordered is None:raise ValueError('Species require complete training episode descriptors')
    species=Species() if species is None else species;species.assign(population,ids,values,ordered,generation-1,eligible=valid)
    order=np.lexsort((ids,-values));order=order[valid[order]];best=int(order[0]);mates=MateIndex(pedigree,ids)
    records=[r for r in species.state['records'] if not r['stagnant'] or species.labels[best]==r['id']]
    records=[r for r in records if any(valid[i] for i in species.groups[r['id']])]
    elite_budget=min(int(target*.1),len(order));fresh_budget=int(np.ceil(target*.2));cross_budget=target-elite_budget-fresh_budget
    champions=sorted((min((i for i in species.groups[r['id']] if valid[i]),key=lambda i:(-values[i],ids[i])) for r in records),key=lambda i:(-values[i],ids[i]))
    selected=champions[:min(len(champions),elite_budget//2)];selected=list(dict.fromkeys(selected+[int(i) for i in order]))[:elite_budget]
    keys={program_key(population[i]) for i in selected}
    children=[];parents=[];ops=[];metadata=[];fallbacks=0;duplicates=0;attempts=0;invalid_crossovers=0;no_mates=0
    def fresh(reason):
        nonlocal duplicates
        for _ in range(64):
            m=sample(rng,1)[0];key=program_key(m)
            if key not in keys:break
            duplicates+=1
        else:raise RuntimeError('Unique fresh candidate attempts exhausted')
        keys.add(key);children.append(m);parents.append((-1,-1));ops.append('fresh');metadata.append(dict(route=reason))
    for _ in range(fresh_budget):fresh('fresh')
    # One-slot species floor, then profit-rank-weighted largest-remainder allocation.
    records.sort(key=lambda r:(-values[min((i for i in species.groups[r['id']] if valid[i]),key=lambda i:(-values[i],ids[i]))],r['id']))
    records=records[:cross_budget]
    count=len(records);quota=np.ones(count,dtype=int);weights=np.arange(count,0,-1,dtype=float)
    extra=(cross_budget-count)*weights/weights.sum();quota+=np.floor(extra).astype(int)
    for k in np.argsort(-(extra-np.floor(extra)),kind='stable')[:cross_budget-int(quota.sum())]:quota[k]+=1
    requests={'within':0,'between':0};realized={'within':0,'between':0}
    for rec,slots in zip(records,quota):
        pool=np.asarray([i for i in species.groups[rec['id']] if valid[i]],dtype=int);pool=pool[np.lexsort((ids[pool],-values[pool]))]
        for _ in range(int(slots)):
            route='within' if rng.random()<.8 else 'between';requests[route]+=1;accepted=False
            for _ in range(config.mate_attempts):
                a=int(pool[int(rng.integers(len(pool),size=3).min())]);options=order[mates.unrelated(a)[order]]
                options=options[np.asarray([species.labels[int(i)]==rec['id'] for i in options])==(route=='within')]
                if not len(options):no_mates+=1;continue
                b=int(options[int(rng.integers(len(options),size=3).min())]);attempts+=1
                try:child,operator=cross(rng,population[a],population[b])
                except (ValueError,RuntimeError):invalid_crossovers+=1;continue
                key=program_key(child)
                if key in keys:duplicates+=1;continue
                keys.add(key);children.append(child);parents.append((int(ids[a]),int(ids[b])));ops.append('crossover');metadata.append(dict(route=route,species=rec['id'],structural_delta=sum(len(v.nodes) for v in child.networks.values())-sum(len(v.nodes) for v in population[a].networks.values())));realized[route]+=1;accepted=True;break
            if not accepted:fallbacks+=1;fresh(route+'-unrelated-fresh-fallback')
    rate=float(rng.uniform(config.mutation_min,config.mutation_max));chosen=rng.choice(len(children),int(round(rate*len(children))),replace=False)
    structural=0
    for j in chosen:
        j=int(j);old=children[j];oldkey=program_key(old);keys.remove(oldkey);edits=int(rng.integers(1,6))
        for _ in range(config.mate_attempts):
            try:child,fields=mutation(rng,old,edits,ops[j]=='crossover')
            except (ValueError,RuntimeError):continue
            key=program_key(child)
            if key!=oldkey and key not in keys:break
        else:raise RuntimeError('Distinct structural/parameter mutation budget exhausted')
        structural_delta=sum(len(v.nodes) for v in child.networks.values())-sum(len(v.nodes) for v in old.networks.values());structural_flag=any(e['field'].startswith('structure:') for e in fields);structural+=int(structural_flag)
        keys.add(key);children[j]=child;ops[j]+='_mutation';metadata[j].update(mutation_fields=fields,mutation_node_delta=structural_delta,structural_mutation=structural_flag)
    born=pedigree.birth(children,generation,parents,ops)
    for serial,meta in zip(born,metadata):pedigree.records[int(serial)].update(meta)
    assert len(selected)+len(children)==target
    telemetry=dict(species.telemetry);telemetry.update(elites=len(selected),fresh_candidates=sum(o.startswith('fresh') for o in ops),crossovers=sum(o.startswith('crossover') for o in ops),mutations=len(chosen),mutation_rate=rate,structural_mutations=structural,unrelated_mate_fallbacks=fallbacks,rejected_duplicates=duplicates,crossover_attempts=attempts,invalid_crossover_attempts=invalid_crossovers,no_unrelated_options=no_mates,mating_requests=requests,mating_realized=realized,species_offspring={str(r['id']):int(q) for r,q in zip(records,quota)},global_slots=target,local_slots=0,source_population=n,target_population=target)
    # Already-evaluated offspring survival uses birth annotations, no replay work.
    evaluated=[pedigree.records[int(i)] for i in ids];structural_births=[r for r in evaluated if r.get('structural_mutation',False) or r.get('structural_delta',0)]
    elite_ids=set(int(ids[i]) for i in selected)
    telemetry['structural_survival']=dict(evaluated=len(structural_births),elite_survivors=sum(r['serial'] in elite_ids for r in structural_births))
    species.telemetry=telemetry
    return [population[i] for i in selected]+children,np.concatenate((ids[selected],born)),telemetry
