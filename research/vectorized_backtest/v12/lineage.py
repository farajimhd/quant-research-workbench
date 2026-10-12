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
    elite_fraction:float=.1
    related_elite_fraction:float=.1
    fresh_fraction:float=.2
    session_fraction:float=.4
    epsilon_mad_multiplier:float=1.0
    parent_draw_limit:int=16
    crossover_fraction:float=.3
    mate_attempts:int=8

    def validate(self):
        if not 1<=self.ancestry_generations<=8 or not 1<=self.mate_attempts<=32:
            raise ValueError('Bounded pedigree/mate search required')
        if any(not 0<=x<=1 for x in (self.elite_fraction,self.related_elite_fraction,self.fresh_fraction,self.crossover_fraction,self.session_fraction)) or self.elite_fraction+self.fresh_fraction+self.session_fraction>1:
            raise ValueError('Invalid selection fractions')
        if not np.isfinite(self.epsilon_mad_multiplier) or self.epsilon_mad_multiplier<0 or not isinstance(self.parent_draw_limit,int) or self.parent_draw_limit<1:
            raise ValueError('Invalid session-selection bounds')
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
            record=dict(serial=serial,generation=generation,parents=pair.tolist(),operation=str(op),
                mutations=int(mutations[j]),crossovers=int(crosses[j]),ancestors=sorted(ancestors.items()),
                program_sha256=key,complexity=count(member),
                label=f'G{generation:03d}-{serial:06d}-M{mutations[j]:03d}-X{crosses[j]:03d}-{key[:4]}')
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


class ParentSelector:
    """Small CPU training panel; tolerances computed once per generation."""
    def __init__(self,rng,rank,session_pnl,config):
        self.rng=rng;self.rank=np.asarray(rank,dtype=np.int64);self.config=config
        panel=np.asarray(session_pnl,dtype=np.float64)
        if panel.ndim!=2 or not panel.shape[0] or not np.isfinite(panel).all() or not len(self.rank):
            raise ValueError('Finite nonempty training session panel required')
        self.pnl=panel[:,self.rank]
        median=np.median(self.pnl,axis=1,keepdims=True)
        self.epsilon=config.epsilon_mad_multiplier*np.median(np.abs(self.pnl-median),axis=1)
        self.uses=np.zeros(len(self.rank),dtype=np.int64);self.relaxations=0

    def draw(self,session=False):
        pool=np.flatnonzero(self.uses<self.config.parent_draw_limit)
        if not len(pool):
            # No rejection loop: choose among least-used eligible candidates.
            self.relaxations+=1;pool=np.flatnonzero(self.uses==self.uses.min())
        if session:
            for day in self.rng.permutation(self.pnl.shape[0]):
                values=self.pnl[day,pool]
                pool=pool[values>=values.max()-self.epsilon[day]]
                if len(pool)==1:break
            chosen=int(pool[0]) # rank order breaks final ties by overall score
        else:
            chosen=int(pool[self.rng.integers(0,len(pool),size=3).min()])
        self.uses[chosen]+=1
        return int(self.rank[chosen])


def next_population(rng,population,ids,pedigree,rank,generation,config=Selection(),*,session_pnl=None):
    config.validate();n=len(population)
    elite_count=max(1,int(n*config.elite_fraction));related_limit=int(elite_count*config.related_elite_fraction)
    # Score order is authoritative; newer wins only exact score ties (caller ordering).
    elite=[];deferred=[];keys=set()
    independent_limit=max(1,elite_count-related_limit)
    for i in rank:
        key=pedigree.records[int(ids[i])]['program_sha256']
        if key in keys:continue
        if elite and pedigree.relatives(ids[i],ids[elite],generation).any():deferred.append(i);continue
        if len(elite)<independent_limit:elite.append(i);keys.add(key)
        if len(elite)>=independent_limit and len(deferred)>=related_limit:break
    for i in deferred[:related_limit]:
        if len(elite)>=elite_count:break
        key=pedigree.records[int(ids[i])]['program_sha256']
        if key not in keys:elite.append(i);keys.add(key)
    children=[];parents=[];ops=[];failed_mates=duplicates=0
    fresh_count=max(1,int(n*config.fresh_fraction))
    panel=np.asarray(session_pnl,dtype=np.float64)
    if panel.ndim!=2 or panel.shape[1]!=n:raise ValueError('Training panel must match population')
    selector=ParentSelector(rng,rank,panel,config)
    session_count=int(n*config.session_fraction)
    session_children=overall_children=0
    attempts=0
    while len(children)<n-len(elite):
        attempts+=1
        if attempts>n*32:raise ValueError('Unique offspring budget exhausted')
        if len(children)<fresh_count:
            child=sample(rng,1)[0];pair=(-1,-1);op='fresh'
        else:
            session_route=session_children<session_count
            a=selector.draw(session_route);b=None
            if rng.random()<config.crossover_fraction:
                pool=np.asarray([selector.draw(session_route) for _ in range(config.mate_attempts)])
                eligible=pool[~pedigree.relatives(ids[a],ids[pool],generation)]
                if len(eligible):b=int(eligible[0])
                else:failed_mates+=1
            if b is None:child=mutate(rng,population[a]);pair=(int(ids[a]),-1);op='mutation'
            else:child=crossover(rng,population[a],population[b]);pair=(int(ids[a]),int(ids[b]));op='crossover'
        key=program_key(child)
        if key in keys:duplicates+=1;continue
        if op!='fresh':
            if session_route:session_children+=1
            else:overall_children+=1
        keys.add(key);children.append(child);parents.append(pair);ops.append(op)
    born=pedigree.birth(children,generation,parents,ops)
    return [population[i] for i in elite]+children,np.concatenate((ids[elite],born)),dict(
        elites=len(elite),fresh_candidates=ops.count('fresh'),mutations=ops.count('mutation'),
        crossovers=ops.count('crossover'),unrelated_mate_fallbacks=failed_mates,rejected_duplicates=duplicates,
        session_selected_offspring=session_children,overall_selected_offspring=overall_children,
        parent_limit_relaxations=selector.relaxations,unique_parent_draws=int((selector.uses>0).sum()),
        maximum_parent_draws=int(selector.uses.max()))
