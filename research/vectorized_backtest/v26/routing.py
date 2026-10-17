"""Two-leaf rule packing and sticky position ownership, no independent portfolios."""
import torch
from dataclasses import replace
from .composition import MODES


def branches(member):
    return (member,member) if member.routing is None else (member.routing['left'],member.routing['right'])


class RoutingPlan:
    def __init__(self,members,device,maximum_capture_gib=4.):
        from .rule_plan import RulePlan
        self.members=members;self.device=torch.device(device)
        leaves=[];lookup={};self.indices=[]
        for member in members:
            indices=[]
            for m in branches(member):
                from .lineage import program_key
                key=program_key(m)
                if key not in lookup:lookup[key]=len(leaves);leaves.append(m)
                indices.append(lookup[key])
            r=member.routing
            if r and r['mode']=='conditional':
                from .genome import assemble,STAGES
                from .composition import negate
                from .program import Program,Node,Op
                never=Program((Node(Op.CONSTANT,value=0.,unit='bool'),),0)
                condition=assemble({stage:r['condition'] if stage=='entry' else negate(r['condition']) if stage=='exit' else never for stage in STAGES},r['left'].policy,{})
                key=program_key(condition)
                if key not in lookup:lookup[key]=len(leaves);leaves.append(condition)
                indices.append(lookup[key])
            else:indices.append(indices[0])
            self.indices.append(indices)
        self.leaves=leaves;self.base=RulePlan(leaves,device,maximum_capture_gib)
        for group in self.base.groups:
            locations={int(v):j for j,v in enumerate(group['candidates'].cpu().tolist())};group['writes']=[]
            for branch,shift in ((0,0),(1,5),(2,10)):
                pairs=[(i,locations[ids[branch]]) for i,ids in enumerate(self.indices) if ids[branch] in locations and (branch==0 or branch==1 and members[i].routing is not None or branch==2 and members[i].routing is not None and members[i].routing['mode']=='conditional' and group['bit']<2)]
                if pairs:
                    target,source=zip(*pairs)
                    group['writes'].append((torch.tensor(target,device=device),torch.tensor(source,device=device),shift))
            group['fused_writes']=[(target,group['expand'][source],shift) for target,source,shift in group['writes']]

    def __getattr__(self,name):return getattr(self.base,name)
    def evaluate(self,data,members,chunk,listing_batch,maximum_gate_gib,progress=None):
        # One deduplicated rule pass; no repeated evaluation of shared branches.
        self.base.workspace_gib=getattr(self,'workspace_gib',None)
        return self.base.evaluate(data,self.leaves,chunk,listing_batch,2*maximum_gate_gib,progress,packed=len(self.members))
    def close(self):self.base.close()


def make_plan(members,device,maximum_capture_gib=4.):
    if any(m.routing is not None for m in members):return RoutingPlan(members,device,maximum_capture_gib)
    from .rule_plan import RulePlan
    return RulePlan(members,device,maximum_capture_gib)


def eligible_entry(signal,price,observed,membership,swing,policy,execution,terminal):
    initial=torch.where(policy[...,8]>0,swing,price*(1-policy[...,0]))
    tradable=observed&torch.isfinite(price)&(price>0)
    result=((signal&1)!=0)&tradable&membership&~terminal&torch.isfinite(initial)&(initial>0)&(initial<price)
    if execution.risk_protection:
        q=execution.entry_dollars/price.clamp_min(1e-12);cost=execution.cost_bps/10000.
        risk=q*(price-initial).clamp_min(0)+2*(q*execution.fee_per_share).clamp_min(execution.minimum_order_fee)+execution.entry_dollars*cost+q*initial.clamp_min(0)*cost
        result &= ((price-initial)<=(execution.maximum_stop_distance+1e-12)*price.clamp_min(1e-12))&(risk<=execution.maximum_position_risk)
    return result


def choose(raw,held,owner,age,policies,ages,swings,modes,price,observed,membership,terminal,execution):
    from .evaluator import state_signals
    left=raw&31;right=(raw>>5)&31;condition=(raw>>10)&1;condition_known=((raw>>10)&3)!=0
    # Branch entry decisions are causal gates; feasibility uses the execution mark.
    ok0=eligible_entry(left,price,observed,membership,swings[0],policies[0],execution,terminal)
    ok1=eligible_entry(right,price,observed,membership,swings[1],policies[1],execution,terminal)
    proposed=torch.where(ok0,0,1)
    proposed=torch.where(modes==2,torch.where(ok1,1,0),proposed)
    proposed=torch.where(modes==3,torch.where(condition!=0,0,1),proposed)
    proposed=torch.where((modes==0)|(modes==4)|(modes==6),0,proposed)
    picked=torch.where(held,owner,proposed)
    signal=torch.where(picked==0,left,right)
    signal=torch.where((~held)&(modes==4)&~(((left&1)!=0)&((right&1)!=0)),signal&~1,signal)
    signal=torch.where((~held)&(modes==5)&(((left&1)!=0)==((right&1)!=0)),signal&~1,signal)
    signal=torch.where((~held)&(modes==3)&~condition_known,signal&~1,signal)
    # An overlay can request an exit or veto an add; it cannot increase exposure.
    overlay=held&(modes==6)
    signal=torch.where(overlay,(signal|(right&2))&torch.where((right&4)!=0,31,27),signal)
    limits=torch.where((picked==0)[...,None],ages[0],ages[1])
    signal=state_signals(signal,held,age,limits,False)
    policy=torch.where((picked==0)[...,None],policies[0],policies[1])
    swing=torch.where(picked==0,swings[0],swings[1])
    return signal,policy,swing,picked
