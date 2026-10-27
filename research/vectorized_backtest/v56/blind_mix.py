"""Bounded pre-evaluation multi-session composition; no fitness or validation reads."""
from dataclasses import replace
from collections import Counter
import numpy as np
from .genome import Individual,Network,Neuron,STAGES,INPUTS,MARKET_INPUTS,reachable
from .whole_network import network as compose
from .lineage import program_key
from .validation_cache import validation_scope

VERSION='v56-blind-mix-v1'
GROUPS=((2,2),(3,4),(5,8),(9,12),(13,23),(24,24))
METHODS=('tree:union','tree:intersection','tree:xor','tree:xnor','chain:mixed','action-mosaic')

def market_path(net):
    """Nonzero structural market path; decision weights encode thresholds, not gains."""
    todo=[len(net.nodes)-1];seen=set()
    while todo:
        i=todo.pop()
        if i in seen:continue
        seen.add(i);node=net.nodes[i]
        for source,weight in zip(node.sources,node.weights):
            if weight==0 and node.op!='decision':continue
            if 0<=source<MARKET_INPUTS*2//3:return True
            if source<0:todo.append(-source-1)
            elif source>=INPUTS:todo.append(source-INPUTS)
    return False

def group_name(lo,hi):return str(lo) if lo==hi else f'{lo}-{hi}'

def fragment(net,budget,rng):
    """Exact closed subgraph if possible, otherwise an explicit market-edge projection."""
    candidates=list(range(len(net.nodes)));rng.shuffle(candidates)
    # Bound traversal work for large recurrent graphs. Projection is recorded,
    # never passed off as retaining the full parental decision function.
    for root in candidates[:8]:
        seen=set();todo=[root]
        while todo and len(seen)<=budget:
            i=todo.pop()
            if i in seen:continue
            seen.add(i)
            todo.extend(-s-1 if s<0 else s-INPUTS for s in net.nodes[i].sources if s<0 or s>=INPUTS)
        if todo or len(seen)>budget or root!=max(seen):continue
        order=sorted(seen);mapping={old:i for i,old in enumerate(order)}
        nodes=tuple(replace(net.nodes[i],sources=tuple(-mapping[-s-1]-1 if s<0 else INPUTS+mapping[s-INPUTS] if s>=INPUTS else s for s in net.nodes[i].sources),slot=j+1) for j,i in enumerate(order))
        if not any(0<=s<MARKET_INPUTS*2//3 and w!=0 for n in nodes for s,w in zip(n.sources,n.weights)):continue
        try:
            result=Network(nodes,net.threshold,len(nodes)-1,net.size_weight,net.size_bias).validate()
            if market_path(result):return result,dict(kind='closed-subgraph',original_nodes=order,root=root)
        except ValueError:pass
    for i in candidates:
        n=net.nodes[i]
        if n.op not in ('tanh','identity','conv','max','min','mean','product'):continue
        pairs=[(s,w) for s,w in zip(n.sources,n.weights) if 0<=s<MARKET_INPUTS*2//3 and w!=0]
        if not pairs:continue
        node=replace(n,sources=tuple(s for s,w in pairs),weights=tuple(w for s,w in pairs),slot=1)
        return Network((node,),net.threshold,0,net.size_weight,net.size_bias).validate(),dict(kind='market-edge-projection',original_nodes=[i],root=i,removed_edges=len(n.sources)-len(pairs))
    raise ValueError('No nonzero market contribution in donor action')

def mix(donors,rng,method):
    """Donors are (day, expert serial, Individual); preserve a tagged branch per day."""
    count=len(donors);budget=max(1,min(12,(1024-30*(count-1))//(5*count)))
    nets={};provenance={};steps=[];fragments=[];node_identity=1
    for action_index,stage in enumerate(STAGES):
        chosen=[d for i,d in enumerate(donors) if i%5==action_index] if method=='action-mosaic' and count>=5 else donors
        if not chosen:chosen=[donors[action_index%count]]
        leaves=[]
        for day,serial,member in chosen:
            branch,detail=fragment(member.networks[stage],budget,rng)
            # Unique node identities support a direct surviving-contribution audit.
            tagged=tuple(replace(n,uid=node_identity+i) for i,n in enumerate(branch.nodes));node_identity+=len(tagged)
            branch=replace(branch,nodes=tagged);token=f'{stage}:{len(leaves)}'
            leaves.append((branch,{day:{tagged[-1].uid}},token))
            fragments.append(dict(stage=stage,day=day,parent_serial=serial,token=token,**detail))
        depth=0
        while len(leaves)>1:
            next_level=[];pairs=[(leaves[0],leaves[1])]
            tail=leaves[2:]
            if method!='chain:mixed':pairs=list(zip(leaves[::2],leaves[1::2]));tail=leaves[-1:] if len(leaves)%2 else []
            for left,right in pairs:
                mode=('union','intersection','xor','xnor')[(depth+action_index)%4] if method in ('chain:mixed','action-mosaic') else method.split(':')[1]
                # Intermediate graphs are private. Validate all final nodes and
                # budgets once below instead of rewalking every growing prefix.
                child=compose(left[0],right[0],mode,validate=False);labels={k:set(v) for k,v in left[1].items()}
                for day,uids in right[1].items():labels.setdefault(day,set()).update(uids)
                token=f'{stage}:join:{len(steps)}';steps.append(dict(id=token,parents=[left[2],right[2]],mode=mode,stage=stage,depth=depth+1))
                next_level.append((child,labels,token))
            leaves=next_level+tail;depth+=1
        nets[stage],provenance[stage],_=leaves[0]
    # Parameter mutation cannot delete a session's surviving graph contribution.
    mutation_stage=('entry','add','reduce')[int(rng.integers(3))];old=nets[mutation_stage];delta=float(rng.uniform(-.04,.04))
    nets[mutation_stage]=replace(old,size_bias=float(np.clip(old.size_bias+delta,-1.,1.)))
    member=Individual(nets,donors[0][2].policy,{s:0 for s in STAGES}).validate()
    surviving=set()
    for stage,net in member.networks.items():
        active=reachable(net,len(net.nodes)-1);active_uids={net.nodes[i].uid for i in active}
        for day,uids in provenance[stage].items():
            if active_uids & uids:surviving.add(day)
    if surviving!={d for d,_,_ in donors}:raise ValueError('A requested source session lost its output-connected contribution')
    return member,dict(method=method,source_sessions=sorted(surviving),parent_serials=[s for _,s,_ in donors],steps=steps,fragments=fragments,
        depth=max((s['depth'] for s in steps),default=0),mutations=[dict(stage=mutation_stage,field='size_bias',delta=delta)],nodes=sum(len(n.nodes) for n in nets.values()))

def reproduce(experts,seed,quota=512,progress=None):
    if len(experts)!=24 or any(len(v)!=128 for v in experts.values()):raise ValueError('Blind mixing requires exactly 128 unique experts from each of 24 sessions')
    rng=np.random.default_rng(np.random.SeedSequence([seed,56,3072]));days=sorted(experts);seen={program_key(m) for rows in experts.values() for _,m in rows};children=[];metadata=[];rejected=Counter()
    for lo,hi in GROUPS:
        for index in range(quota):
            count=lo+index%(hi-lo+1);method=METHODS[index%len(METHODS)]
            for attempt in range(64):
                selection=rng.permutation(days)[:count]
                # Include top-with-top and same-rank multi-session combinations,
                # alongside independently sampled experts. No new replay scores.
                aligned=(index//6)%3==0 and attempt==0
                ranks=[(index//6)%128]*count if aligned else [int(rng.integers(128)) for _ in selection]
                donors=[(str(day),*experts[str(day)][rank]) for day,rank in zip(selection,ranks)]
                try:
                    with validation_scope():child,meta=mix(donors,rng,method);key=program_key(child)
                    if key in seen:raise ValueError('Duplicate blind offspring')
                    seen.add(key);break
                except ValueError as e:rejected[str(e)]+=1
            else:raise RuntimeError(f'Blind mixing quota unfilled: {group_name(lo,hi)} {index}/{quota}; {dict(rejected)}')
            meta.update(group=group_name(lo,hi),ordinal=len(children),program_sha256=key,blind=True,score=None,donor_strategy='rank-aligned' if aligned else 'independent-ranks',donor_ranks=ranks)
            children.append(child);metadata.append(meta)
            if progress and (len(children)%64==0 or len(children)==6*quota):progress(len(children),6*quota)
    return children,metadata,dict(version=VERSION,groups={group_name(*g):quota for g in GROUPS},rejections=dict(rejected),evaluated=False)
