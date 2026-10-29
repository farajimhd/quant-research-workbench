from collections import Counter
COUNTS=Counter()
def snapshot():return dict(COUNTS)
def delta(before):return {k:v-before.get(k,0) for k,v in COUNTS.items() if v!=before.get(k,0)}
def coverage(members):
    operators=Counter();sizes=Counter();features=Counter();depths=Counter();fanins=Counter();states=Counter();actions={};delayed=0;identities=set();heads=Counter()
    from research.vectorized_backtest.v42.features import CATALOG
    for m in members:
        for stage,n in m.networks.items():
            sizes[len(n.nodes)]+=1
            heads[stage]+=int(n.size_source>=0)
            actions.setdefault(stage,Counter())[len(n.nodes)]+=1;ds=[]
            for node in n.nodes:
                operators[node.op]+=1
                delayed+=sum(s<0 for s in node.sources);identities.add(node.uid)
                from .genome import INPUTS
                ds.append(1+max((ds[s-INPUTS] for s in node.sources if s>=INPUTS),default=0));fanins[len(node.sources)]+=1
                for s in node.sources:
                    if 0<=s<3*len(CATALOG):features[str(s%len(CATALOG))]+=1
                    elif 3*len(CATALOG)<=s<INPUTS:states[str(s-3*len(CATALOG))]+=1
            depths[max(ds)]+=1
    return dict(operators=dict(operators),neuron_counts=dict(sizes),depths=dict(depths),fanins=dict(fanins),state_inputs=dict(states),features=dict(features),per_action_sizes={s:dict(v) for s,v in actions.items()},delayed_connections=delayed,persistent_node_identities=len(identities-{0}),independent_size_heads=dict(heads))
