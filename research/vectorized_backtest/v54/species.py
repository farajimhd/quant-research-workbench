"""Deterministic training-only species, bounded representative comparisons."""
import time
from collections import Counter
import numpy as np
from .genome import INPUTS,MARKET_INPUTS,STAGES


def describe(member):
    n=member.networks['shared'];features=set();parameters={};edges=delayed=0
    for i,x in enumerate(n.nodes):
        parameters[f'core:{i}:bias']=x.bias
        for e,(source,w) in enumerate(zip(x.sources,x.weights)):
            edges+=1;delayed+=int(source<0)
            if 0<=source<MARKET_INPUTS:features.add(str(source))
            parameters[f'core:{i}:source:{source}']=w
    for a,h in enumerate(n.heads):
        for k,b in enumerate(h.branches):
            parameters[f'head:{a}:{k}:bias']=b.bias;parameters[f'head:{a}:{k}:threshold']=b.threshold
            for source,w in zip(b.sources,b.weights):parameters[f'head:{a}:{k}:source:{source}']=w
        parameters[f'head:{a}:size_w']=h.size_weight;parameters[f'head:{a}:size_b']=h.size_bias
    count=len(n.nodes)+sum(len(h.branches)+(h.mode!='plain') for h in n.heads)
    shape=[count,2,edges/64,delayed/max(1,edges)]+[sum(h.mode==mode for h in n.heads)/6 for mode in ('plain','union','intersection','xor','xnor')]
    return dict(features=sorted(features),parameters=parameters,shape=shape,sizes=[count],depths=[2],recurrent_edges=delayed)


def distance(a,b):
    aa=set(a['features']);bb=set(b['features']);feature=1-len(aa&bb)/max(1,len(aa|bb))
    x=np.asarray(a['shape']);y=np.asarray(b['shape']);struct=.5*feature+.5*float(np.mean(abs(x-y)/np.maximum(1,np.maximum(abs(x),abs(y)))))
    common=sorted(a['parameters'].keys()&b['parameters'].keys())
    parameter=float(np.mean([min(1,abs(a['parameters'][k]-b['parameters'][k])/16) for k in common])) if common else .5
    if a.get('active') and b.get('active'):
        x=np.asarray(a['behavior']);y=np.asarray(b['behavior']);behavior=float(np.mean(abs(x-y)/np.maximum(1,np.maximum(abs(x),abs(y)))))
        return .4*struct+.2*parameter+.4*behavior
    # Inactivity supplies no evidence of behavioral similarity.
    return (.4*struct+.2*parameter)/.6


def training_behavior(ordered,n):
    parts=[];active=np.zeros(n,dtype=bool);lower=np.arange(48)*1800;upper=lower+1800
    for metrics in ordered:
        values=np.empty((n,131),dtype=np.float64)
        for i in range(n):
            entries=np.asarray(metrics['episode_entry_clocks'][i],dtype=np.int64)
            listings=np.asarray(metrics['episode_listings'][i],dtype=np.int64)
            active[i]|=len(entries)>0
            # Bounded clock/listing sketches plus size-sensitive realized exposure.
            time_bins=np.bincount(np.minimum(entries//1800,47),minlength=48)
            listing_bins=np.bincount(listings%32,minlength=32)
            exits=np.asarray(metrics['episode_exit_clocks'][i],dtype=np.int64)
            holding=np.maximum(0,np.minimum(exits[:,None],upper)-np.maximum(entries[:,None],lower)).sum(0)/1800
            values[i,:48]=time_bins;values[i,48:96]=holding;values[i,96:128]=listing_bins
            values[i,128]=metrics['closed_positions'][i];values[i,129]=metrics['capital_dollar_seconds'][i]/3600000;values[i,130]=metrics['turnover'][i]/1000
        parts.append(np.log1p(values))
    return np.concatenate(parts,axis=1).tolist(),active


class Species:
    def __init__(self,state=None):
        self.state=state or dict(next_id=0,threshold=.5,records=[],generation=0)
        self.telemetry={};self.labels=[];self.descriptors=[];self.groups={}
    def assign(self,population,ids,profits,ordered,generation,eligible=None):
        from .cpu_work import descriptors as describe_population
        started=time.perf_counter();descriptors=describe_population(population);behaviors,active=training_behavior(ordered,len(population))
        for i,d in enumerate(descriptors):d.update(behavior=behaviors[i],active=bool(active[i]))
        eligible=np.ones(len(population),dtype=bool) if eligible is None else np.asarray(eligible,dtype=bool)
        previous={r['id']:r for r in self.state['records']};representatives={i:r['representative'] for i,r in previous.items()};groups={};labels=[None]*len(population);capped=0
        from research.vectorized_backtest.v42.species_distance import DistanceIndex
        distance_index=DistanceIndex(representatives)
        for i in np.argsort(ids,kind='stable'):
            if not eligible[int(i)]:continue
            distances=distance_index.distances(descriptors[int(i)]);best=min(distances,default=(2,None))
            if best[0]<=self.state['threshold'] or len(representatives)>=64:
                sid=best[1];capped+=int(best[0]>self.state['threshold'])
            else:
                sid=self.state['next_id'];self.state['next_id']+=1;representatives[sid]=descriptors[int(i)]
                distance_index.add(sid,descriptors[int(i)])
            labels[int(i)]=sid;groups.setdefault(sid,[]).append(int(i))
        records=[]
        for sid,indexes in sorted(groups.items()):
            champion=min(indexes,key=lambda i:(-profits[i],ids[i]));old=previous.get(sid);best=float(profits[champion]);improved=old is None or best>old['best_profit']+1e-9
            last=generation if improved else old['last_improved'];record=dict(id=sid,best_profit=best if improved else old['best_profit'],last_improved=last,stagnant=generation-last>=10,
                champion_index=champion,champion_serial=int(ids[champion]),size=len(indexes),representative=descriptors[champion])
            records.append(record)
        threshold=self.state['threshold']*(1.05 if len(records)>32 else .95 if len(records)<16 else 1.)
        self.state.update(records=records,generation=generation,threshold=float(np.clip(threshold,.15,.85)))
        self.labels=labels;self.descriptors=descriptors;self.groups=groups
        telemetry_start=time.perf_counter()
        sizes=np.asarray([sum(d['sizes']) for d in descriptors]);depths=np.asarray([max(d['depths']) for d in descriptors]);recurrence=np.asarray([d['recurrent_edges'] for d in descriptors])
        self.telemetry=dict(species_count=len(records),stagnant_species=sum(r['stagnant'] for r in records),species=[{k:r[k] for k in ('id','size','best_profit','last_improved','stagnant','champion_serial')} for r in records],
            neurons=dict(min=int(sizes.min()),median=float(np.median(sizes)),max=int(sizes.max()),buckets={str(k):int(np.sum((sizes<=k)&(sizes>(0 if j==0 else [8,16,32,64,128,256,512][j-1])))) for j,k in enumerate([8,16,32,64,128,256,512])}),
            depth=dict(median=float(np.median(depths)),max=int(depths.max())),recurrent_edges=dict(median=float(np.median(recurrence)),max=int(recurrence.max())),representative_cap_assignments=capped,species_seconds=time.perf_counter()-started,validation_feedback=False)
        self.telemetry['telemetry_seconds']=time.perf_counter()-telemetry_start
        return labels
