"""Training-only sparse intervals; bounded neighbor queries, no dense exact NxN."""
import numpy as np

ENTRY_TOLERANCE=60
NEIGHBOR_PROPOSALS=32
EXACT_PROPOSALS=4
MAXIMUM_PAIR_CACHE=8192


def integral(starts,ends,points):
    prefix=np.r_[0,np.cumsum(ends-starts,dtype=np.int64)]
    i=np.searchsorted(ends,points,side='right')
    clipped=np.minimum(i,len(starts)-1)
    return prefix[i]+np.where(i<len(starts),np.clip(points-starts[clipped],0,ends[clipped]-starts[clipped]),0)


class BehaviorIndex:
    def __init__(self,ordered):
        self.cache={};self.exact_queries=0;self.candidates=[]
        n=len(ordered[0]['net_pnl']);self.sketch=np.zeros((n,256),dtype=bool)
        self.available=all('episode_entry_clocks' in m and 'episode_exit_clocks' in m for m in ordered)
        for i in range(n):
            starts=[];ends=[]
            if self.available:
                for day,m in enumerate(ordered):
                    listings=np.asarray(m['episode_listings'][i],dtype=np.int64)
                    entry=np.asarray(m['episode_entry_clocks'][i],dtype=np.int64)
                    exit=np.asarray(m['episode_exit_clocks'][i],dtype=np.int64)
                    if not(len(listings)==len(entry)==len(exit)) or np.any(entry>exit):raise ValueError('Invalid training intervals')
                    if np.any(entry<0) or np.any(exit>=86400):raise ValueError('Behavior clock bound exceeded')
                    base=(day*1048576+listings)*86400
                    starts.append(base+entry);ends.append(base+np.maximum(exit,entry+1))
                a=np.concatenate(starts);b=np.concatenate(ends);order=np.argsort(a,kind='stable');a,b=a[order],b[order]
                if len(a)>1 and np.any(a[1:]<b[:-1]):raise ValueError('Overlapping positions in one listing lane')
            else:a=b=np.empty(0,dtype=np.int64)
            self.candidates.append((a,b))
            if len(a):
                # Entry and holding endpoints only; no expansion across every held second.
                tokens=np.r_[a//60,(a+b)//120,b//60].astype(np.uint64)
                with np.errstate(over='ignore'):hashed=tokens*np.uint64(11400714819323198485)
                self.sketch[i,np.unique((hashed^(hashed>>32))%256).astype(int)]=True
        self.active=np.array([len(a)>0 for a,b in self.candidates])

    def similarity(self,i,j):
        key=tuple(sorted((int(i),int(j))))
        if key in self.cache:return self.cache[key]
        a,b=self.candidates[i];c,d=self.candidates[j]
        if not(len(a) and len(c)):return None
        overlap=int((integral(c,d,b)-integral(c,d,a)).sum());da=int((b-a).sum());db=int((d-c).sum())
        holding=overlap/max(1,da+db-overlap)
        # One-to-one chronological entry matching, never many entries to one entry.
        left=right=matches=0
        while left<len(a) and right<len(c):
            if a[left]//86400==c[right]//86400 and abs(int(a[left]-c[right]))<=ENTRY_TOLERANCE:
                matches+=1;left+=1;right+=1
            elif a[left]<c[right]:left+=1
            else:right+=1
        entry=matches/max(1,len(a)+len(c)-matches)
        result=dict(score=.5*(holding+entry),holding=holding,entry=entry,coverage_left=overlap/max(1,da),coverage_right=overlap/max(1,db))
        self.exact_queries+=1
        if len(self.cache)<MAXIMUM_PAIR_CACHE:self.cache[key]=result
        return result

    def partner(self,rng,a,options,route):
        options=np.asarray(options,dtype=int)
        if route=='random' or not self.available or not self.active[a]:return int(rng.choice(options)),None
        options=options[self.active[options]]
        if not len(options):return None,None
        probes=rng.choice(options,min(NEIGHBOR_PROPOSALS,len(options)),replace=False)
        x=self.sketch[a];y=self.sketch[probes];intersection=(y&x).sum(1);union=(y|x).sum(1)
        approx=intersection/np.maximum(union,1);order=np.argsort(approx,kind='stable')
        selected=probes[order[-EXACT_PROPOSALS:] if route=='similar' else order[:EXACT_PROPOSALS]]
        exact=[(int(b),self.similarity(a,int(b))) for b in selected]
        chosen=(max if route=='similar' else min)(exact,key=lambda p:p[1]['score'])
        return chosen
