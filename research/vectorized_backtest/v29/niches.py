"""Bounded structural niches; financial objectives remain unmodified."""
import numpy as np

def signature(member):
    sizes=[len(n.nodes) for n in member.networks.values()]
    delayed=sum(s<0 for n in member.networks.values() for node in n.nodes for s in node.sources)
    return (sum(sizes).bit_length(),max(sizes).bit_length(),min(3,delayed.bit_length()))

def groups(population,order):
    result={}
    for i in order:result.setdefault(signature(population[int(i)]),[]).append(int(i))
    return list(result.values())

def elite_order(population,order,budget):
    niches=groups(population,order)
    champions=[g[0] for g in niches[:min(16,budget//2)]]
    champion_set=set(champions)
    return np.asarray(champions+[int(i) for i in order if i not in champion_set],dtype=int),niches
