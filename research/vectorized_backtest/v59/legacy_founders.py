"""Explicit diagnostic conversion, never used to initialize the V59 campaign.

Remove delayed/feedback/forbidden edges without sampling replacements. Empty or
malformed operators become inactive nodes. Retain every founder and report the
loss of inputs; this is not an unchanged-genome execution comparison.
"""
from collections import Counter
from .genome import Individual,Network,Neuron,INPUTS,MARKET_INPUTS,STAGES,prune
from .config import STATES
from .feature_policy import BLOCKED,ALLOWED,CATALOG
from research.vectorized_backtest.v42.config import STATES as OLD_STATES
from research.vectorized_backtest.v27.genome import Policy

VERSION='v45-to-v59-feedforward-diagnostic-v1'


def convert(payload):
    counts=Counter();nets={};old_inputs=MARKET_INPUTS+len(OLD_STATES)
    for stage in STAGES:
        saved=payload['networks'][stage];nodes=[]
        for i,node in enumerate(saved['nodes']):
            sources=[];weights=[]
            for s,w in zip(node['sources'],node['weights']):
                reason=None
                if s<0:reason='delayed_edges_removed'
                elif s<MARKET_INPUTS:
                    if s%len(CATALOG) in BLOCKED:reason='forbidden_feature_edges_removed'
                elif s<old_inputs:
                    name=OLD_STATES[s-MARKET_INPUTS]
                    if name not in STATES:reason='feedback_edges_removed'
                    elif stage=='entry' and name!='elapsed_seconds':reason='entry_state_edges_removed'
                    else:s=MARKET_INPUTS+STATES.index(name)
                else:s=INPUTS+s-old_inputs
                if reason:counts[reason]+=1
                else:sources.append(s);weights.append(w)
            op=node.get('op','tanh');bias=node['bias']
            if not sources or (op=='attention' and (len(sources)<3 or len(sources)%2!=1)):
                sources=[ALLOWED[0]];weights=[0.];bias=0.;op='identity';counts['inactive_nodes']+=1
            nodes.append(Neuron(tuple(sources),tuple(weights),bias,node.get('uid',0),op,node.get('slot',i+1)))
        net=prune(Network(tuple(nodes),saved['threshold'],saved.get('size_source',-1),saved.get('size_weight',1.),saved.get('size_bias',0.)))
        counts['unreachable_nodes_removed']+=len(nodes)-len(net.nodes)
        if not any(0<=s<2*len(CATALOG) for n in net.nodes for s in n.sources):
            net=Network((Neuron((ALLOWED[0],),(0.,),0.,nodes[-1].uid,'identity',nodes[-1].slot),),saved['threshold'])
            counts['inactive_actions_without_market_input']+=1
        nets[stage]=net
    member=Individual(nets,Policy(**payload['policy']),payload.get('minimum_age',{})).validate()
    return member,dict(counts)
