"""Persistent-ID remapping and explicit bounded structural edits."""
from dataclasses import replace
from .genome import MIN_HIDDEN,MAX_NODES,node_count

def remap_node(node,old_nodes,lookup):
    return replace(node,sources=tuple(-lookup[old_nodes[-s-1].uid]-1 if s<0 else s for s in node.sources))

def remap_head(head,old_nodes,lookup):
    return replace(head,branches=tuple(replace(b,sources=tuple(lookup[old_nodes[s].uid] for s in b.sources)) for b in head.branches))

def grow(rng,network,count=1):
    """Clone a tapped cell, redirect its head tap; preserve every clock's outputs.

    Clone inputs still reference the original cells, including original self
    memory. Both cells therefore produce identical values from session reset.
    Later edits can specialize the new persistent identity independently.
    """
    if count not in (1,2,4,8) or node_count(network)+count>MAX_NODES:raise ValueError('Growth exceeds strategy node budget')
    nodes=list(network.nodes);heads=list(network.heads);used={n.uid for n in nodes}
    for _ in range(count):
        a=int(rng.integers(6));h=heads[a];k=int(rng.integers(len(h.branches)));branch=h.branches[k];e=int(rng.integers(len(branch.sources)))
        old=branch.sources[e];uid=rng.uid()
        while uid in used:uid=rng.uid()
        used.add(uid);new=len(nodes);nodes.append(replace(nodes[old],uid=uid))
        sources=list(branch.sources);sources[e]=new;branches=list(h.branches);branches[k]=replace(branch,sources=tuple(sources));heads[a]=replace(h,branches=tuple(branches))
    return replace(network,nodes=tuple(nodes),heads=tuple(heads)).validate()

def prune(network,removed):
    removed=set(removed);old=network.nodes
    if not removed or any(type(i) is not int or not 0<=i<len(old) for i in removed) or len(old)-len(removed)<MIN_HIDDEN:raise ValueError('Invalid bounded pruning')
    keep=[i for i in range(len(old)) if i not in removed];lookup={i:j for j,i in enumerate(keep)};nodes=[]
    for i in keep:
        x=old[i];edges=[(s if s>=0 else -lookup[-s-1]-1,w) for s,w in zip(x.sources,x.weights) if s>=0 or -s-1 not in removed]
        # A zero-weight market placeholder retains the bias but no known flag.
        if not edges:edges=[(0,0.)]
        nodes.append(replace(x,sources=tuple(s for s,w in edges),weights=tuple(w for s,w in edges)))
    heads=[]
    for h in network.heads:
        branches=[]
        for b in h.branches:
            edges=[(lookup[s],w) for s,w in zip(b.sources,b.weights) if s not in removed]
            if not edges:edges=[(0,0.)]
            branches.append(replace(b,sources=tuple(s for s,w in edges),weights=tuple(w for s,w in edges)))
        heads.append(replace(h,branches=tuple(branches)))
    return replace(network,nodes=tuple(nodes),heads=tuple(heads)).validate()

def union(rng,a,b):
    """Full ID union; never truncate dependencies to squeeze under the ceiling."""
    chosen={n.uid:(n,a.nodes) for n in a.nodes};order=list(chosen)
    for n in b.nodes:
        if n.uid not in chosen:order.append(n.uid);chosen[n.uid]=(n,b.nodes)
        elif rng.random()>=.5:chosen[n.uid]=(n,b.nodes)
    lookup={uid:i for i,uid in enumerate(order)}
    return tuple(remap_node(*chosen[uid],lookup) for uid in order),lookup
