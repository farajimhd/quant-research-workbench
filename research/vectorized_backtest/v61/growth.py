"""Local feed-forward growth and dependency-preserving subtree exchange."""
from dataclasses import replace
from .genome import Neuron,Network,INPUTS,random_input,prune


def extend(net,stage,rng,kind):
    nodes=list(net.nodes);old=len(nodes)-1
    size=net.size_source if net.size_source>=0 else old
    if kind=='insert':
        # Exactly preserve the action output; keep the old sizing output.
        nodes.append(Neuron((INPUTS+old,),(1.,),0.,rng.uid(),op='identity'))
    elif kind in ('condition','alternative'):
        # Combine margins around the existing action threshold, preserving the
        # parent's decision boundary rather than AND/OR-ing arbitrary levels.
        nodes.append(Neuron((INPUTS+old,),(1.,),-net.threshold,rng.uid(),op='identity'))
        margin=len(nodes)-1
        nodes.append(Neuron((random_input(rng,stage),),(float(rng.choice([-1.,1.])),),float(rng.uniform(-.5,.5)),rng.uid()))
        predicate=len(nodes)-1
        nodes.append(Neuron((INPUTS+margin,INPUTS+predicate),(1.,1.),0.,rng.uid(),op='min' if kind=='condition' else 'max'))
        return replace(net,nodes=tuple(nodes),threshold=0.,size_source=size).validate()
    else:raise ValueError(kind)
    return replace(net,nodes=tuple(nodes),size_source=size).validate()


def subtree(recipient,donor,target,root):
    """Replace one recipient expression with a complete donor expression."""
    needed=set()
    def visit(i):
        if i in needed:return
        needed.add(i)
        for s in donor.nodes[i].sources:
            if s>=INPUTS:visit(s-INPUTS)
    visit(root);order=sorted(needed);mapping={n:target+i for i,n in enumerate(order)}
    inserted=[replace(donor.nodes[i],sources=tuple(INPUTS+mapping[s-INPUTS] if s>=INPUTS else s for s in donor.nodes[i].sources)) for i in order]
    used={n.slot for i,n in enumerate(recipient.nodes) if i!=target}
    for i,n in enumerate(inserted):
        slot=n.slot
        if slot in used:slot=2**64+n.uid
        while slot in used:slot+=1
        inserted[i]=replace(n,slot=slot);used.add(slot)
    delta=len(inserted)-1;newroot=mapping[root]
    def remap(s):
        if s<INPUTS:return s
        i=s-INPUTS
        return INPUTS+(newroot if i==target else i+delta if i>target else i)
    nodes=list(recipient.nodes[:target])+inserted+[replace(n,sources=tuple(remap(s) for s in n.sources)) for n in recipient.nodes[target+1:]]
    size=recipient.size_source if recipient.size_source>=0 else len(recipient.nodes)-1
    size=newroot if size==target else size+delta if size>target else size
    return prune(replace(recipient,nodes=tuple(nodes),size_source=size)).validate()
