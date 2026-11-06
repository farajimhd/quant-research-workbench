"""Conservative offspring proposals; never rewrites evaluated populations."""
from dataclasses import replace
import numpy as np


def boolean_decisions(network):
    """Remove internal Boolean re-tests only when unknown values are also zero.

    CUDA Boolean operators may propagate values from unknown operands. Therefore
    a Boolean range alone is insufficient: require zero-when-unknown inductively.
    Preserve output-last ABI and sizing references; no arithmetic reassociation.
    """
    from .genome import INPUTS
    nodes=[];mapping={};safe=[];removed=[]
    for i,node in enumerate(network.nodes):
        sources=tuple(INPUTS+mapping[s-INPUTS] if s>=INPUTS else s for s in node.sources)
        is_safe=(node.op=='decision' or (node.op in ('whole_and','whole_or','whole_xor')
                 and all(s>=INPUTS and safe[s-INPUTS] for s in node.sources)))
        safe.append(is_safe)
        source=node.sources[0]
        if (i<len(network.nodes)-1 and node.op=='decision' and source>=INPUTS
                and safe[source-INPUTS] and 0<=float(np.float32(node.weights[0]))<1):
            mapping[i]=mapping[source-INPUTS]
            removed.append(dict(node=i,source=source-INPUTS,
                proof='Input is Boolean and zero when unknown; threshold in[0,1) preserves both value and known mask.'))
        else:
            mapping[i]=len(nodes);nodes.append(replace(node,sources=sources))
    size=mapping[network.size_source] if network.size_source>=0 else network.size_source
    return replace(network,nodes=tuple(nodes),size_source=size).validate(),removed


def simplify_boolean_decisions(member):
    from .strategy_mix import leaves
    from .authority import construct
    branches=[];evidence=[]
    for index,branch in enumerate(leaves(member)):
        networks={}
        for stage,network in branch.networks.items():
            networks[stage],removed=boolean_decisions(network)
            evidence.extend(dict(branch=index,stage=stage,**row) for row in removed)
        branches.append(replace(branch,networks=networks))
    child=replace(branches[0],routing=tuple(branches)) if member.routing else branches[0]
    return construct(child),evidence


def qualify_cuda(parent,child,seed=17006):
    """Bounded adversarial signed-input/mask check, not training performance."""
    import torch
    from .network import NetworkPlan
    from .network_cuda import market_batch
    plan=NetworkPlan([parent,child],'cuda');plan.prepare(2,5)
    tested=0
    try:
        t=256;c=len(plan.columns);lag=len(plan.cross_columns) if plan.uses_cross else 0
        torch.manual_seed(seed)
        for available in (0.,.25,.75,1.):
            for repeat in range(4):
                values=torch.rand((t,5,3*c+lag),device='cuda')*2-1
                valid=torch.rand((t,5,c+lag),device='cuda')<available
                values[:,:,2*c:3*c]=valid[:,:,:c].float()
                work=torch.empty((t,plan.total_nodes,5),device='cuda');known=torch.empty_like(work,dtype=torch.bool)
                actions,ratios=market_batch(plan,values,valid,torch.ones((t,5),device='cuda',dtype=torch.bool),work,known,
                    torch.arange(5,device='cuda',dtype=torch.int32)[None].expand(t,-1))
                torch.testing.assert_close(actions[:,0],actions[:,1],rtol=0,atol=0)
                torch.testing.assert_close(ratios[:,0],ratios[:,1],rtol=0,atol=0)
                tested+=t*5
        return dict(kind='synthetic_cuda_equivalence',seed=seed,observations=tested,action_bits_exact=True,
                    sizing_exact=True,training_performance_test=False,validation_used=False)
    finally:plan.close()
