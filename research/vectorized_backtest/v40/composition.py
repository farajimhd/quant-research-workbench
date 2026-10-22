"""Generic graph inheritance, exchange and nonlinear composition only."""
from dataclasses import replace,fields
from .genome import Individual,Network,Neuron,Policy,STAGES,INPUTS,MARKET_INPUTS,random_input,prune,random_policy_gene,SWING_WINDOWS
from .config import contract,FAN_IN,WEIGHT_BOUND
from .sampling_report import COUNTS
from .schedule import Proposal,SETTINGS,balanced,generation_root,new_uid
from itertools import combinations

def settings(selection):return dict(**contract(),reproduction_v40=SETTINGS,validation_feedback=False)
def choose_operator(rng):return 'independent-action-graphs'

def import_module(nodes,net,root):
    piece=prune(net,None if root==len(net.nodes)-1 else root);offset=len(nodes)
    nodes.extend(Neuron(tuple(s-offset if s<0 else s if s<INPUTS else s+offset for s in n.sources),n.weights,n.bias,n.uid,n.op) for n in piece.nodes)
    return INPUTS+len(nodes)-1

def compose(rng,a,b,whole):
    nodes=[];x=import_module(nodes,a,len(a.nodes)-1 if whole else rng.pick(len(a.nodes),'primary-module'))
    y=import_module(nodes,b,len(b.nodes)-1 if whole else rng.pick(len(b.nodes),'donor-module'))
    # Compose margins about the parents' thresholds. A weak secondary term
    # perturbs a useful primary decision instead of resetting its trigger.
    weights=(1.,float(rng.uniform(-.5,.5)))
    bias=-weights[0]*a.threshold-weights[1]*b.threshold if whole else 0.
    nodes.append(Neuron((x,y),weights,bias,new_uid(rng,'composition')))
    return Network(tuple(nodes),0.,a.size_source if whole and a.size_source>=0 else x-INPUTS,a.size_weight,a.size_bias).validate()

def exchange(rng,a,b):
    target=rng.pick(len(a.nodes),'primary-module');piece=prune(b,rng.pick(len(b.nodes),'donor-module'))
    extra=len(piece.nodes)-1
    mapping={i:i if i<target else i+extra for i in range(len(a.nodes))}
    def remap(s):return -mapping[-s-1]-1 if s<0 else s if s<INPUTS else INPUTS+mapping[s-INPUTS]
    def donor(s):return -(target-s-1)-1 if s<0 else s if s<INPUTS else s+target
    nodes=[]
    for i,node in enumerate(a.nodes):
        if i==target:nodes.extend(Neuron(tuple(donor(v) for v in n.sources),n.weights,n.bias,n.uid,n.op) for n in piece.nodes)
        else:nodes.append(replace(node,sources=tuple(remap(v) for v in node.sources)))
    old_size=a.size_source if a.size_source>=0 else len(a.nodes)-1
    return prune(Network(tuple(nodes),piece.threshold if target==len(a.nodes)-1 else a.threshold,
        mapping[old_size],a.size_weight,a.size_bias)).validate()


def homologous_weights(rng,a,b):
    def unique(net):
        from collections import Counter
        counts=Counter(n.uid for n in net.nodes if n.uid)
        return {n.uid:n for n in net.nodes if n.uid and counts[n.uid]==1}
    other=unique(b);own=unique(a);nodes=[]
    def key(net,source):
        if source<0 or source>=INPUTS:
            uid=net.nodes[-source-1 if source<0 else source-INPUTS].uid
            if not uid or uid not in (own if net is a else other):return None
            return ('delay' if source<0 else 'node',uid)
        return ('input',source)
    for node in a.nodes:
        donor=other.get(node.uid)
        if donor is None or donor.op!=node.op or node.uid not in own:nodes.append(node);continue
        weights={k:w for s,w in zip(donor.sources,donor.weights) if (k:=key(b,s)) is not None};out=[]
        for source,w in zip(node.sources,node.weights):
            match=weights.get(key(a,source));out.append((w+match)/2 if match is not None and rng.pick(2,'homologous-edge')==0 else w)
        nodes.append(replace(node,weights=tuple(out)))
    return replace(a,nodes=tuple(nodes)).validate()

def proposal(rng,purpose,parent=None):
    if isinstance(rng,Proposal):return rng
    from .lineage import program_key
    return Proposal(generation_root(rng),(purpose,program_key(parent) if parent is not None else ''))


def cross(rng,a,b,operator=None):
    rng=proposal(rng,'standalone-cross',a);plan=rng.plan
    base,donor=(a,b) if (plan['orientation'] if 'orientation' in plan else rng.pick(2,'orientation'))==0 else (b,a)
    networks=dict(base.networks);ages=dict(base.minimum_age)
    count=(plan['count'] if 'count' in plan else 1+rng.pick(5,'action-count'))
    choices=list(combinations(STAGES,count));changed=choices[rng.pick(len(choices),'action-subset')]
    events=[]
    for j,stage in enumerate(changed):
        if plan.get('modes'):mode=plan['modes'][j]
        else:
            cell=rng.pick(10,'action-mode')
            mode='inherit' if cell<4 else 'exchange' if cell<7 else 'compose'
        COUNTS['requested_'+mode]+=1;left,right=(donor,base) if mode=='inherit' else (base,donor)
        for attempt in range(8):
            context=rng.fork('action',j,'attempt',attempt);COUNTS['module_attempt_'+mode]+=1
            try:
                if mode=='inherit':n=left.networks[stage]
                elif mode=='exchange':n=exchange(context,left.networks[stage],right.networks[stage])
                else:n=compose(context,left.networks[stage],right.networks[stage],(plan.get('action_start',0)+j)%2==0)
            except ValueError:
                COUNTS['rejected_'+mode]+=1
                if attempt==7:raise
            else:break
        mixing=((plan.get('action_start',0)+j)//2)%2==0
        if mixing:n=homologous_weights(context,n,right.networks[stage])
        COUNTS['accepted_'+mode]+=1;networks[stage]=n
        events.append(dict(stage=stage,mode=mode,whole=(plan.get('action_start',0)+j)%2==0,homologous=mixing))
        if stage!='entry':ages[stage]=left.minimum_age.get(stage,0)
    genes={f.name:getattr(base.policy,f.name) for f in fields(Policy)}
    options=[f.name for f in fields(Policy) if not f.name.startswith('swing_')]+['swing']
    name=options[rng.pick(len(options),'policy-field')]
    if name=='swing':genes.update(swing_left=donor.policy.swing_left,swing_right=donor.policy.swing_right)
    else:genes[name]=getattr(donor.policy,name)
    return Individual(networks,Policy(**genes),ages).validate(),dict(actions=events,base_parent=0 if base is a else 1,policy_field=name)


def reflected(x,bound):return float(bound-abs((x+bound)%(4*bound)-2*bound))
def parameter(rng,x,threshold=False):
    bound=.999999 if threshold else WEIGHT_BOUND
    reset=rng.pick(5,'parameter-reset')==0
    return float(rng.uniform(-bound if threshold else -1,bound if threshold else 1)) if reset else reflected(x+float(rng.normal(0,.1 if threshold else .2)),bound)


def mutation(rng,parent,count,crossover_child=False):
    if not 1<=count<=5:raise ValueError('1..5 distinct edits required')
    root=proposal(rng,'standalone-mutation',parent);child=parent;edits=[];used=set()
    plans=root.plan.get('edits',[])
    for edit in range(count):
        if plans:scheduled=plans[edit]
        else:
            mode='parameter' if root.pick(10,'mutation-kind')<7 else 'structure'
            field=SETTINGS['parameter_fields'][int(balanced(20,SETTINGS['parameter_weights'])[root.pick(20,'parameter-kind')])] if mode=='parameter' else SETTINGS['structural_fields'][root.pick(7,'structural-kind')]
            scheduled=dict(stage=root.pick(5,'mutation-stage'),mode=mode,field=field)
        for attempt in range(256):
            rng=root.fork('edit',edit,'attempt',attempt)
            # Finite schedules choose where/what; RNG proposes new values only.
            stage=STAGES[(scheduled['stage']+attempt//len(SETTINGS['structural_fields']))%5]
            mode=scheduled['mode'];field=scheduled['field']
            # If a scheduled edit is inapplicable, rotate through the same legal
            # operator family rather than consume unrelated offspring streams.
            choices=SETTINGS['parameter_fields'] if mode=='parameter' else SETTINGS['structural_fields']
            field=choices[(choices.index(field)+attempt)%len(choices)]
            net=child.networks[stage];nodes=list(net.nodes);newnet=net;policy=child.policy;ages=dict(child.minimum_age)
            try:
                if mode=='parameter':
                    if field=='threshold':key=(stage,field);newnet=replace(net,threshold=parameter(rng,net.threshold,True))
                    elif field=='policy':
                        names=[f.name for f in fields(Policy) if not f.name.startswith('swing_')]+['swing'];name=names[rng.pick(len(names),'policy-gene')];key=('policy',name)
                        if name=='swing':
                            enabled=rng.pick(2,'swing-enabled')==0;policy=replace(policy,swing_left=int(rng.choice(SWING_WINDOWS)) if enabled else 0,swing_right=int(rng.choice(SWING_WINDOWS)) if enabled else 0)
                        else:policy=replace(policy,**{name:random_policy_gene(rng,name)})
                    elif field=='size':
                        name=('size_source','size_weight','size_bias')[rng.pick(3,'size-field')];key=(stage,name)
                        value=rng.pick(len(net.nodes),'size-head') if name=='size_source' else parameter(rng,getattr(net,name));newnet=replace(net,**{name:value})
                    elif field=='age':
                        if stage=='entry':continue
                        key=(stage,field);ages[stage]=int(rng.integers(3601))
                    else:
                        index=rng.pick(len(nodes),'node');node=nodes[index]
                        if field=='bias':key=(stage,field,index);nodes[index]=replace(node,bias=parameter(rng,node.bias))
                        elif rng.pick(5,'weight-module')==0:
                            key=(stage,'module_weights',index);nodes[index]=replace(node,weights=tuple(parameter(rng,w) for w in node.weights),bias=parameter(rng,node.bias))
                        else:
                            edge=rng.pick(len(node.weights),'edge');key=(stage,field,index,edge);w=list(node.weights);w[edge]=parameter(rng,w[edge]);nodes[index]=replace(node,weights=tuple(w))
                        newnet=replace(net,nodes=tuple(nodes))
                else:
                    if any(e['stage']==stage for e in edits):continue
                    index=rng.pick(len(nodes),'node');node=nodes[index];key=(stage,'structure')
                    if field=='operator':
                        from .genome import OPS
                        nodes[index]=replace(node,op=OPS[rng.pick(len(OPS),'new-operator')])
                    elif field=='input':
                        edge=rng.pick(len(node.sources),'edge');sources=list(node.sources);sources[edge]=random_input(rng,stage);nodes[index]=replace(node,sources=tuple(sources))
                    elif field=='connect':
                        if len(node.sources)>=FAN_IN:continue
                        source=int(rng.integers(INPUTS+index))
                        if stage=='entry' and MARKET_INPUTS<=source<MARKET_INPUTS+7:continue
                        nodes[index]=replace(node,sources=node.sources+(source,),weights=node.weights+(float(rng.uniform(-1,1)),))
                    elif field=='disconnect':
                        if len(node.sources)==1:continue
                        edge=rng.pick(len(node.sources),'edge');nodes[index]=replace(node,sources=node.sources[:edge]+node.sources[edge+1:],weights=node.weights[:edge]+node.weights[edge+1:])
                    elif field=='delay':
                        edge=rng.pick(len(node.sources),'edge');sources=list(node.sources);sources[edge]=-int(rng.integers(len(nodes)))-1;nodes[index]=replace(node,sources=tuple(sources))
                    elif field=='grow':
                        if len(node.sources)>=FAN_IN:continue
                        sources=[s for s in node.sources if s>=0];source=sources[rng.pick(len(sources),'growth-source')] if sources else random_input(rng,stage)
                        def shift(s):return s-1 if s<0 and -s-1>=index else s+1 if s>=INPUTS+index else s
                        nodes=[replace(n,sources=tuple(shift(s) for s in n.sources)) for n in nodes]
                        nodes.insert(index,Neuron((source,),(1.,),0.,rng.uid()));target=nodes[index+1];nodes[index+1]=replace(target,sources=target.sources+(INPUTS+index,),weights=target.weights+(.05,))
                        if newnet.size_source>=index:newnet=replace(newnet,size_source=newnet.size_source+1)
                    else:
                        options=[s-INPUTS for s in nodes[-1].sources if s>=INPUTS]
                        if not options:continue
                        newnet=prune(net,options[rng.pick(len(options),'remove-module')]);nodes=list(newnet.nodes)
                    newnet=prune(replace(newnet,nodes=tuple(nodes)))
                if key in used or any(e['stage']==stage and e['field'].startswith('structure') for e in edits):continue
                candidate=Individual(dict(child.networks,**{stage:newnet}),policy,ages).validate()
                if candidate==child:continue
            except ValueError:continue
            child=candidate;used.add(key);edits.append(dict(stage=stage,field=mode+':'+field,gene=list(key),scheduled=scheduled,attempt=attempt));break
        else:raise RuntimeError('Distinct network mutation budget exhausted')
    return child,edits
