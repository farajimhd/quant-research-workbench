"""Boolean head composition over one common latent representation."""
from dataclasses import replace
from .genome import HEADS,STAGES,Head
POLICY=dict(total_crossover_fraction=.25,modes=dict(union=.4,intersection=.2,xor=.2,xnor=.2),max_strategy_nodes=512,shared_core='base parent',nested_composition='reject explicitly',stop='continuous inheritance; never Boolean')
def fits(a,b,stages):
    return all(len(a.networks['shared'].heads[HEADS.index(s)].branches)==len(b.networks['shared'].heads[HEADS.index(s)].branches)==1 for s in stages)
def cross(a,b,mode,stages=None):
    stages=STAGES if stages is None else tuple(stages)
    if mode not in POLICY['modes'] or not stages or len(set(stages))!=len(stages) or any(s not in STAGES for s in stages):raise ValueError('Invalid discrete composition')
    if not fits(a,b,stages):raise ValueError('Nested head composition exceeds two-branch contract')
    n=a.networks['shared'];heads=list(n.heads);ages=dict(a.minimum_age)
    for s in stages:
        i=HEADS.index(s);x=heads[i];y=b.networks['shared'].heads[i]
        # Both readouts are evaluated on the base parent's common latent state.
        heads[i]=replace(x,branches=(x.branches[0],y.branches[0]),mode=mode)
        ages[s]=max(a.minimum_age.get(s,0),b.minimum_age.get(s,0))
    return replace(a,networks={'shared':replace(n,heads=tuple(heads))},minimum_age=ages).validate(),dict(actions=[dict(stage=s,mode='shared-head:'+mode,whole=True) for s in stages],whole_network=True,shared_core='base-parent',parent_behavior_preserved=False)
