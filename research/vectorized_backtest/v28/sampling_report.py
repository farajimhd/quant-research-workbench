from collections import Counter
COUNTS=Counter()
def snapshot():return dict(COUNTS)
def delta(before):return {k:v-before.get(k,0) for k,v in COUNTS.items() if v!=before.get(k,0)}
def coverage(members):
    sizes=Counter();features=Counter();depths=Counter();fanins=Counter();states=Counter();actions={}
    from .features import CATALOG
    for m in members:
        for stage,n in m.networks.items():
            sizes[len(n.nodes)]+=1
            actions.setdefault(stage,Counter())[len(n.nodes)]+=1;ds=[]
            for node in n.nodes:
                from .genome import INPUTS
                ds.append(1+max((ds[s-INPUTS] for s in node.sources if s>=INPUTS),default=0));fanins[len(node.sources)]+=1
                for s in node.sources:
                    if s<len(CATALOG):features[str(s)]+=1
                    elif s<INPUTS:states[str(s-len(CATALOG))]+=1
            depths[max(ds)]+=1
    return dict(neuron_counts=dict(sizes),depths=dict(depths),fanins=dict(fanins),state_inputs=dict(states),features=dict(features),per_action_sizes={s:dict(v) for s,v in actions.items()})
