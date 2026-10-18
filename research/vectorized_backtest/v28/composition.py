"""Generic graph inheritance, exchange and nonlinear composition only."""
from dataclasses import replace,fields
from .genome import Individual,Network,Neuron,Policy,STAGES,INPUTS,MARKET_INPUTS,random_input,prune,random_policy_gene,SWING_WINDOWS
from .config import contract,FAN_IN
from .sampling_report import COUNTS

def settings(selection):return dict(**contract(),partner_requests={k:1/3 for k in ('similar','dissimilar','random')},validation_feedback=False)
def choose_operator(rng):return 'independent-action-graphs'

def import_module(nodes,net,root):
    piece=prune(net,root);offset=len(nodes)
    nodes.extend(Neuron(tuple(s if s<INPUTS else s+offset for s in n.sources),n.weights,n.bias) for n in piece.nodes)
    return INPUTS+len(nodes)-1

def compose(rng,a,b,whole):
    nodes=[];x=import_module(nodes,a,len(a.nodes)-1 if whole else int(rng.integers(len(a.nodes))))
    y=import_module(nodes,b,len(b.nodes)-1 if whole else int(rng.integers(len(b.nodes))))
    # Compose margins about the parents' thresholds. A weak secondary term
    # perturbs a useful primary decision instead of resetting its trigger.
    weights=(1.,float(rng.uniform(-.5,.5)))
    bias=-weights[0]*a.threshold-weights[1]*b.threshold if whole else 0.
    nodes.append(Neuron((x,y),weights,bias))
    return Network(tuple(nodes),0.).validate()

def exchange(rng,a,b):
    target=int(rng.integers(len(a.nodes)));donor=int(rng.integers(len(b.nodes)));nodes=[];mapped={}
    def visit(i):
        if i in mapped:return mapped[i]
        if i==target:mapped[i]=import_module(nodes,b,donor);return mapped[i]
        n=a.nodes[i];sources=tuple(s if s<INPUTS else visit(s-INPUTS) for s in n.sources)
        nodes.append(Neuron(sources,n.weights,n.bias));mapped[i]=INPUTS+len(nodes)-1;return mapped[i]
    visit(len(a.nodes)-1)
    return Network(tuple(nodes),b.threshold if target==len(a.nodes)-1 else a.threshold).validate()

def cross(rng,a,b,operator=None):
    base,donor=(a,b) if rng.integers(2)==0 else (b,a)
    networks=dict(base.networks);ages=dict(base.minimum_age)
    changed=rng.choice(STAGES,size=int(rng.integers(1,3)),replace=False)
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
    bound=.999999 if threshold else 4.
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
                field=str(rng.choice(('weight','bias','threshold','policy','age'),p=(.4,.2,.2,.15,.05)))
                if field=='threshold':key=(stage,field);newnet=replace(net,threshold=parameter(rng,net.threshold,True))
                elif field=='policy':
                    name=str(rng.choice([f.name for f in fields(Policy) if not f.name.startswith('swing_')]+['swing']));key=('policy',name)
                    if name=='swing':
                        enabled=bool(rng.integers(2));policy=replace(policy,swing_left=int(rng.choice(SWING_WINDOWS)) if enabled else 0,swing_right=int(rng.choice(SWING_WINDOWS)) if enabled else 0)
                    else:policy=replace(policy,**{name:random_policy_gene(rng,name)})
                elif field=='age':
                    if stage=='entry':continue
                    key=(stage,field);ages[stage]=int(rng.integers(3601))
                elif field=='bias':key=(stage,field,index);nodes[index]=replace(node,bias=parameter(rng,node.bias));newnet=replace(net,nodes=tuple(nodes))
                else:
                    edge=int(rng.integers(len(node.weights)));key=(stage,field,index,edge);w=list(node.weights);w[edge]=parameter(rng,w[edge]);nodes[index]=replace(node,weights=tuple(w));newnet=replace(net,nodes=tuple(nodes))
            else:
                if any(e['stage']==stage for e in edits):continue
                field=str(rng.choice(('input','connect','disconnect','grow','remove')));key=(stage,'structure')
                if field=='input':
                    e=int(rng.integers(len(node.sources)));sources=list(node.sources);sources[e]=random_input(rng,stage);nodes[index]=replace(node,sources=tuple(sources))
                elif field=='connect':
                    if len(node.sources)>=FAN_IN:continue
                    s=int(rng.integers(INPUTS+index))
                    if stage=='entry' and MARKET_INPUTS<=s<INPUTS:continue
                    nodes[index]=Neuron(node.sources+(s,),node.weights+(float(rng.uniform(-1,1)),),node.bias)
                elif field=='disconnect':
                    if len(node.sources)==1:continue
                    e=int(rng.integers(len(node.sources)));nodes[index]=Neuron(node.sources[:e]+node.sources[e+1:],node.weights[:e]+node.weights[e+1:],node.bias)
                elif field=='grow':nodes.append(Neuron((INPUTS+len(nodes)-1,random_input(rng,stage)),tuple(float(x) for x in rng.uniform(-1,1,2)),float(rng.uniform(-1,1))))
                else:
                    options=[s-INPUTS for s in nodes[-1].sources if s>=INPUTS]
                    if not options:continue
                    newnet=prune(net,int(rng.choice(options)));nodes=list(newnet.nodes)
                # Local edits preserve the existing action boundary. Only an
                # explicit threshold mutation changes it.
                newnet=prune(Network(tuple(nodes),newnet.threshold))
            if key in used or any(e['stage']==stage and e['field'].startswith('structure') for e in edits):continue
            candidate=Individual(dict(child.networks,**{stage:newnet}),policy,ages).validate()
            if candidate==child:continue
        except ValueError:continue
        child=candidate;used.add(key);edits.append(dict(stage=stage,field=mode+':'+field,gene=list(key)))
    raise RuntimeError('Distinct network mutation budget exhausted')
