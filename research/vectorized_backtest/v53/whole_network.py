"""Exact branch-preserving Boolean composition inside the bounded sparse ABI."""
from dataclasses import replace
from .genome import Individual, Network, Neuron, STAGES, INPUTS

POLICY = dict(total_crossover_fraction=.25, phase0_same_owner_only=True, phase1_parent_pool='unified-session-score',
              modes=dict(union=.4, intersection=.2, xor=.2, xnor=.2),
              sizing='sole firing branch, maximum if both; XNOR agreement uses maximum; never sum',
              financial_policy='base parent', max_strategy_nodes=512,
              recurrent_state='disjoint branch nodes', oversize='explicit fresh fallback')


def network(a, b, mode, age_a=0, age_b=0):
    if mode not in POLICY['modes']: raise ValueError('Unknown whole-network mode')
    nodes=[]; roots=[]; sizes=[]
    for side, (net, age) in enumerate(((a, age_a), (b, age_b))):
        offset=len(nodes)
        def remap(s):
            return -(offset-s-1)-1 if s<0 else s if s<INPUTS else s+offset
        nodes.extend(replace(n, sources=tuple(remap(s) for s in n.sources),
                             slot=4*n.slot+side+1) for n in net.nodes)
        root=offset+len(net.nodes)-1
        head=offset+(net.size_source if net.size_source>=0 else len(net.nodes)-1)
        slot=8*side+3
        # decision encodes threshold in weight and minimum age / 3600 in bias.
        roots.append(len(nodes));nodes.append(Neuron((INPUTS+root,), (net.threshold,), age/3600., op='decision',slot=slot))
        sizes.append(len(nodes));nodes.append(Neuron((INPUTS+head,), (net.size_weight,), net.size_bias,op='linear',slot=slot+4))
    size=len(nodes)
    nodes.append(Neuron(tuple(INPUTS+i for i in (*roots,*sizes)),(1.,)*4,0.,op='whole_xnor_size' if mode=='xnor' else 'whole_size',slot=19))
    op={'union':'whole_or','intersection':'whole_and','xor':'whole_xor','xnor':'whole_xnor'}[mode]
    nodes.append(Neuron(tuple(INPUTS+i for i in roots),(1.,1.),0.,op=op,slot=23))
    return Network(tuple(nodes), .5, size, 1., 0.).validate()


def node_cost(a,b,stages):
    """Exact total allocation before graph construction; no budget increase."""
    return sum(len(n.nodes) for n in a.networks.values())+sum(len(b.networks[s].nodes)+6 for s in stages)


def fits(a,b,stages):
    from research.vectorized_backtest.v42.config import MAX_STRATEGY_NODES,MAX_NODES
    return (node_cost(a,b,stages)<=MAX_STRATEGY_NODES
        and all(len(a.networks[s].nodes)+len(b.networks[s].nodes)+6<=MAX_NODES for s in stages))


def cross(a,b,mode,stages=None):
    if a.case_owner!=b.case_owner:raise ValueError('Cross-case reproduction is forbidden')
    stages=STAGES if stages is None else tuple(stages)
    if not stages or len(stages)!=len(set(stages)) or any(s not in STAGES for s in stages):raise ValueError('Invalid composed actions')
    if not fits(a,b,stages):raise ValueError('Preflight strategy compute budget exceeded')
    nets=dict(a.networks);ages=dict(a.minimum_age)
    for s in stages:
        nets[s]=network(a.networks[s],b.networks[s],mode,a.minimum_age.get(s,0),b.minimum_age.get(s,0))
        ages[s]=max(a.minimum_age.get(s,0),b.minimum_age.get(s,0)) if mode=='xnor' else 0
    child=Individual(nets,a.policy,ages,a.case_owner).validate()
    return child,dict(actions=[dict(stage=s,mode='whole:'+mode,whole=True) for s in stages],
        base_parent=0,policy_field='base-parent',whole_network=True,
        node_counts=[sum(len(n.nodes) for n in p.networks.values()) for p in (a,b,child)])
