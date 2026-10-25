"""Bounded pedigree shared with the direct phase2 controller."""
import numpy as np
from .lineage import program_key
from research.vectorized_backtest.v42.complexity import count

class CompactPedigree:
    """Keep active parent metadata; retain birth evidence in generation seals."""
    def __init__(self,depth=5,records=None,next_serial=0):
        self.depth=depth;self.records={r['serial']:r for r in (records or [])};self.next_serial=next_serial
    def birth(self,members,generation,parents,operations):
        ids=np.arange(self.next_serial,self.next_serial+len(members),dtype=np.int64)
        for serial,member,pair,op in zip(ids,members,parents,operations):
            ancestors={};primary=self.records[int(pair[0])] if pair[0]>=0 else None
            for parent in pair:
                if parent<0:continue
                ancestors[int(parent)]=1
                for ancestor,distance in self.records[int(parent)]['ancestors']:
                    if distance<self.depth:ancestors[ancestor]=min(ancestors.get(ancestor,self.depth+1),distance+1)
            if len(ancestors)>2**(self.depth+1)-2:raise ValueError('Pedigree bound exceeded')
            mutations=(primary['mutations'] if primary else 0)+int('mutation' in op)
            crosses=(primary['crossovers'] if primary else 0)+int('crossover' in op)
            origin=primary['origin_generation'] if primary else generation;key=program_key(member)
            self.records[int(serial)]=dict(serial=int(serial),generation=generation,origin_generation=origin,parents=list(map(int,pair)),operation=op,
                mutations=mutations,crossovers=crosses,ancestors=sorted(ancestors.items()),program_sha256=key,case_owner=member.case_owner,complexity=count(member),
                label=f'G{origin:03d}-{serial:06d}-M{mutations:03d}-X{crosses:03d}-{key[:4]}')
        from research.vectorized_backtest.v42.family_stats import annotate
        for serial,pair in zip(ids,parents):
            annotate(self.records[int(serial)],[self.records[int(p)] for p in pair if p>=0])
        self.next_serial+=len(members);return ids
    def retain(self,ids):self.records={int(i):self.records[int(i)] for i in ids}
