"""Whole-strategy routing and coherent module crossover under the same budget."""
from dataclasses import replace
from .genome import Individual,STAGES
from .config import MAX_STRATEGY_NODES,MAX_NODES

def leaves(member):
    return tuple(member.routing) if member.routing else (member,)

def node_count(member):
    return sum(len(n.nodes) for m in leaves(member) for n in m.networks.values())

def validate(member):
    if not isinstance(member.routing,tuple) or len(member.routing)<2:raise ValueError('Routing requires immutable ordered branches')
    for m in member.routing:
        if m.routing:raise ValueError('Routing branches must be flattened')
        m.validate()
    if node_count(member)>MAX_STRATEGY_NODES:raise ValueError('Routed strategy compute budget exceeded')
    if any(sum(len(m.networks[s].nodes) for m in member.routing)>MAX_NODES for s in STAGES):raise ValueError('Routed action budget exceeded')
    if any(m.case_owner!=member.case_owner for m in member.routing):raise ValueError('Routed owner mismatch')

def route(a,b):
    branches=leaves(a)+leaves(b)
    return replace(branches[0],routing=branches).validate()

def cross(a,b,mode,branch_a=0,branch_b=0):
    if mode=='entry_routing':
        child=route(a,b)
    else:
        a=leaves(a)[branch_a%len(leaves(a))];b=leaves(b)[branch_b%len(leaves(b))]
        if mode=='entry_management_exchange':
            nets=dict(b.networks);nets['entry']=a.networks['entry']
            ages=dict(b.minimum_age);ages['entry']=a.minimum_age.get('entry',0)
            child=Individual(nets,b.policy,ages,a.case_owner).validate()
        elif mode=='reciprocal_veto':
            from .whole_network import network
            nets=dict(a.networks)
            # B is an entry confirmation/veto only; keep A's sizing head.
            combined=network(a.networks['entry'],b.networks['entry'],'intersection',a.minimum_age.get('entry',0),b.minimum_age.get('entry',0))
            from .genome import prune
            head=a.networks['entry'].size_source
            combined=replace(combined,size_source=head if head>=0 else len(a.networks['entry'].nodes)-1,
                size_weight=a.networks['entry'].size_weight,size_bias=a.networks['entry'].size_bias)
            nets['entry']=prune(combined)
            child=Individual(nets,a.policy,{**a.minimum_age,'entry':0},a.case_owner).validate()
        else:raise ValueError('Unknown strategy combination')
    return child,dict(actions=[dict(stage='strategy',mode=mode)],branch_a=branch_a,branch_b=branch_b,
        arbitration='first feasible current-market entry branch; independent of execution',branch_count=len(leaves(child)))

def count(member):
    return sum(len(n.nodes)+sum(len(x.sources) for x in n.nodes) for m in leaves(member) for n in m.networks.values())
