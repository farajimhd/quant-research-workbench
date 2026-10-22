"""Compiled notebook-06 semantics with compact, reconstructable score history.

Loops over time remain necessary for causal break/retest state. Numba executes
those loops on numeric arrays; no Python dictionary per band/event is stored.
No compilation cache is written into the source tree.
"""
import ast
import json
from pathlib import Path
import time
import numpy as np
import polars as pl
from numba import njit


@njit(cache=False)
def _cores(prices, scores):
    n=len(prices)
    starts=np.empty(n,np.int64);ends=np.empty(n,np.int64);vals=np.empty(n)
    m=0
    for i in range(n):
        if m==0 or scores[i]!=vals[m-1]:
            starts[m]=i;ends[m]=i;vals[m]=scores[i];m+=1
        else:ends[m-1]=i
    kinds=np.zeros(m,np.int8)
    for j in range(m):
        peak=m>1 and (j==0 or vals[j]>vals[j-1]) and (j==m-1 or vals[j]>vals[j+1])
        valley=j>0 and j<m-1 and vals[j]<vals[j-1] and vals[j]<vals[j+1]
        kinds[j]=1 if peak else -1 if valley else 0
    left=np.zeros(m);right=np.zeros(m);v=0.
    for j in range(m):
        left[j]=v
        if kinds[j]==-1:v=vals[j]
    v=0.
    for j in range(m-1,-1,-1):
        right[j]=v
        if kinds[j]==-1:v=vals[j]
    out=np.empty((m,2),np.int64);k=0
    for j in range(m):
        if kinds[j]!=1:continue
        floor=max(left[j],right[j]);center=(starts[j]+ends[j])//2
        lo=center;hi=center
        while lo>0 and scores[lo-1]>floor:lo-=1
        while hi+1<n and scores[hi+1]>floor:hi+=1
        if scores[center]>floor:out[k,0]=lo;out[k,1]=hi;k+=1
    return out[:k]


@njit(cache=False)
def _kernel(price_values,rank,ts,volume,discovery,tau):
    n=len(rank);u=len(price_values)
    stats=np.zeros((u,2,3));seen=np.zeros(u,np.bool_);first=np.zeros(u,np.int64)
    owner=np.full(u,-1,np.int64)
    # Maximum number of non-overlapping zones is bounded by observed prices.
    lower=np.empty(u,np.int64);upper=np.empty(u,np.int64);zone_role=np.empty(u,np.int64)
    zone_period=np.empty(u,np.int64);zone_count=0;ztotals=np.zeros((u,2,3))
    periods=[];ends=[];initial=[];live=[];pending=[];updates=[]
    normalizers=np.ones((n,2,2));norm=np.ones((2,2))
    deferred=np.zeros(2,np.int64);profile_builds=0
    cand=np.empty((2*u,4),np.int64);candscore=np.empty(2*u);ncand=0
    counts=np.zeros((n,4),np.int64)
    for i in range(n):
        r=rank[i];event=i+1;changed=not seen[r]
        if not seen[r]:seen[r]=True;first[r]=event
        if i>=2:
            prev=rank[i-1];arrival=np.sign(prev-rank[i-2]);departure=np.sign(r-prev)
            role=0 if arrival==-1 and departure==1 else 1 if arrival==1 and departure==-1 else -1
            if role>=0:
                changed=True
                delta=(ts[i]-ts[i-1])/1e6
                stats[prev,role,0]+=1.;stats[prev,role,1]+=volume[i-1];stats[prev,role,2]+=delta
                z=owner[prev]
                if z>=0:
                    ztotals[z,role,0]+=1.;ztotals[z,role,1]+=volume[i-1];ztotals[z,role,2]+=delta
                    updates.append((float(event),float(z),float(role),ztotals[z,role,0],ztotals[z,role,1],ztotals[z,role,2]))
        if event<discovery:continue
        if changed or event==discovery:
            profile_builds+=1
            observed=np.where(seen)[0];k=len(observed)
            pref=np.zeros((k+1,2,3))
            for j in range(k):
                for role in range(2):
                    for col in range(3):pref[j+1,role,col]=pref[j,role,col]+stats[observed[j],role,col]
            ncand=0
            for role in range(2):
                maxcount=1.;maxvol=1.
                for j in range(k):
                    maxcount=max(maxcount,stats[observed[j],role,0]);maxvol=max(maxvol,stats[observed[j],role,1])
                scores=np.zeros(k)
                for j in range(k):
                    cnt=stats[observed[j],role,0]
                    if cnt>0:
                        scores[j]=(cnt/maxcount+stats[observed[j],role,1]/maxvol+tau/(tau+stats[observed[j],role,2]/cnt))/3
                cores=_cores(observed,scores);begin=ncand;norm[role,0]=1.;norm[role,1]=1.
                for c in range(len(cores)):
                    lo=cores[c,0];hi=cores[c,1]
                    cand[ncand,0]=observed[lo];cand[ncand,1]=observed[hi];cand[ncand,2]=role
                    origin=first[observed[lo]]
                    for j in range(lo+1,hi+1):origin=min(origin,first[observed[j]])
                    cand[ncand,3]=origin
                    cnt=pref[hi+1,role,0]-pref[lo,role,0];vol=pref[hi+1,role,1]-pref[lo,role,1]
                    norm[role,0]=max(norm[role,0],cnt);norm[role,1]=max(norm[role,1],vol)
                    ncand+=1
                for c in range(begin,ncand):
                    lo=np.searchsorted(observed,cand[c,0]);hi=np.searchsorted(observed,cand[c,1])+1
                    cnt=pref[hi,role,0]-pref[lo,role,0];vol=pref[hi,role,1]-pref[lo,role,1];delay=pref[hi,role,2]-pref[lo,role,2]
                    candscore[c]=(min(cnt/norm[role,0],1.)+min(vol/norm[role,1],1.)+tau/(tau+delay/cnt))/3 if cnt>0 else 0.
            # Stable ordering preserves role/price tie order from notebook 06.
            order=np.argsort(-candscore[:ncand],kind='mergesort')
            cand[:ncand]=cand[:ncand][order];candscore[:ncand]=candscore[:ncand][order]
        normalizers[i]=norm
        # Process active periods in activation order, preserving reference transition IDs.
        j=0
        while j<len(live):
            z=live[j];role=zone_role[z]
            broken=r<lower[z] if role==0 else r>upper[z]
            if broken:
                ends[zone_period[z]]=event;pending.append(z);live.pop(j)
            else:j+=1
        j=0
        while j<len(pending):
            z=pending[j];parent=zone_period[z];role=1-zone_role[z]
            inside=lower[z]<=r<=upper[z]
            approach=rank[i-1]<lower[z] if role==1 else rank[i-1]>upper[z]
            if ends[parent]<event and inside and approach:
                period=len(periods);periods.append((z,role,event,event,parent));ends.append(0)
                cnt=ztotals[z,role,0];vol=ztotals[z,role,1];delay=ztotals[z,role,2]
                score=(min(cnt/norm[role,0],1.)+min(vol/norm[role,1],1.)+tau/(tau+delay/cnt))/3 if cnt>0 else 0.
                initial.append(score);zone_role[z]=role;zone_period[z]=period;live.append(z);pending.pop(j)
            else:j+=1
        for c in range(ncand):
            lo=cand[c,0];hi=cand[c,1];role=cand[c,2]
            valid=r>=lo if role==0 else r<=hi
            if not valid:deferred[1]+=1;continue
            occupied=False
            for j in range(lo,hi+1):
                if owner[j]>=0:occupied=True;break
            if occupied:deferred[0]+=1;continue
            z=zone_count;zone_count+=1;lower[z]=lo;upper[z]=hi;zone_role[z]=role
            for j in range(lo,hi+1):
                owner[j]=z
                for rr in range(2):
                    for col in range(3):ztotals[z,rr,col]+=stats[j,rr,col]
            for rr in range(2):
                updates.append((float(event),float(z),float(rr),ztotals[z,rr,0],ztotals[z,rr,1],ztotals[z,rr,2]))
            period=len(periods);periods.append((z,role,cand[c,3],event,-1));ends.append(0);initial.append(candscore[c])
            zone_period[z]=period;live.append(z)
        counts[i,0]=zone_count;counts[i,1]=len(live);counts[i,2]=len(pending);counts[i,3]=len(periods)
    # Explicit shapes also support no-peak/flat streams with zero periods.
    period_array=np.empty((len(periods),5),np.int64)
    for j in range(len(periods)):
        for col in range(5):period_array[j,col]=periods[j][col]
    update_array=np.empty((len(updates),6),np.float64)
    for j in range(len(updates)):
        for col in range(6):update_array[j,col]=updates[j][col]
    return (period_array,np.array(ends),np.array(initial),lower[:zone_count],upper[:zone_count],
            update_array,normalizers,counts,deferred,profile_builds)


def prepare(frame):
    """Vectorized chronology checks and coordinate compression (no future evidence)."""
    assert frame.height>=200
    assert frame.select('time_us','ordinal').equals(frame.sort('time_us','ordinal').select('time_us','ordinal'))
    assert frame['ordinal'].n_unique()==frame.height
    prices=frame['price_units'].to_numpy().astype(np.int64)
    sizes=frame['volume'].to_numpy()
    assert (prices>0).all() and (sizes>0).all() and np.isfinite(sizes).all()
    values,rank=np.unique(prices,return_inverse=True)
    return values,rank.astype(np.int64),frame['time_us'].to_numpy().astype(np.int64),frame['volume'].to_numpy().astype(np.float64)


def run_fast(frame,session_open_us,discovery=200,tau=1.):
    assert 3<=discovery<=frame.height and np.isfinite(tau) and tau>0
    prepared=prepare(frame);start=time.perf_counter()
    result=_kernel(*prepared,discovery,tau)
    return FastReplay(frame,prepared,result,time.perf_counter()-start,discovery,tau,session_open_us)


class FastReplay:
    def __init__(self,frame,prepared,result,seconds,discovery,tau,session_open_us):
        self.frame=frame;self.values,self.rank,self.times,self.volumes=prepared
        (self.periods,self.ends,self.initial,self.lower,self.upper,self.updates,self.norm,
         self.counts,self.deferred,self.profile_builds)=result
        self.seconds=seconds;self.discovery=discovery;self.tau=tau
        self.session_open=int(session_open_us)
        assert self.times[0]>=self.session_open
        self.index={}
        # Build each compact evidence series once, not once per slider movement.
        if len(self.updates):
            order=np.lexsort((self.updates[:,0],self.updates[:,2],self.updates[:,1]))
            sorted_updates=self.updates[order]
            keys=sorted_updates[:,1:3].astype(np.int64)
            cuts=np.r_[0,np.flatnonzero(np.any(keys[1:]!=keys[:-1],axis=1))+1,len(keys)]
            for a,b in zip(cuts[:-1],cuts[1:]):self.index[tuple(keys[a])]=sorted_updates[a:b]
        self.child={int(p[4]):i for i,p in enumerate(self.periods) if p[4]>=0}

    def components(self,band,role,events):
        events=np.asarray(events,dtype=np.int64)
        series=self.index[(int(band),int(role))]
        at=np.searchsorted(series[:,0],events,side='right')-1
        assert (at>=0).all()
        vals=series[at,3:6];count,volume,delay=vals.T
        norm=self.norm[events-1,role,:]
        mean=np.divide(delay,count,out=np.zeros_like(delay),where=count>0)
        base=np.where(count>0,(np.minimum(count/norm[:,0],1)+np.minimum(volume/norm[:,1],1)+self.tau/(self.tau+mean))/3,0.)
        return base,vals

    def samples(self,period,events):
        band,role,origin,start,parent=self.periods[period]
        events=np.asarray(events,dtype=np.int64)
        base,vals=self.components(band,role,events)
        age=(self.times[events-1]-self.times[start-1])/1e6
        elapsed=(self.times[events-1]-self.session_open)/1e6
        fraction=np.divide(age,elapsed,out=np.zeros_like(age),where=elapsed>0)
        return base+(1-base)*fraction,base,vals,age

    def state_at(self,event):
        assert self.discovery<=event<=self.frame.height
        rows=[];waiting=[]
        for pid,p in enumerate(self.periods):
            band,role,origin,start,parent=map(int,p)
            if start>event:continue
            broken=0<self.ends[pid]<=event
            end=int(self.ends[pid]) if broken else event
            scores,base,vals,age=self.samples(pid,[end])
            rows.append(dict(band_id=band,period_id=pid,role='support' if role==0 else 'resistance',
                lower_units=int(self.values[self.lower[band]]),upper_units=int(self.values[self.upper[band]]),
                origin_event=origin,start_event=start,broken_event=int(self.ends[pid]) if broken else None,
                draw_end_event=end,status='broken' if broken else 'active',base_score=float(base[0]),
                trade_count=float(vals[0,0]),volume_sum=float(vals[0,1]),delay_sum=float(vals[0,2]),
                intact_seconds=float(age[0]),strength=0. if broken else float(scores[0]),display_score=float(scores[0])))
            child=self.child.get(pid)
            if broken and (child is None or self.periods[child,3]>event):
                waiting.append(dict(band_id=band,parent_period=pid,waiting_for='resistance' if role==0 else 'support',
                                    since_event=end,lower_units=int(self.values[self.lower[band]]),upper_units=int(self.values[self.upper[band]])))
        return pl.DataFrame(rows,schema={
            'band_id':pl.Int64,'period_id':pl.Int64,'role':pl.String,'lower_units':pl.Int64,'upper_units':pl.Int64,
            'origin_event':pl.Int64,'start_event':pl.Int64,'broken_event':pl.Int64,'draw_end_event':pl.Int64,
            'status':pl.String,'base_score':pl.Float64,'trade_count':pl.Float64,'volume_sum':pl.Float64,
            'delay_sum':pl.Float64,'intact_seconds':pl.Float64,'strength':pl.Float64,'display_score':pl.Float64}),pl.DataFrame(waiting,schema={'band_id':pl.Int64,'parent_period':pl.Int64,'waiting_for':pl.String,'since_event':pl.Int64,'lower_units':pl.Int64,'upper_units':pl.Int64})

    @property
    def array_bytes(self):
        arrays=[self.periods,self.ends,self.initial,self.lower,self.upper,self.updates,self.norm,self.counts,self.values,self.rank,self.times,self.volumes]
        return sum(a.nbytes for a in arrays)+sum(a.nbytes for a in self.index.values())


def load_reference(path,session_open):
    """Load only the named baseline functions, never notebook I/O or chart cells."""
    notebook=json.loads(Path(path).read_text(encoding='utf-8'))
    scope=dict(np=np,pl=pl,TAU_SECONDS=1.,DISCOVERY_EVENT=200,SESSION_OPEN_US=session_open)
    for cell in notebook['cells']:
        if cell['cell_type']!='code':continue
        for node in ast.parse(''.join(cell['source'])).body:
            if isinstance(node,ast.FunctionDef) and node.name in ('find_basins','peak_cores','replay_evolving'):
                exec(compile(ast.Module(body=[node],type_ignores=[]),str(path),'exec'),scope)
    return scope['replay_evolving']


def compare_reference(fast, reference):
    """Exact identities/transitions; tight float tolerance for range-sum arithmetic."""
    assert len(fast.periods)==len(reference['periods'])
    stable_ids={};samples_checked=0;max_error=0.
    for pid,p in enumerate(reference['periods']):
        band,role,origin,start,parent=map(int,fast.periods[pid])
        actual=(role,origin,start,parent,int(fast.values[fast.lower[band]]),int(fast.values[fast.upper[band]]),int(fast.ends[pid]))
        expected=(0 if p['role']=='support' else 1,p['origin_event'],p['start_event'],
                  -1 if p['parent_period'] is None else p['parent_period'],p['lower_units'],p['upper_units'],p['end_event'] or 0)
        assert actual==expected,(pid,actual,expected)
        stable_ids.setdefault(p['band_id'],band)
        assert stable_ids[p['band_id']]==band
        history=p['score_history'];events=[h['event'] for h in history]
        scores,base,values,age=fast.samples(pid,events)
        expected_scores=np.array([h['strength'] for h in history])
        max_error=max(max_error,float(np.max(np.abs(scores-expected_scores))))
        assert np.allclose(scores,expected_scores,rtol=1e-10,atol=1e-12),pid
        assert np.allclose(base,[h['base_score'] for h in history],rtol=1e-10,atol=1e-12),pid
        assert np.allclose(values,np.array([[h['trade_count'],h['volume_sum'],h['delay_sum']] for h in history]),rtol=1e-10,atol=1e-10),pid
        samples_checked+=len(history)
    assert fast.deferred.tolist()==[reference['deferred']['overlap'],reference['deferred']['wrong_side']]
    return dict(periods=len(fast.periods),score_samples=samples_checked,max_score_error=max_error)


def audit_replay(fast):
    """Independent direct scans of immutable boundaries and actual trade sequences."""
    raw=fast.frame['price_units'].to_numpy()
    order=np.argsort(fast.lower)
    assert np.all(fast.upper[order][:-1]<fast.lower[order][1:])
    flips=0
    for pid,p in enumerate(fast.periods):
        band,role,origin,start,parent=map(int,p)
        lower=int(fast.values[fast.lower[band]]);upper=int(fast.values[fast.upper[band]])
        end=int(fast.ends[pid]);stop=end if end else len(raw)
        future=raw[start:stop]
        hits=np.flatnonzero(future<lower if role==0 else future>upper)
        assert (int(hits[0])+start+1 if len(hits) else 0)==end,pid
        if parent>=0:
            flips+=1
            old=fast.periods[parent];assert old[0]==band and old[1]!=role and 0<fast.ends[parent]<start
            assert lower<=raw[start-1]<=upper
            assert raw[start-2]<lower if role==1 else raw[start-2]>upper
            score,base,_,age=fast.samples(pid,[start]);assert age[0]==0 and score[0]==base[0]
    return dict(periods=len(fast.periods),zones=len(fast.lower),retests=flips,checks='passed')


def save_compact(fast,path):
    """Write an analysis result cache, not a streaming-resume checkpoint."""
    np.savez_compressed(path,periods=fast.periods,ends=fast.ends,initial=fast.initial,
        lower=fast.lower,upper=fast.upper,updates=fast.updates,norm=fast.norm,counts=fast.counts,
        deferred=fast.deferred,profile_builds=fast.profile_builds,session_open=fast.session_open,
        discovery=fast.discovery,tau=fast.tau)
