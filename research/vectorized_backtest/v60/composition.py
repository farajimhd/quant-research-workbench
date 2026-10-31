"""Mask crossover entry point and inherited bounded structural mutations."""

from dataclasses import replace,fields

from .genome import Individual,Network,Neuron,Policy,STAGES,INPUTS,MARKET_INPUTS,random_input,prune,random_policy_gene,SWING_WINDOWS

from .config import contract,FAN_IN,WEIGHT_BOUND

from .sampling_report import COUNTS

from research.vectorized_backtest.v60.schedule import Proposal,SETTINGS,balanced,generation_root,new_uid

from itertools import combinations



def settings(selection):return dict(**contract(),reproduction_v42=SETTINGS,validation_feedback=False)

def choose_operator(rng):return 'independent-action-graphs'



def proposal(rng,purpose,parent=None):

    if isinstance(rng,Proposal):return rng

    from research.vectorized_backtest.v60.lineage import program_key

    return Proposal(generation_root(rng),(purpose,program_key(parent) if parent is not None else ''))





def cross(rng,a,b,operator=None):
    from .strategy_mix import leaves
    context=proposal(rng,'standalone-cross',a)
    a=leaves(a)[context.pick(len(leaves(a)),'left-branch')]
    b=leaves(b)[context.pick(len(leaves(b)),'right-branch')]
    from .mask_genome import cross as masked_cross
    return masked_cross(proposal(rng,'standalone-cross',a),a,b)


def reflected(x,bound):return float(bound-abs((x+bound)%(4*bound)-2*bound))

def parameter(rng,x,threshold=False):

    bound=.999999 if threshold else WEIGHT_BOUND

    reset=rng.pick(5,'parameter-reset')==0

    return float(rng.uniform(-bound if threshold else -1,bound if threshold else 1)) if reset else reflected(x+float(rng.normal(0,.1 if threshold else .2)),bound)





def mutation(rng,parent,count,crossover_child=False):

    if parent.routing:
        root=proposal(rng,'route-mutation',parent);branches=list(parent.routing)
        index=root.pick(len(branches),'branch');branches[index],edits=mutation(root,branches[index],count,crossover_child)
        return replace(branches[0],routing=tuple(branches)).validate(),[dict(e,branch=index) for e in edits]
    if not 1<=count<=5:raise ValueError('1..5 distinct edits required')

    root=proposal(rng,'standalone-mutation',parent);child=parent;edits=[];used=set()

    plans=root.plan.get('edits',[])

    for edit in range(count):

        if plans:scheduled=plans[edit]

        else:

            mode='parameter' if root.pick(10,'mutation-kind')<7 else 'structure'

            field=SETTINGS['parameter_fields'][int(balanced(20,SETTINGS['parameter_weights'])[root.pick(20,'parameter-kind')])] if mode=='parameter' else SETTINGS['structural_fields'][root.pick(len(SETTINGS['structural_fields']),'structural-kind')]

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

                        nodes[index]=replace(node,op=OPS[rng.pick(9,'new-operator')])

                    elif field=='input':

                        edge=rng.pick(len(node.sources),'edge');sources=list(node.sources);sources[edge]=random_input(rng,stage);nodes[index]=replace(node,sources=tuple(sources))

                    elif field=='connect':

                        if len(node.sources)>=FAN_IN:continue

                        from .authority import legal_sources
                        source=int(rng.choice(legal_sources(index,entry=stage=='entry')))

                        if stage=='entry' and MARKET_INPUTS<=source<MARKET_INPUTS+7:continue

                        nodes[index]=replace(node,sources=node.sources+(source,),weights=node.weights+(float(rng.uniform(-1,1)),))

                    elif field=='disconnect':

                        if len(node.sources)==1:continue

                        edge=rng.pick(len(node.sources),'edge');nodes[index]=replace(node,sources=node.sources[:edge]+node.sources[edge+1:],weights=node.weights[:edge]+node.weights[edge+1:])

                    elif field=='delay':

                        continue  # Delayed edges are forbidden in V60; rotate to a legal structural edit.

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

                from .authority import feasible_policy
                candidate=Individual(dict(child.networks,**{stage:newnet}),feasible_policy(policy),ages,child.case_owner).validate()

                if candidate==child:continue

            except ValueError:continue

            child=candidate;used.add(key);edits.append(dict(stage=stage,field=mode+':'+field,gene=list(key),scheduled=scheduled,attempt=attempt));break

        else:raise RuntimeError('Distinct network mutation budget exhausted')

    return child,edits
