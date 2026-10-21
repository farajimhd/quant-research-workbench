"""Generic graph inheritance, exchange and nonlinear composition only."""
from dataclasses import replace,fields
from .genome import Individual,Network,Neuron,Policy,STAGES,INPUTS,MARKET_INPUTS,random_input,prune,random_policy_gene,SWING_WINDOWS
from .config import contract,FAN_IN,WEIGHT_BOUND
from .sampling_report import COUNTS

def settings(selection):return dict(**contract(),partner_requests={k:1/3 for k in ('similar','dissimilar','random')},validation_feedback=False)
def choose_operator(rng):return 'independent-action-graphs'

def import_module(nodes,net,root):
    piece=prune(net,None if root==len(net.nodes)-1 else root);offset=len(nodes)
    nodes.extend(Neuron(tuple(s-offset if s<0 else s if s<INPUTS else s+offset for s in n.sources),n.weights,n.bias,n.uid,n.op) for n in piece.nodes)
    return INPUTS+len(nodes)-1

def compose(rng,a,b,whole):
    nodes=[];x=import_module(nodes,a,len(a.nodes)-1 if whole else int(rng.integers(len(a.nodes))))
    y=import_module(nodes,b,len(b.nodes)-1 if whole else int(rng.integers(len(b.nodes))))
    # Compose margins about the parents' thresholds. A weak secondary term
    # perturbs a useful primary decision instead of resetting its trigger.
    weights=(1.,float(rng.uniform(-.5,.5)))
    bias=-weights[0]*a.threshold-weights[1]*b.threshold if whole else 0.
    nodes.append(Neuron((x,y),weights,bias,int(rng.integers(1,2**63))))
    return Network(tuple(nodes),0.,a.size_source if whole and a.size_source>=0 else x-INPUTS,a.size_weight,a.size_bias).validate()

def exchange(rng,a,b):
    target=int(rng.integers(len(a.nodes)));piece=prune(b,int(rng.integers(len(b.nodes))))
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
            match=weights.get(key(a,source));out.append((w+match)/2 if match is not None and rng.random()<.5 else w)
        nodes.append(replace(node,weights=tuple(out)))
    return replace(a,nodes=tuple(nodes)).validate()

def cross(rng,a,b,operator=None):
    base,donor=(a,b) if rng.integers(2)==0 else (b,a)
    networks=dict(base.networks);ages=dict(base.minimum_age)
    changed=rng.choice(STAGES,size=int(rng.integers(1,6)),replace=False)
    for stage in changed:
        mode=str(rng.choice(('inherit','exchange','compose'),p=(.4,.3,.3)));COUNTS['requested_'+mode]+=1
        left,right=(donor,base) if mode=='inherit' else (base,donor)
        for attempt in range(8):
            COUNTS['module_attempt_'+mode]+=1
            try:
                if mode=='inherit':n=left.networks[stage]
                elif mode=='exchange':n=exchange(rng,left.networks[stage],right.networks[stage])
                else:n=compose(rng,left.networks[stage],right.networks[stage],rng.random()<.5)
            except ValueError:
                COUNTS['rejected_'+mode]+=1
                if attempt==7:raise
            else:break
        if rng.random()<.5:n=homologous_weights(rng,n,right.networks[stage])
        COUNTS['accepted_'+mode]+=1;networks[stage]=n
        if stage!='entry':ages[stage]=left.minimum_age.get(stage,0)
    # Keep the base action/policy combination, exchanging at most one policy
    # gene (or the coupled swing pair), rather than scattering every gene.
    genes={f.name:getattr(base.policy,f.name) for f in fields(Policy)}
    name=str(rng.choice([f.name for f in fields(Policy) if not f.name.startswith('swing_')]+['swing']))
    if name=='swing':genes.update(swing_left=donor.policy.swing_left,swing_right=donor.policy.swing_right)
    else:genes[name]=getattr(donor.policy,name)
    return Individual(networks,Policy(**genes),ages).validate(),'independent-action-graphs'

def reflected(x,bound):return float(bound-abs((x+bound)%(4*bound)-2*bound))
def parameter(rng,x,threshold=False):
    bound=.999999 if threshold else WEIGHT_BOUND
    return reflected(x+float(rng.normal(0,.1 if threshold else .2)),bound) if rng.random()<.8 else float(rng.uniform(-bound if threshold else -1,bound if threshold else 1))

def mutation(rng,parent,count,crossover_child=False):
    if not 1<=count<=5:raise ValueError('1..5 distinct edits required')
    child=parent;edits=[];used=set()
    for _ in range(256):
        if len(edits)==count:return child,edits
        stage=str(rng.choice(STAGES));net=child.networks[stage];nodes=list(net.nodes);index=int(rng.integers(len(nodes)));node=nodes[index]
        mode='parameter' if rng.random()<.7 else 'structure';field='';newnet=net;policy=child.policy;ages=dict(child.minimum_age)
        try:
            if mode=='parameter':
                field=str(rng.choice(('weight','bias','threshold','policy','age','size'),p=(.3,.15,.15,.15,.05,.2)))
                if field=='threshold':key=(stage,field);newnet=replace(net,threshold=parameter(rng,net.threshold,True))
                elif field=='policy':
                    name=str(rng.choice([f.name for f in fields(Policy) if not f.name.startswith('swing_')]+['swing']));key=('policy',name)
                    if name=='swing':
                        enabled=bool(rng.integers(2));policy=replace(policy,swing_left=int(rng.choice(SWING_WINDOWS)) if enabled else 0,swing_right=int(rng.choice(SWING_WINDOWS)) if enabled else 0)
                    else:policy=replace(policy,**{name:random_policy_gene(rng,name)})
                elif field=='size':
                    name=str(rng.choice(('size_source','size_weight','size_bias')));key=(stage,name)
                    value=int(rng.integers(len(net.nodes))) if name=='size_source' else parameter(rng,getattr(net,name))
                    newnet=replace(net,**{name:value})
                elif field=='age':
                    if stage=='entry':continue
                    key=(stage,field);ages[stage]=int(rng.integers(3601))
                elif field=='bias':key=(stage,field,index);nodes[index]=replace(node,bias=parameter(rng,node.bias));newnet=replace(net,nodes=tuple(nodes))
                elif rng.random()<.2:
                    key=(stage,'module_weights',index);nodes[index]=replace(node,weights=tuple(parameter(rng,w) for w in node.weights),bias=parameter(rng,node.bias));newnet=replace(net,nodes=tuple(nodes))
                else:
                    edge=int(rng.integers(len(node.weights)));key=(stage,field,index,edge);w=list(node.weights);w[edge]=parameter(rng,w[edge]);nodes[index]=replace(node,weights=tuple(w));newnet=replace(net,nodes=tuple(nodes))
            else:
                if any(e['stage']==stage for e in edits):continue
                field=str(rng.choice(('input','connect','disconnect','grow','remove','delay','operator')));key=(stage,'structure')
                if field=='operator':
                    from .genome import OPS
                    nodes[index]=replace(node,op=str(rng.choice(OPS)))
                elif field=='input':
                    e=int(rng.integers(len(node.sources)));sources=list(node.sources);sources[e]=random_input(rng,stage);nodes[index]=replace(node,sources=tuple(sources))
                elif field=='connect':
                    if len(node.sources)>=FAN_IN:continue
                    s=int(rng.integers(INPUTS+index))
                    if stage=='entry' and MARKET_INPUTS<=s<MARKET_INPUTS+7:continue
                    nodes[index]=replace(node,sources=node.sources+(s,),weights=node.weights+(float(rng.uniform(-1,1)),))
                elif field=='disconnect':
                    if len(node.sources)==1:continue
                    e=int(rng.integers(len(node.sources)));nodes[index]=replace(node,sources=node.sources[:e]+node.sources[e+1:],weights=node.weights[:e]+node.weights[e+1:])
                elif field=='delay':
                    e=int(rng.integers(len(node.sources)));sources=list(node.sources);sources[e]=-int(rng.integers(len(nodes)))-1;nodes[index]=replace(node,sources=tuple(sources))
                elif field=='grow':
                    if len(node.sources)>=FAN_IN:continue
                    old_source=int(rng.choice([s for s in node.sources if s>=0] or [random_input(rng,stage)]))
                    def shift(s):return s-1 if s<0 and -s-1>=index else s+1 if s>=INPUTS+index else s
                    nodes=[replace(n,sources=tuple(shift(s) for s in n.sources)) for n in nodes]
                    nodes.insert(index,Neuron((old_source,),(1.,),0.,int(rng.integers(1,2**63))))
                    target=nodes[index+1];nodes[index+1]=replace(target,sources=target.sources+(INPUTS+index,),weights=target.weights+(.05,))
                    if newnet.size_source>=index:newnet=replace(newnet,size_source=newnet.size_source+1)
                else:
                    options=[s-INPUTS for s in nodes[-1].sources if s>=INPUTS]
                    if not options:continue
                    newnet=prune(net,int(rng.choice(options)));nodes=list(newnet.nodes)
                # Local edits preserve the existing action boundary. Only an
                # explicit threshold mutation changes it.
                newnet=prune(replace(newnet,nodes=tuple(nodes)))
            if key in used or any(e['stage']==stage and e['field'].startswith('structure') for e in edits):continue
            candidate=Individual(dict(child.networks,**{stage:newnet}),policy,ages).validate()
            if candidate==child:continue
        except ValueError:continue
        child=candidate;used.add(key);edits.append(dict(stage=stage,field=mode+':'+field,gene=list(key)))
    raise RuntimeError('Distinct network mutation budget exhausted')
