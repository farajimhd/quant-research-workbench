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
    fresh_fraction:float=.2
    champion_partners:int=5
    mate_attempts:int=8

    def validate(self):
        if not 1<=self.ancestry_generations<=8 or not 1<=self.mate_attempts<=32:
            raise ValueError('Bounded pedigree/mate search required')
        if not 0<=self.fresh_fraction<1 or not 0<=self.champion_partners<=30:
            raise ValueError('Invalid selection allocation')
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
        mutations=np.asarray([r['mutations'] for r in self.records]+[0],dtype=np.int64)[primary]+(np.asarray(operations)=='mutation')
        crosses=np.asarray([r['crossovers'] for r in self.records]+[0],dtype=np.int64)[primary]+(np.asarray(operations)=='crossover')
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


def next_population(rng,population,ids,pedigree,rank,generation,config=Selection(),*,session_values,session_eligible):
    config.validate();n=len(population)
    order,valid=session_order(session_values,session_eligible,ids);days=len(order)
    fresh=int(n*config.fresh_fraction);budget=n-fresh
    if budget<days:raise ValueError('Population must support one session allocation each')
    width=max(1,budget//days)
    pools=order[:,:width];pool_valid=valid[:,:width]
    active=valid.any(1);champions=order[:,0]
    elite=[];keys=set()
    for a in champions[active]:
        key=pedigree.records[int(ids[a])]['program_sha256']
        if key not in keys:elite.append(int(a));keys.add(key)
    children=[];parents=[];ops=[];routes=[];uses=np.zeros(n,dtype=np.int64)
    rejected=failed=champion_children=general_children=0
    mates=MateIndex(pedigree,ids)
    # Random cycles cover each pool before reusing a first parent.
    cycles=[rng.permutation(pools[s,pool_valid[s]]).tolist() for s in range(days)]
    cursors=np.zeros(days,dtype=np.int64);partner_uses=np.zeros(days,dtype=np.int64)

    def admit(a=None,b=None,source_session=None,partner_session=None,route='fresh'):
        nonlocal rejected
        child=sample(rng,1)[0] if a is None else crossover(rng,population[a],population[b])
        key=program_key(child)
        if key in keys:rejected+=1;return False
        keys.add(key);children.append(child)
        parents.append((-1,-1) if a is None else (int(ids[a]),int(ids[b])))
        ops.append('fresh' if a is None else 'crossover')
        routes.append(dict(selection_route=route,source_session=source_session,partner_session=partner_session))
        if a is not None:uses[a]+=1;uses[b]+=1
        return True

    # Distinct random partner sessions for each champion, ordered offspring slots.
    champion_budget=min(days*config.champion_partners,budget-len(elite))
    attempted=0
    for s in np.roll(np.arange(days),-(generation-2)%days):
        if not active[s]:continue
        a=int(champions[s]);eligible=np.flatnonzero(active & (np.arange(days)!=s) & mates.unrelated(a)[champions])
        for t in rng.permutation(eligible)[:config.champion_partners]:
            if attempted>=champion_budget:break
            attempted+=1
            if admit(a,int(champions[t]),int(s),int(t),'champion'):champion_children+=1
    transferred=champion_budget-champion_children
    general_budget=budget-len(elite)-champion_children
    slots=np.resize(np.roll(np.arange(days),-(generation-2)%days),general_budget)
    for s in slots:
        accepted=False
        for attempt in range(config.mate_attempts):
            if not cycles[s]:break
            if cursors[s]>=len(cycles[s]):
                cycles[s]=rng.permutation(pools[s,pool_valid[s]]).tolist();cursors[s]=0
            a=int(cycles[s][cursors[s]]);cursors[s]+=1
            eligible=pool_valid & mates.unrelated(a)[pools];eligible[s]=False
            sessions=np.flatnonzero(eligible.any(1))
            if not len(sessions):continue
            least=sessions[partner_uses[sessions]==partner_uses[sessions].min()]
            t=int(rng.choice(least));b=int(rng.choice(pools[t,eligible[t]]));partner_uses[t]+=1
            if admit(a,b,int(s),t,'general'):accepted=True;general_children+=1;break
        if not accepted:failed+=1
    # Missing mates/duplicates become explicitly counted immigrants, never mutations.
    attempts=0
    while len(children)+len(elite)<n:
        attempts+=1
        if attempts>n*32:raise ValueError('Unique immigrant budget exhausted')
        admit()
    born=pedigree.birth(children,generation,parents,ops)
    for serial,route in zip(born,routes):pedigree.records[int(serial)].update(route)
    return [population[i] for i in elite]+children,np.concatenate((ids[elite],born)),dict(
        elites=len(elite),fresh_candidates=ops.count('fresh'),mutations=0,crossovers=ops.count('crossover'),
        champion_crossovers=champion_children,general_crossovers=general_children,
        champion_slots_transferred=transferred,unrelated_mate_fallbacks=failed,rejected_duplicates=rejected,
        nominal_fresh_candidates=fresh,extra_immigrants=ops.count('fresh')-fresh,
        session_parent_pool_width=width,empty_session_pools=int((~active).sum()),
        unique_parent_draws=int((uses>0).sum()),maximum_parent_draws=int(uses.max()))
