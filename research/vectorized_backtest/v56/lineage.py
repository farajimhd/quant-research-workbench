"""Bounded numeric pedigree; training-only selection and deterministic resume."""
from dataclasses import dataclass, asdict
from hashlib import sha256
import json
import numpy as np
from .genome import sample, mutate, crossover,STAGES
from research.vectorized_backtest.v42.complexity import count
from research.vectorized_backtest.v42.schedule import Proposal,SETTINGS,balanced,rank_index,generation_root


def program_key(member):
    from .identity_cache import program_key as cached_key
    cached=cached_key(member)
    if cached is not None:return cached
    from research.vectorized_backtest.v42.semantics import identity
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

    profit_fresh_fraction:float=0.
    behavior_pairing:bool=True

    def validate(self):
        if self.profit_fresh_fraction not in (0.,.25):raise ValueError('Unsupported profit retention fraction')
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
            from research.vectorized_backtest.v42.family_stats import annotate
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




def next_population(rng,population,ids,pedigree,rank,generation,config=Selection(),*,session_values,session_eligible,global_values,global_eligible,target_size=None,behavior=None,species=None,ordered=None,case_values=None,case_ids=None,probe=None):
    """One score pool in mixed phase; strictly owned case pools in phase0."""
    from dataclasses import replace
    from .species import Species
    from .composition import cross,mutation
    from .whole_network import cross as whole_cross
    from .ownership import quotas,round_robin
    from .allocation import budgets
    from .config import CROSSOVER
    config.validate();n=len(population);target=n if target_size is None else int(target_size)
    values=np.asarray(global_values,float);valid=np.asarray(global_eligible,bool)
    if values.shape!=(n,) or valid.shape!=(n,) or not np.isfinite(values).all():raise ValueError('Finite aligned selection scores required')
    owners=[m.case_owner for m in population];isolation=any(x is not None for x in owners)
    if isolation:raise ValueError('V56 forbids ticker-owned reproduction')
    if isolation and (any(x is None for x in owners) or not set(owners)<=set(case_ids)):raise ValueError('Incomplete phase0 ownership')
    labels=owners if isolation else ['SESSION']*n
    cases=sorted(case_ids) if isolation else ['SESSION']
    active=np.asarray([m['closed_positions'] for m in ordered],float).sum(axis=0)>0
    if active.shape!=(n,):raise ValueError('Aligned training activity required')
    groups={c:sorted([i for i in range(n) if labels[i]==c and valid[i] and active[i]],key=lambda i:(-values[i],ids[i])) for c in cases}
    groups={c:p for c,p in groups.items() if p}
    allocation=quotas(cases,groups,target,generation) if isolation else {'SESSION':target}
    remaining=dict(allocation)
    budget=budgets(target);elite_budget=budget['elites'];profit_budget=int(budget['fresh']*config.profit_fresh_fraction);fresh_budget=budget['fresh']-profit_budget;cross_budget=budget['crossovers']
    order=round_robin(groups,generation) if isolation else groups.get('SESSION',[])
    selected=[];keys=set()
    for i in order:
        key=program_key(population[i]);case=labels[i]
        if key in keys or remaining[case]<=0:continue
        selected.append(i);keys.add(key);remaining[case]-=1
        if len(selected)==elite_budget:break
    score_selected=list(selected);profit_selected=[]
    net=np.asarray([m['net_pnl'] for m in ordered],float).sum(axis=0) if profit_budget else np.zeros(n)
    for i in sorted(groups.get('SESSION',[]),key=lambda i:(-net[i],ids[i])):
        if len(profit_selected)>=profit_budget:break
        key=program_key(population[i])
        if key in keys:continue
        selected.append(i);profit_selected.append(i);keys.add(key);remaining['SESSION']-=1
    species=Species() if species is None else species;species.assign(population,ids,values,ordered,generation-1,eligible=valid)
    mates=MateIndex(pedigree,ids);root_key=generation_root(rng)
    children=[];parents=[];ops=[];metadata=[];duplicates=0;fallbacks=0;invalid_crossovers=0;whole_rejections={}
    def fresh(reason,case=None):
        nonlocal duplicates
        if case is None:
            available=[c for c in cases if remaining[c]>0]
            case=available[(generation+len(children))%len(available)]
        if remaining[case]<=0:raise ValueError('Case allocation exhausted')
        for attempt in range(64):
            from .initial_population import immigrant
            m=immigrant(Proposal(root_key,(generation,len(children),'immigrant',attempt)),probe)
            if isolation:m=replace(m,case_owner=case).validate()
            key=program_key(m)
            if key not in keys:break
            duplicates+=1
        else:raise RuntimeError('Unique fresh candidate attempts exhausted')
        keys.add(key);children.append(m);parents.append((-1,-1));ops.append('fresh');remaining[case]-=1
        metadata.append(dict(route=reason,case=case if isolation else None,case_owner=m.case_owner))
    for _ in range(fresh_budget):fresh('fresh')
    for _ in range(elite_budget-len(score_selected)):fresh('insufficient-active-elites')
    for _ in range(profit_budget-len(profit_selected)):fresh('insufficient-active-profit-leaders')
    def parent_mutation(pool,case,slot,reason):
        if not pool:
            fresh('no-active-eligible-parent',case);return
        for attempt in range(config.mate_attempts):
            parent=pool[(rank_index(len(pool),slot,generation)+attempt)%len(pool)]
            try:
                child,fields=mutation(Proposal(root_key,(generation,len(children),'parent-mutation',attempt)),population[parent],1+slot%5)
            except (ValueError,RuntimeError):continue
            key=program_key(child)
            if key in keys:continue
            keys.add(key);children.append(child);parents.append((int(ids[parent]),-1));ops.append('parent_mutation');remaining[case]-=1
            metadata.append(dict(route=reason,case=None,case_owner=None,mutation_fields=fields,parent_objectives=[float(values[parent])],
                structural_mutation=any(f['field'].startswith('structure:') for f in fields)))
            return
        raise RuntimeError('Distinct parent-mutation offspring attempts exhausted')
    from .ticker_selection import specialist_pools
    local_pools=specialist_pools(ordered,valid,active,ids) if ordered and 'ticker_score' in ordered[0] else {}
    if (valid&active).any() and not local_pools:raise ValueError('Ticker specialist scores required for V56 reproduction')
    ticker_names=list(local_pools)
    parent_plan=balanced(2*cross_budget,[.25,.75],generation)==0
    local_parent_selections=0;global_parent_selections=0
    def parent_pool(slot,which,default):
        if parent_plan[2*slot+which]:return local_pools[ticker_names[(2*slot+which+generation)%len(ticker_names)]]
        return default
    budget_filtered=0
    whole_plan=balanced(cross_budget,[.25,.75],generation)==0
    whole_modes=balanced(int(whole_plan.sum()),[.4,.2,.2,.2],generation)
    route_plan=balanced(cross_budget,SETTINGS['mating_weights'],generation)
    sibling_plan=1+balanced(cross_budget,list(CROSSOVER['children_per_pair'].values()),generation)
    action_counts=1+balanced(cross_budget,SETTINGS['action_weights'],generation)
    starts=np.cumsum(action_counts)-action_counts
    modes=balanced(int(action_counts.sum()),SETTINGS['action_mode_weights'],generation)
    cross_slot=0;whole_offset=0;group=0;sibling_requests={1:0,2:0,3:0};sibling_realized={1:0,2:0,3:0}
    for case,slots in list(remaining.items()):
        left=slots;pool=groups.get(case,[])
        while left:
            slot=cross_slot;whole=bool(whole_plan[slot]);wanted=1 if whole else int(sibling_plan[group]);count=min(wanted,left)
            # Do not let a sibling group consume a separately scheduled whole slot.
            for j in range(1,count):
                if whole_plan[slot+j]!=whole:count=j;break
            sibling_requests[wanted]+=1;group+=1;left-=count;route='within' if route_plan[slot]==0 else 'between';pair=None
            for attempt in range(config.mate_attempts):
                if not pool:break
                first_pool=parent_pool(slot,0,pool)
                a=int(first_pool[(rank_index(len(first_pool),slot,generation)+attempt)%len(first_pool)])
                options=[i for i in parent_pool(slot,1,pool) if mates.unrelated(a)[i] and (species.labels[i]==species.labels[a])==(route=='within')]
                if whole:
                    from .whole_network import fits
                    stages=(STAGES[(slot+generation)%len(STAGES)],)
                    before=len(options)
                    options=[i for i in options if (fits(population[a],population[i],stages) if generation%2==0 else fits(population[i],population[a],stages))]
                    budget_filtered+=before-len(options)
                if options:
                    b=int(options[(rank_index(len(options),slot,generation,base=3)+attempt)%len(options)]);pair=(a,b);pair_local=(bool(parent_plan[2*slot]),bool(parent_plan[2*slot+1]));break
            accepted_group=[]
            for sibling in range(count):
                slot=cross_slot;cross_slot+=1;whole=bool(whole_plan[slot])
                whole_mode=('union','intersection','xor','xnor')[int(whole_modes[whole_offset])] if whole else None
                if whole:whole_offset+=1
                if pair is None:fallbacks+=1;parent_mutation(pool,case,slot,'no-compatible-size-feasible-parent');continue
                local_parent_selections+=sum(pair_local)
                global_parent_selections+=2-sum(pair_local)
                first,second=pair if (sibling+generation)%2==0 else pair[::-1]
                offset=int(starts[slot]);width=int(action_counts[slot]);plan=dict(orientation=0,count=width,action_start=offset,modes=[SETTINGS['action_modes'][int(i)] for i in modes[offset:offset+width]])
                accepted=False
                for attempt in range(config.mate_attempts):
                    try:
                        child,operator=whole_cross(population[first],population[second],whole_mode,stages=(STAGES[(slot+generation)%len(STAGES)],)) if whole else cross(Proposal(root_key,(generation,len(children),'cross',attempt),plan),population[first],population[second])
                    except (ValueError,RuntimeError) as exc:
                        invalid_crossovers+=1
                        if whole:whole_rejections[str(exc)]=whole_rejections.get(str(exc),0)+1;break
                        continue
                    if child.case_owner!=(case if isolation else None):raise ValueError('Crossover owner leakage')
                    key=program_key(child)
                    if key in keys:duplicates+=1;continue
                    keys.add(key);children.append(child);parents.append((int(ids[first]),int(ids[second])));ops.append('crossover');remaining[case]-=1
                    metadata.append(dict(route=route,selection_route='owned-case' if isolation else 'unified-session',case=case if isolation else None,case_owner=child.case_owner,
                        parent_selection_sources=['ticker' if x else 'global' for x in (pair_local if (sibling+generation)%2==0 else pair_local[::-1])],crossover=operator,whole_network=whole,whole_mode=whole_mode,pair_group=group,sibling_index=sibling,parent_objectives=[float(values[first]),float(values[second])],
                        structural_delta=sum(len(v.nodes) for v in child.networks.values())-sum(len(v.nodes) for v in population[first].networks.values())))
                    accepted_group.append(len(metadata)-1);accepted=True;break
                if not accepted:fallbacks+=1;parent_mutation(pool,case,slot,'invalid-or-duplicate-child')
            if accepted_group:
                sibling_realized[len(accepted_group)]+=1
                for i in accepted_group:metadata[i]['sibling_count']=len(accepted_group)
    assert cross_slot==cross_budget and whole_offset==len(whole_modes) and not any(remaining.values())
    rate=SETTINGS['mutation_fraction']
    if not config.mutation_min<=rate<=config.mutation_max:raise ValueError('Mutation schedule outside approved envelope')
    mutation_pool=np.asarray([i for i,o in enumerate(ops) if o=='fresh'],dtype=int)
    chosen=mutation_pool[balanced(len(mutation_pool),[rate,1-rate],generation)==0]
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
        last_error='duplicate mutation'
        for attempt in range(config.mate_attempts):
            try:child,fields=mutation(Proposal(root_key,(generation,j,'mutation',attempt),dict(edits=edit_plan)),old,edits,ops[j]=='crossover')
            except (ValueError,RuntimeError) as exc:last_error=str(exc);continue
            key=program_key(child)
            if key!=oldkey and key not in keys:break
        else:raise RuntimeError(f'Distinct structural/parameter mutation budget exhausted: slot {j}, edits {edit_plan}, last error {last_error}')
        structural_delta=sum(len(v.nodes) for v in child.networks.values())-sum(len(v.nodes) for v in old.networks.values());structural_flag=any(e['field'].startswith('structure:') for e in fields);structural+=int(structural_flag)
        keys.add(key);children[j]=child;ops[j]+='_mutation';metadata[j].update(mutation_fields=fields,mutation_node_delta=structural_delta,structural_mutation=structural_flag)
    for child,meta in zip(children,metadata):
        if child.case_owner!=meta['case_owner']:raise ValueError('Mutation owner leakage')
    born=pedigree.birth(children,generation,parents,ops)
    for serial,meta in zip(born,metadata):pedigree.records[int(serial)].update(meta)
    result=[population[i] for i in selected]+children;newids=np.concatenate((ids[selected],born))
    assert len(result)==target
    telemetry=dict(species.telemetry,selection_policy='isolated-case-score' if isolation else 'unified-session-score',
        ticker_parent_fraction=.25,ticker_specialist_tickers=len(local_pools),ticker_parent_selections=local_parent_selections,global_parent_selections=global_parent_selections,active_parent_candidates=int((valid&active).sum()),reproduction_policy='v56-ranked-active-risk-cost-v2',
        requested_elites=elite_budget,elites=len(score_selected),profit_elites=len(profit_selected),requested_profit_elites=profit_budget,score_elite_ids=[int(ids[i]) for i in score_selected],profit_elite_ids=[int(ids[i]) for i in profit_selected],baseline_fresh=fresh_budget,requested_crossovers=cross_budget,
        fresh_candidates=sum(o.startswith('fresh') for o in ops),crossovers=sum('crossover' in o for o in ops),
        parent_mutations=ops.count('parent_mutation'),post_crossover_mutation=False,
        mutations=len(chosen)+ops.count('parent_mutation'),mutation_rate=rate,structural_mutations=structural+sum(m.get('structural_mutation',False) for o,m in zip(ops,metadata) if o=='parent_mutation'),rejected_duplicates=duplicates,
        invalid_crossover_attempts=invalid_crossovers,fallbacks=fallbacks,case_quotas=allocation,ownership_verified=True,
        sibling_requests=sibling_requests,sibling_realized=sibling_realized,
        whole_network=dict(requested=int(whole_plan.sum()),accepted=sum(m.get('whole_network',False) for m in metadata),rejected_reasons=whole_rejections,budget_filtered_mate_options=budget_filtered,composed_actions_per_child=1,
            requested_modes={k:int((whole_modes==i).sum()) for i,k in enumerate(('union','intersection','xor','xnor'))}),
        accepted_mask_modes={mode:sum(a.get('mode')=='mask:'+mode for m in metadata for a in m.get('crossover',{}).get('actions',[])) for mode in SETTINGS['action_modes']})
    if isolation:
        counts={c:sum(m.case_owner==c for m in result) for c in cases}
        if counts!=allocation:raise ValueError('Case allocation mismatch')
        for child,pair in zip(children,parents):
            if any(p>=0 and pedigree.records[int(p)].get('case_owner')!=child.case_owner for p in pair):raise ValueError('Cross-case ancestry leakage')
    telemetry.update(outcome_telemetry([pedigree.records[int(i)] for i in ids],values,ordered,set(map(int,ids[selected])),species.descriptors,generation))
    species.telemetry=telemetry
    return result,newids,telemetry


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
