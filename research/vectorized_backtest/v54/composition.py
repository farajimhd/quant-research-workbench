"""Homologous core recombination and local six-head inheritance."""
from dataclasses import replace
from .genome import HIDDEN,HEADS,Head,Branch,MARKET_INPUTS,INPUTS,FAN_IN,random_policy_gene
from research.vectorized_backtest.v42.schedule import Proposal,generation_root,SETTINGS

def proposal(rng,purpose,parent=None):
    if isinstance(rng,Proposal):return rng
    from .identity_cache import program_key
    return Proposal(generation_root(rng),(purpose,program_key(parent) if parent is not None else ''))
def settings(selection):return dict(reproduction_v54='homologous core plus six typed heads',validation_feedback=False,max_strategy_nodes=512)
def cross(rng,a,b,operator=None):
    rng=proposal(rng,'shared-cross',a);na=a.networks['shared'];nb=b.networks['shared']
    nodes=tuple(x if rng.random()<.5 else y for x,y in zip(na.nodes,nb.nodes))
    heads=tuple(x if rng.random()<.5 else y for x,y in zip(na.heads,nb.heads))
    child=replace(a,networks={'shared':replace(na,nodes=nodes,heads=heads)})
    return child.validate(),dict(actions=[dict(stage=s,mode='homologous-inherit') for s in HEADS],shared_core=True,whole_network=False)

def mutation(rng,parent,count,crossover_child=False):
    if not 1<=count<=5:raise ValueError('1..5 edits required')
    rng=proposal(rng,'shared-mutation',parent);n=parent.networks['shared'];nodes=list(n.nodes);heads=list(n.heads);policy=parent.policy;ages=dict(parent.minimum_age);edits=[]
    for i in range(count):
        r=rng.fork('edit',i);plan=rng.plan.get('edits',[]);scheduled=plan[i] if plan else {}
        structure=scheduled.get('mode','structure' if r.random()<.3 else 'parameter')=='structure'
        target=int(r.integers(6));core=bool(r.random()<.65);field=scheduled.get('field','weight')
        if not structure and field=='policy':
            name=str(r.choice(list(policy.__dataclass_fields__)))
            if name.startswith('swing_'):name='stop_fraction'
            policy=replace(policy,**{name:random_policy_gene(r,name)});label='policy:'+name
        elif not structure and field=='age':
            stage=HEADS[target%5];ages[stage]=int(r.integers(301));label='age'
        elif core:
            j=int(r.integers(HIDDEN));x=nodes[j];e=int(r.integers(len(x.sources)))
            if structure:
                sources=list(x.sources);options=list(range(-HIDDEN,0))+list(range(INPUTS));s=int(r.choice(options))
                while s in sources:s=int(r.choice(options))
                sources[e]=s;nodes[j]=replace(x,sources=tuple(sources));label='structure:rewire'
            elif field=='bias':nodes[j]=replace(x,bias=float(max(-16,min(16,x.bias+r.normal(0,.2)))));label='bias'
            else:
                w=list(x.weights);w[e]=float(max(-16,min(16,w[e]+r.normal(0,.2))));nodes[j]=replace(x,weights=tuple(w));label='weight'
        else:
            h=heads[target];k=int(r.integers(len(h.branches)));branches=list(h.branches);x=branches[k];e=int(r.integers(len(x.sources)))
            if structure:
                sources=list(x.sources);s=int(r.integers(HIDDEN))
                while s in sources:s=int(r.integers(HIDDEN))
                sources[e]=s;branches[k]=replace(x,sources=tuple(sources));label='structure:head-rewire'
            elif field=='threshold' and target<5:branches[k]=replace(x,threshold=float(r.uniform(-.999,.999)));label='threshold'
            elif field=='size':h=replace(h,size_bias=float(max(-16,min(16,h.size_bias+r.normal(0,.3)))));label='size'
            else:
                w=list(x.weights);w[e]=float(max(-16,min(16,w[e]+r.normal(0,.2))));branches[k]=replace(x,weights=tuple(w));label='head-weight'
            heads[target]=replace(h,branches=tuple(branches))
        edits.append(dict(stage='shared' if core else HEADS[target],field=label))
    return replace(parent,networks={'shared':replace(n,nodes=tuple(nodes),heads=tuple(heads))},policy=policy,minimum_age=ages).validate(),edits
