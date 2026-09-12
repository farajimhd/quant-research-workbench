"""Retrospective single-session zones. Never use this session's output intraday."""
from dataclasses import asdict, dataclass
from hashlib import sha256
import json

import numpy as np
from scipy.signal import find_peaks

VERSION = 'historical-session-reaction-zones-1'


@dataclass(frozen=True)
class Settings:
    tick: float = .01
    noise_multiple: float = 6.
    range_fraction: float = .05
    reaction_seconds: int = 180
    maximum_gap_seconds: int = 60
    minimum_rejections: int = 2
    minimum_role_rejection_fraction: float = .6
    maximum_candidates: int = 512


def extract(bars, profile, *, ticker, session, available_at, source, settings=Settings()):
    """Bars have epoch-second end times and canonical OHLC/eligible volume.

    Whole-session extrema and volume nodes propose zones. A bounded forward
    encounter study grades both roles. All retained zones are returned; no
    chart-timeframe parameter, prior seed, or hidden top-N truncation exists.
    """
    s=settings
    if (not all(np.isfinite(x) for x in asdict(s).values()) or s.tick<=0 or s.noise_multiple<=0 or s.range_fraction<=0
            or s.reaction_seconds<=0 or s.maximum_gap_seconds<=0 or s.minimum_rejections<1
            or not 0<=s.minimum_role_rejection_fraction<=1):
        raise ValueError('Invalid extraction settings')
    if not bars:
        raise ValueError('No canonical price bars')
    a=np.array([[b[k] for k in ('t','open','high','low','close','volume')] for b in bars],dtype=float)
    t,o,h,l,c,v=a.T
    if (not np.isfinite(a).all() or np.any(np.diff(t)<=0) or np.any(l<=0) or np.any(v<0)
            or np.any(h<np.maximum(o,c)) or np.any(l>np.minimum(o,c))):
        raise ValueError('Invalid or unordered OHLCV')
    if available_at<t[-1]:
        raise ValueError('Historical levels cannot be available before session end')
    p=np.array([[r['price'],r['volume']] for r in profile],dtype=float)
    if p.size and (not np.isfinite(p).all() or np.any(p[:,0]<=0) or np.any(p[:,1]<0)):
        raise ValueError('Invalid volume profile')
    ranges=h-l
    noise=float(np.median(ranges[ranges>0])) if np.any(ranges>0) else s.tick
    prominence=max(3*s.tick,s.noise_multiple*noise,(h.max()-l.min())*s.range_fraction)
    half=max(s.tick,np.ceil(prominence/4/s.tick)*s.tick)
    # Break the sequence at data gaps: no extrema or encounters bridge a gap.
    cuts=np.r_[0,np.flatnonzero(np.diff(t)>s.maximum_gap_seconds)+1,len(t)]
    proposals=[]
    for left,right in zip(cuts,cuts[1:]):
        for values,kind in ((h[left:right],'high'),(-l[left:right],'low')):
            peaks,props=find_peaks(values,prominence=prominence)
            proposals.extend((float(abs(values[i])),float(w),kind) for i,w in zip(peaks,props['prominences']))
        proposals.extend([(float(h[left:right].max()),prominence,'high'),(float(l[left:right].min()),prominence,'low')])
    if p.size:
        # Explicit empty bins prevent separated price islands becoming neighbors.
        first=int(np.floor(p[:,0].min()/half));last=int(np.ceil(p[:,0].max()/half))
        if last-first>100000:
            raise ValueError('Volume-profile geometry exceeds capacity')
        hist=np.zeros(last-first+1)
        np.add.at(hist,np.floor(p[:,0]/half).astype(int)-first,p[:,1])
        smooth=np.convolve(hist,np.ones(3)/3,mode='same') if len(hist)>=3 else hist
        peaks,_=find_peaks(smooth,prominence=max(float(smooth.max())*.08,1.))
        proposals.extend(((i+first+.5)*half,prominence,'volume') for i in peaks)
    # Bounded-span clustering, not transitive neighbor chaining.
    groups=[]
    for price,weight,kind in sorted(proposals):
        if not groups or price-groups[-1][0][0]>2*half:
            groups.append([])
        groups[-1].append((price,weight,kind))
    if len(groups)>s.maximum_candidates:
        raise ValueError(f'{len(groups)} candidates exceed explicit capacity; no truncation performed')
    levels=[];rejected=[]
    for group in groups:
        prices=np.array([x[0] for x in group]);weights=np.array([x[1] for x in group])
        center=float(prices[np.searchsorted(np.cumsum(weights),weights.sum()/2)])
        lower=round(np.floor((center-half)/s.tick+1e-9)*s.tick,8)
        upper=round(np.ceil((center+half)/s.tick-1e-9)*s.tick,8)
        center=round((lower+upper)/2,8)
        encounters=[];armed=True;last_side=None
        for i in range(1,len(t)):
            if t[i]-t[i-1]>s.maximum_gap_seconds:
                armed=True;last_side=None
                continue
            # Rearm only after a meaningful excursion, avoiding repeated touches.
            if c[i-1]<lower-prominence or c[i-1]>upper+prominence:
                armed=True
                last_side='resistance' if c[i-1]<lower else 'support'
            if not armed or h[i]<lower or l[i]>upper:
                continue
            role=last_side or ('resistance' if c[i-1]<lower else 'support' if c[i-1]>upper else None)
            if role is None:
                continue
            armed=False
            end=min(len(t),int(np.searchsorted(t,t[i]+s.reaction_seconds,side='right')))
            gap=np.flatnonzero(np.diff(t[i:end])>s.maximum_gap_seconds)
            if len(gap):end=i+int(gap[0])+1
            future=c[i+1:end]
            rejection=future<lower-prominence if role=='resistance' else future>upper+prominence
            beyond=future>upper+half if role=='resistance' else future<lower-half
            acceptance=np.zeros(len(future),dtype=bool)
            if len(future)>1:
                acceptance[1:]=beyond[1:] & beyond[:-1] & (np.diff(t[i+1:end])==1)
            hits=np.flatnonzero(rejection|acceptance)
            j=i+1+int(hits[0]) if len(hits) else end-1
            outcome=('rejection' if rejection[hits[0]] else 'acceptance') if len(hits) else 'unresolved'
            # Time-window baseline includes empty seconds; no future baseline.
            prior=int(np.searchsorted(t,t[i]-60))
            baseline=float(v[prior:i].sum()/max(1,t[i]-max(t[0],t[i]-60)))
            duration=max(1,t[j]-t[i]+1)
            encounters.append(dict(at=float(t[i]),resolved_at=float(t[j]),role=role,outcome=outcome,
                volume=float(v[i:j+1].sum()),relative_volume=float(v[i:j+1].sum()/duration/baseline) if baseline>0 else None))
        supports=sum(e['outcome']=='rejection' and e['role']=='support' for e in encounters)
        resistances=sum(e['outcome']=='rejection' and e['role']=='resistance' for e in encounters)
        accepted=sum(e['outcome']=='acceptance' for e in encounters)
        role_quality={}
        for role in ('support','resistance'):
            wins=sum(e['role']==role and e['outcome']=='rejection' for e in encounters)
            losses=sum(e['role']==role and e['outcome']=='acceptance' for e in encounters)
            role_quality[role]=wins/max(1,wins+losses)
        profile_volume=float(p[(p[:,0]>=lower)&(p[:,0]<=upper),1].sum()) if p.size else 0.
        identity=sha256(f'{VERSION}|{ticker}|{session}|{lower:.8f}|{upper:.8f}'.encode()).hexdigest()[:16]
        row=dict(id=identity,lower=lower,upper=upper,price=center,support_rejections=supports,
            resistance_rejections=resistances,accepted_crossings=accepted,encounters=encounters,
            profile_volume=profile_volume,role_rejection_fraction=role_quality,proposal_sources=sorted({x[2] for x in group}),
            closing_role='support' if c[-1]>upper else 'resistance' if c[-1]<lower else 'within_band',
            evidence_role='both' if supports and resistances else 'support' if supports else 'resistance' if resistances else 'unconfirmed',
            available_at=available_at)
        # Do not erase a proven reaction area merely because it was later crossed.
        if any(count>=s.minimum_rejections and role_quality[role]>=s.minimum_role_rejection_fraction
                for role,count in (('support',supports),('resistance',resistances))):
            levels.append(row)
        else:
            rejected.append(dict(row,reason='insufficient_repeated_role_rejection_evidence'))
    digest=sha256(json.dumps(dict(bars=bars,profile=profile),sort_keys=True,separators=(',',':')).encode()).hexdigest()
    return dict(version=VERSION,ticker=ticker,session=session,available_at=available_at,retrospective=True,
        prior_level_count=0,source=source,input_sha256=digest,settings=asdict(s),
        geometry=dict(noise=noise,prominence=prominence,half_width=half),
        counts=dict(bars=len(bars),proposals=len(proposals),candidates=len(groups),selected=len(levels),rejected=len(rejected)),
        levels=levels,rejected=rejected)
