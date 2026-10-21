"""Bounded generation-local CPU descriptors; no fitness, validation or RNG."""
from collections import OrderedDict
from time import perf_counter
import numpy as np
from .genome import INPUTS,MARKET_INPUTS,OPS
from .features import CATALOG


def bounded_probe(probe):
    if probe.get('metadata',{}).get('validation_opened') is not False:
        raise ValueError('Crossover probe must be certified training-only')
    result={k:np.array(probe[k][:1,:8,:8],copy=True,order='C') for k in ('inputs','known','held_states','eligible')}
    result['metadata']=dict(probe['metadata'])
    for key,array in result.items():
        if key!='metadata':array.flags.writeable=False
    return result


class CrossoverContext:
    def __init__(self,probe=None,capacity=256):
        if not isinstance(capacity,int) or capacity<1:raise ValueError('Positive bounded descriptor cache required')
        self.cache=OrderedDict();self.capacity=capacity
        self.telemetry=dict(descriptor_hits=0,descriptor_misses=0,profile_builds=0,
            profile_seconds=0.,matching_requests=0,interface_proposals=0,interface_skipped=0)
        self.probe=None
        if probe is not None:
            # Fixed chronological prefix, independent of candidates and RNG.
            self.probe=bounded_probe(probe)

    def describe(self,net,stage,*,profile=False):
        key=(id(net),stage)
        if key in self.cache:
            self.telemetry['descriptor_hits']+=1;self.cache.move_to_end(key)
            record=self.cache[key]
        else:
            self.telemetry['descriptor_misses']+=1
            n=len(net.nodes);signature=np.zeros((n,16),np.float64)
            for i,node in enumerate(net.nodes):
                role=signature[i]
                for source,w in zip(node.sources,node.weights):
                    if not w:continue
                    if source<0:
                        role[13]+=1
                        # Bounded role sketch; full recurrent arithmetic is
                        # measured below, not approximated by this signature.
                        j=-source-1
                        if j<i:role[:13]+=signature[j,:13]
                    elif source<MARKET_INPUTS:
                        feature=source%len(CATALOG)
                        role[feature%8]+=1;role[8+source//len(CATALOG)]+=1
                    elif source<INPUTS:role[11 if source-MARKET_INPUTS<7 else 12]+=1
                    else:
                        role[:14]+=signature[source-INPUTS,:14]
                        role[14]=max(role[14],signature[source-INPUTS,14])
                norm=np.linalg.norm(role[:13]);role[:13]/=max(norm,1e-12)
                role[13]/=max(1,len(node.sources));role[14]+=1;role[15]=len(node.sources)
            record=dict(net=net,signature=signature,stats=None)
            self.cache[key]=record
            if len(self.cache)>self.capacity:self.cache.popitem(last=False)
        if profile and self.probe is not None and record['stats'] is None:
            began=perf_counter();n=len(net.nodes);width=max(len(x.sources) for x in net.nodes)
            sources=np.zeros((n,width),np.int32);weights=np.zeros((n,width),np.float32)
            for i,node in enumerate(net.nodes):
                sources[i,:len(node.sources)]=node.sources;weights[i,:len(node.weights)]=node.weights
            from .module_stats import scan_modules
            sums,squares,counts=scan_modules(self.probe['inputs'],self.probe['known'],self.probe['held_states'],sources,weights,
                np.asarray([x.bias for x in net.nodes],np.float32),np.asarray([len(x.sources) for x in net.nodes],np.int32),
                np.asarray([OPS.index(x.op) for x in net.nodes],np.int32),MARKET_INPUTS,len(CATALOG),stage=='entry')
            mean=sums/np.maximum(counts,1);std=np.sqrt(np.maximum(0,squares/np.maximum(counts,1)-mean**2))
            record['stats']=np.column_stack((mean,std,counts))
            self.telemetry['profile_builds']+=1;self.telemetry['profile_seconds']+=perf_counter()-began
        return record

    def donor_options(self,a,target,b,stage):
        self.telemetry['matching_requests']+=1
        one=self.describe(a,stage,profile=True);two=self.describe(b,stage,profile=True)
        x=one['signature'][target];y=two['signature']
        feature=1-np.clip(y[:,:13]@x[:13],0,1)
        temporal=np.abs(y[:,13]-x[13])
        shape=np.mean(np.abs(y[:,14:]-x[14:])/np.maximum(1,np.maximum(y[:,14:],x[14:])),axis=1)
        output=np.zeros(len(b.nodes))
        if one['stats'] is not None and two['stats'] is not None:
            aa=one['stats'][target,:2];bb=two['stats'][:,:2]
            output=np.mean(np.minimum(1,np.abs(bb-aa)),axis=1)
        distance=.45*feature+.20*temporal+.15*shape+.20*output
        # Stable tie-break; no process-dependent hash or approximate ANN index.
        return np.lexsort((np.arange(len(b.nodes)),distance))[:4]

    def interface(self,a,target,b,root,stage):
        one=self.describe(a,stage,profile=True)['stats'];two=self.describe(b,stage,profile=True)['stats']
        if one is None or two is None or min(one[target,2],two[root,2])<8 or min(one[target,1],two[root,1])<1e-4:
            self.telemetry['interface_skipped']+=1;return None
        scale=float(np.clip(one[target,1]/two[root,1],.25,4.))
        offset=float(one[target,0]-scale*two[root,0])
        self.telemetry['interface_proposals']+=1
        return scale,offset
