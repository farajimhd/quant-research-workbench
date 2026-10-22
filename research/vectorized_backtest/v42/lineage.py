"""Bounded numeric pedigree; training-only selection and deterministic resume."""
from dataclasses import dataclass, asdict
from hashlib import sha256
import json
import numpy as np
from .genome import sample, mutate, crossover
from .complexity import count
from .schedule import Proposal,SETTINGS,balanced,rank_index,generation_root


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
        if (self.global_fraction,self.global_elite_fraction,self.global_fresh_fraction,self.local_fresh_fraction,self.champion_partners)!=(1.,.1,.2,.2,0):raise ValueError('V42 requires approved global-only reproduction budgets')
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
    root_key=generation_root(rng)
    children=[];parents=[];ops=[];metadata=[];fallbacks=0;duplicates=0;attempts=0;invalid_crossovers=0;no_mates=0
    def fresh(reason):
        nonlocal duplicates
        for _ in range(64):
            context=Proposal(root_key,(generation,len(children),'immigrant',_));m=sample(context,1)[0];key=program_key(m)
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
    from .config import CROSSOVER
    requests={'within':0,'between':0};realized={'within':0,'between':0}
    route_plan=balanced(cross_budget,SETTINGS['mating_weights'],generation)
    sibling_plan=1+balanced(cross_budget,list(CROSSOVER['children_per_pair'].values()),generation)
    action_counts=1+balanced(cross_budget,SETTINGS['action_weights'],generation)
    starts=np.cumsum(action_counts)-action_counts
    modes=balanced(int(action_counts.sum()),SETTINGS['action_mode_weights'],generation)
    sibling_requests={1:0,2:0,3:0};sibling_realized={1:0,2:0,3:0};pairs=set();group=0;cross_slot=0
    for rec,slots in zip(records,quota):
        pool=np.asarray([i for i in species.groups[rec['id']] if valid[i]],dtype=int);pool=pool[np.lexsort((ids[pool],-values[pool]))]
        left=int(slots)
        while left:
            first_slot=cross_slot;route='within' if route_plan[first_slot]==0 else 'between'
            wanted=int(sibling_plan[group]);count=min(wanted,left);left-=count;group+=1;sibling_requests[wanted]+=1;requests[route]+=count
            pair=None
            for attempt in range(config.mate_attempts):
                a=int(pool[(rank_index(len(pool),first_slot,generation)+attempt)%len(pool)]);options=order[mates.unrelated(a)[order]]
                options=options[np.asarray([species.labels[int(i)]==rec['id'] for i in options])==(route=='within')]
                if not len(options):no_mates+=1;continue
                b=int(options[(rank_index(len(options),first_slot,generation,base=3)+attempt)%len(options)]);pair=(a,b);break
            accepted_group=[]
            if pair is not None:pairs.add(tuple(sorted((int(ids[a]),int(ids[b])))))
            for sibling in range(count):
                slot=cross_slot;cross_slot+=1
                if pair is None:fallbacks+=1;fresh(route+'-unrelated-fresh-fallback');continue
                first,second=(a,b) if (sibling+first_slot+generation)%2==0 else (b,a);accepted=False
                offset=int(starts[slot]);width=int(action_counts[slot]);selection=slice(offset,offset+width)
                plan=dict(orientation=0,count=width,action_start=offset,modes=[SETTINGS['action_modes'][int(i)] for i in modes[selection]])
                for attempt in range(config.mate_attempts):
                    attempts+=1
                    try:child,operator=cross(Proposal(root_key,(generation,len(children),'cross',attempt),plan),population[first],population[second])
                    except (ValueError,RuntimeError):invalid_crossovers+=1;continue
                    key=program_key(child)
                    if key in keys:duplicates+=1;continue
                    keys.add(key);children.append(child);parents.append((int(ids[first]),int(ids[second])));ops.append('crossover')
                    metadata.append(dict(route=route,species=rec['id'],crossover=operator,stream_slot=len(children)-1,pair_group=group,sibling_index=sibling,parent_objectives=[float(values[first]),float(values[second])],structural_delta=sum(len(v.nodes) for v in child.networks.values())-sum(len(v.nodes) for v in population[first].networks.values())))
                    accepted_group.append(len(metadata)-1);realized[route]+=1;accepted=True;break
                if not accepted:fallbacks+=1;fresh(route+'-unrelated-fresh-fallback')
            if accepted_group:
                sibling_realized[len(accepted_group)]+=1
                for index in accepted_group:metadata[index]['sibling_count']=len(accepted_group)
    rate=SETTINGS['mutation_fraction']
    if not config.mutation_min<=rate<=config.mutation_max:raise ValueError('Mutation schedule outside approved envelope')
    chosen=np.flatnonzero(balanced(len(children),[rate,1-rate],generation)==0)
    edit_counts=1+balanced(len(chosen),SETTINGS['edit_weights'],generation)
    edit_modes=balanced(int(edit_counts.sum()),[SETTINGS['parameter_fraction'],1-SETTINGS['parameter_fraction']],generation)
    parameter_fields=balanced(int((edit_modes==0).sum()),SETTINGS['parameter_weights'],generation)
    structural_fields=balanced(int((edit_modes==1).sum()),[1]*len(SETTINGS['structural_fields']),generation)
    edit_offset=parameter_offset=structural_offset=0
    structural=0
    for position,j in enumerate(chosen):
        j=int(j);old=children[j];oldkey=program_key(old);keys.remove(oldkey);edits=int(edit_counts[position]);edit_plan=[]
        for e in range(edits):
            mode='parameter' if edit_modes[edit_offset]==0 else 'structure';edit_offset+=1
            if mode=='parameter':
                field=SETTINGS['parameter_fields'][int(parameter_fields[parameter_offset])];parameter_offset+=1
            else:
                field=SETTINGS['structural_fields'][int(structural_fields[structural_offset])];structural_offset+=1
            edit_plan.append(dict(mode=mode,field=field,stage=int(((j+generation*target)//(5**e))%5)))
        for attempt in range(config.mate_attempts):
            try:child,fields=mutation(Proposal(root_key,(generation,j,'mutation',attempt),dict(edits=edit_plan)),old,edits,ops[j]=='crossover')
            except (ValueError,RuntimeError):continue
            key=program_key(child)
            if key!=oldkey and key not in keys:break
        else:raise RuntimeError('Distinct structural/parameter mutation budget exhausted')
        structural_delta=sum(len(v.nodes) for v in child.networks.values())-sum(len(v.nodes) for v in old.networks.values());structural_flag=any(e['field'].startswith('structure:') for e in fields);structural+=int(structural_flag)
        keys.add(key);children[j]=child;ops[j]+='_mutation';metadata[j].update(mutation_fields=fields,mutation_node_delta=structural_delta,structural_mutation=structural_flag)
    born=pedigree.birth(children,generation,parents,ops)
    for serial,meta in zip(born,metadata):pedigree.records[int(serial)].update(meta)
    assert len(selected)+len(children)==target
    telemetry=dict(species.telemetry);telemetry.update(deterministic_allocation=True,stream_root_sha256=root_key.hex(),mutation_edit_counts={str(i):int((edit_counts==i).sum()) for i in range(1,6)},requested_action_modes={name:int((modes==i).sum()) for i,name in enumerate(SETTINGS['action_modes'])},elites=len(selected),fresh_candidates=sum(o.startswith('fresh') for o in ops),crossovers=sum(o.startswith('crossover') for o in ops),mutations=len(chosen),mutation_rate=rate,structural_mutations=structural,unrelated_mate_fallbacks=fallbacks,rejected_duplicates=duplicates,crossover_attempts=attempts,invalid_crossover_attempts=invalid_crossovers,no_unrelated_options=no_mates,mating_requests=requests,mating_realized=realized,species_offspring={str(r['id']):int(q) for r,q in zip(records,quota)},global_slots=target,local_slots=0,source_population=n,target_population=target)
    # Already-evaluated offspring survival uses birth annotations, no replay work.
    evaluated=[pedigree.records[int(i)] for i in ids];structural_births=[r for r in evaluated if r.get('structural_mutation',False) or r.get('structural_delta',0)]
    elite_ids=set(int(ids[i]) for i in selected)
    telemetry['structural_survival']=dict(evaluated=len(structural_births),elite_survivors=sum(r['serial'] in elite_ids for r in structural_births))
    telemetry.update(sibling_requests=sibling_requests,sibling_realized=sibling_realized,distinct_parent_pairs=len(pairs))
    accepted=[m['crossover'] for m in metadata if 'crossover' in m]
    telemetry['accepted_mask_modes']={mode:sum(a.get('mode')=='mask:'+mode for m in accepted for a in m['actions']) for mode in SETTINGS['action_modes']}
    telemetry.update(outcome_telemetry(evaluated,values,ordered,elite_ids,species.descriptors,generation))
    species.telemetry=telemetry
    return [population[i] for i in selected]+children,np.concatenate((ids[selected],born)),telemetry


def outcome_telemetry(evaluated,values,ordered,elite_ids,descriptors,generation):
    """Pure observer: existing training metrics only; no RNG or selection writes."""
    telemetry={}
    outcomes={};sibling_groups={};total_profits=np.asarray([m['net_pnl'] for m in ordered],dtype=float).sum(axis=0)
    for i,r in enumerate(evaluated):
        if r.get('generation')!=generation-1:continue
        tags=set(a.get('join',a['mode']) for a in r.get('crossover',{}).get('actions',[]))
        tags.update(e['field'] for e in r.get('mutation_fields',[]))
        for tag in tags:
            stat=outcomes.setdefault(tag,dict(evaluated=0,profitable=0,elite_survivors=0,beat_best_parent=0,parent_comparisons=0))
            stat['evaluated']+=1;stat['profitable']+=int(total_profits[i]>0);stat['elite_survivors']+=int(r['serial'] in elite_ids)
            if r.get('parent_objectives'):stat['parent_comparisons']+=1;stat['beat_best_parent']+=int(values[i]>max(r['parent_objectives']))
        if r.get('sibling_count',0)>1:sibling_groups.setdefault(r['pair_group'],[]).append(i)
    telemetry['operator_outcomes']=outcomes
    differences=[]
    for indexes in sibling_groups.values():
        first=np.asarray(descriptors[indexes[0]]['behavior'])
        for i in indexes[1:]:
            other=np.asarray(descriptors[i]['behavior']);differences.append(float(np.mean(abs(other-first)/np.maximum(1,np.maximum(abs(other),abs(first))))))
    telemetry['sibling_survival']=dict(evaluated=sum(map(len,sibling_groups.values())),elite_survivors=sum(evaluated[i]['serial'] in elite_ids for indexes in sibling_groups.values() for i in indexes),behavior_comparisons=len(differences),mean_behavior_distance=float(np.mean(differences)) if differences else None)
    return telemetry
