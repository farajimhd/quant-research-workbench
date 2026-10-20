"""Deterministic training-only species, bounded representative comparisons."""
import time
from collections import Counter
import numpy as np
from .genome import INPUTS,MARKET_INPUTS,STAGES


def describe(member):
    features=set();parameters={};shape=[];sizes=[];depths=[];recurrences=0
    for stage,net in ((s,member.networks[s]) for s in STAGES):
        counts=Counter(n.uid for n in net.nodes);depth=[];delayed=edges=0
        for i,node in enumerate(net.nodes):
            depth.append(1+max((depth[s-INPUTS] for s in node.sources if s>=INPUTS),default=0))
            for source,w in zip(node.sources,node.weights):
                edges+=1;delayed+=int(source<0)
                if 0<=source<MARKET_INPUTS:features.add(str(source))
                if node.uid and counts[node.uid]==1:
                    if source>=INPUTS or source<0:
                        uid=net.nodes[-source-1 if source<0 else source-INPUTS].uid
                        if not uid or counts[uid]!=1:continue
                        identity=('r' if source<0 else 'n')+str(uid)
                    else:identity='x'+str(source)
                    parameters[f'{stage}:{node.uid}:{identity}']=float(w)
            if node.uid and counts[node.uid]==1:parameters[f'{stage}:{node.uid}:bias']=float(node.bias)
        n=len(net.nodes);d=max(depth);sizes.append(n);depths.append(d);recurrences+=delayed
        from .genome import OPS
        shape.extend([n,d,edges/max(1,n),delayed/max(1,edges)]+[sum(x.op==op for x in net.nodes)/n for op in OPS])
    return dict(features=sorted(features),parameters=parameters,shape=shape,sizes=sizes,depths=depths,recurrent_edges=recurrences)


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
    rows=[[] for _ in range(n)];active=np.zeros(n,dtype=bool)
    lower=np.arange(48)*1800
    for metrics in ordered:
        entries=[np.asarray(x,dtype=np.int64) for x in metrics['episode_entry_clocks']]
        lengths=np.asarray([len(x) for x in entries]);active|=lengths>0
        owners=np.repeat(np.arange(n),lengths)
        entry=np.concatenate(entries);exits=np.concatenate([np.asarray(x,dtype=np.int64) for x in metrics['episode_exit_clocks']])
        listings=np.concatenate([np.asarray(x,dtype=np.int64) for x in metrics['episode_listings']])
        time_bins=np.bincount(owners*48+np.minimum(entry//1800,47),minlength=n*48).reshape(n,48)
        listing_bins=np.bincount(owners*32+listings%32,minlength=n*32).reshape(n,32)
        holding=np.zeros((n,48),dtype=np.int64)
        # Integer scatter sums preserve the original ledger arithmetic exactly.
        # Process one clock bin at a time, avoiding an episodes-by-48 arena.
        for j,start in enumerate(lower):
            duration=np.maximum(0,np.minimum(exits,start+1800)-np.maximum(entry,start))
            np.add.at(holding[:,j],owners,duration)
        values=np.column_stack((time_bins,holding/1800,listing_bins,
            metrics['closed_positions'],np.asarray(metrics['capital_dollar_seconds'])/3600000,
            np.asarray(metrics['turnover'])/1000))
        for row,value in zip(rows,np.log1p(values).tolist()):row.extend(value)
    return rows,active


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
        from .species_distance import DistanceIndex
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
