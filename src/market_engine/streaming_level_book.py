"""Causal V7 observations from completed canonical 1s bars and a prior book.

Historical geometry is immutable within the session. Current-day proposals are
confirmed directional reversals, not whole-session extrema or volume modes.
"""
from copy import deepcopy
from dataclasses import asdict
import heapq
from math import ceil,isfinite,floor
import numpy as np
from .historical_session_levels import Settings,VERSION as EXTRACTION_VERSION
from .historical_level_checkpoint import digest,Policy

VERSION='causal-level-book-v7-1'


class StreamingLevelBook:
    def __init__(self,prior,*,ticker,session,start,end,split_factor=1.,split_evidence=(),settings=Settings()):
        if prior.get('checkpoint_hash')!=digest({k:v for k,v in prior.items() if k!='checkpoint_hash'}):raise ValueError('Prior book integrity mismatch')
        if prior['ticker']!=ticker or prior['session']>=session or prior['available_at']>start:raise ValueError('Prior book must precede the streaming session')
        if prior.get('source_extraction_version')!=EXTRACTION_VERSION:raise ValueError('Streaming requires the rebuilt narrow-band book')
        if not isfinite(split_factor) or split_factor<=0 or (split_factor!=1 and not split_evidence):raise ValueError('Split adjustment requires valid evidence')
        if not isfinite(start) or not isfinite(end) or end<=start:raise ValueError('Invalid session bounds')
        self.ticker=ticker;self.session=session;self.start=start;self.end=end;self.as_of=start
        self.prior_hash=prior['checkpoint_hash'];self.split_factor=split_factor;self.split_evidence=list(split_evidence)
        self.settings=asdict(settings);self.rows=[];self.pending={};self.previous=None
        self.small=[];self.large=[];self.high=None;self.low=None;self.trend=0;self.hod=None;self.lod=None
        self.bars_processed=0;self.proposals=0;self.merged=0
        for old in prior['levels']:
            role=old['role_segments'][-1]['role'] if old['role_segments'] else 'transition'
            row=dict(id=old['id'],lower=float(old['lower']*split_factor),upper=float(old['upper']*split_factor),price=float(old['price']*split_factor),
                historical=True,origin_session=old['origin_session'],qualified=True,role=role,segments=[dict(start=start,role=role)],
                events=[],last_contact=-1.,armed=True,side=None,created_at=start,proposal_at=None)
            self.rows.append(row)
        self._index()

    def _index(self):
        old_armed=getattr(self,'armed',None);old_sides=getattr(self,'sides',None)
        self.lower=np.array([r['lower'] for r in self.rows]);self.upper=np.array([r['upper'] for r in self.rows])
        self.armed=np.array([r['armed'] for r in self.rows],dtype=bool)
        self.sides=np.array([1 if r['side']=='resistance' else -1 if r['side']=='support' else 0 for r in self.rows],dtype=np.int8)
        if old_armed is not None:self.armed[:len(old_armed)]=old_armed;self.sides[:len(old_sides)]=old_sides

    def _noise(self,value):
        if value>0:
            if not self.small or value<=-self.small[0]:heapq.heappush(self.small,-value)
            else:heapq.heappush(self.large,value)
            if len(self.small)>len(self.large)+1:heapq.heappush(self.large,-heapq.heappop(self.small))
            if len(self.large)>len(self.small):heapq.heappush(self.small,-heapq.heappop(self.large))
        if not self.small:return self.settings['tick']
        return -self.small[0] if len(self.small)>len(self.large) else (-self.small[0]+self.large[0])/2

    def _resolve(self,index,event,outcome,stamp,reason=None):
        row=self.rows[index]
        event=dict(event,outcome=outcome,resolved_at=stamp)
        if reason:event['reason']=reason
        row['events'].append(event)
        if outcome=='unresolved' or event['at']<row['last_contact']:return
        row['last_contact']=event['at']
        if outcome=='rejection':role=event['role']
        elif row['role']==event['role']:role='transition'
        else:return
        was_qualified=row['qualified']
        if not was_qualified:
            for side in ('support','resistance'):
                wins=sum(e['role']==side and e['outcome']=='rejection' for e in row['events'])
                losses=sum(e['role']==side and e['outcome']=='acceptance' for e in row['events'])
                if wins>=self.settings['minimum_rejections'] and wins/max(1,wins+losses)>=self.settings['minimum_role_rejection_fraction']:row['qualified']=True
        if row['qualified'] and (not was_qualified or role!=row['role']):row['segments'].append(dict(start=stamp,role=role))
        row['role']=role

    def _proposal(self,price,pivot,role,bar,half,prominence):
        if bar['t']-pivot>self.settings['reaction_seconds']:return
        self.proposals+=1;tick=self.settings['tick'];center=round(floor(price/tick+.5)*tick,8)
        lower=round(center-half,8);upper=round(center+half,8)
        if lower<=0:raise ValueError('Streaming band reaches nonpositive price')
        policy=Policy();matches=[]
        for i,r in enumerate(self.rows):
            width=max(upper-lower,r['upper']-r['lower']);union=max(upper,r['upper'])-min(lower,r['lower'])
            if r['lower']-1e-10<=price<=r['upper']+1e-10 and union<=width*policy.maximum_union_width_multiple and abs(center-r['price'])<=width*policy.maximum_center_distance_multiple:
                matches.append(i)
        if matches:
            self.merged+=1
            # Existing bands are evaluated directly by their own encounter state;
            # a matching proposal must not add the same rejection a second time.
            return
        if sum(not r['historical'] for r in self.rows)>=self.settings['maximum_candidates']:raise ValueError('Streaming candidate capacity reached; no truncation')
        identity=digest(dict(version=VERSION,ticker=self.ticker,session=self.session,price=center,half=half,at=bar['t']))[:16]
        row=dict(id=identity,lower=lower,upper=upper,price=center,historical=False,origin_session=self.session,qualified=False,
            role=role,segments=[],events=[],last_contact=-1.,armed=False,side=None,created_at=bar['t'],proposal_at=pivot)
        self.rows.append(row);self._index()
        self._resolve(len(self.rows)-1,dict(at=pivot,role=role,extreme=price,prominence=prominence,source='confirmed_directional_reversal'),'rejection',bar['t'])

    def update(self,bar,*,observed_at=None):
        """A bar's t is its close time; future, duplicate and invalid bars fail."""
        b={k:float(bar[k]) for k in ('t','open','high','low','close','volume')}
        t=b['t'];o=b['open'];h=b['high'];l=b['low'];c=b['close']
        if (not all(isfinite(x) for x in b.values()) or t!=int(t) or not self.as_of<t<=self.end or l<=0 or b['volume']<0
                or h<max(o,c) or l>min(o,c) or (observed_at is not None and (not isfinite(observed_at) or t>observed_at))):raise ValueError('Invalid, future or unordered completed 1s bar')
        gap=self.previous is not None and t-self.previous['t']>self.settings['maximum_gap_seconds']
        noise=self._noise(h-l);self.hod=max(self.hod or h,h);self.lod=min(self.lod or l,l)
        tick=self.settings['tick'];prominence=max(3*tick,self.settings['noise_multiple']*noise,(self.hod-self.lod)*self.settings['range_fraction'])
        half=max(tick,ceil(self.settings['band_noise_multiple']*noise/tick-1e-9)*tick)
        if gap:
            for i,events in self.pending.items():
                for event in events:self._resolve(i,event,'unresolved',self.previous['t'],'data_gap')
            self.pending.clear();self.high=None;self.low=None;self.trend=0
            self.armed[:]=True;self.sides[:]=0
        else:
            for i,events in list(self.pending.items()):
                row=self.rows[i];keep=[]
                for e in events:
                    if t-e['at']>self.settings['reaction_seconds']:
                        self._resolve(i,e,'unresolved',self.previous['t'],'reaction_timeout');continue
                    resistance=e['role']=='resistance';e['extreme']=max(e['extreme'],h) if resistance else min(e['extreme'],l)
                    rejection=c<row['lower']-e['prominence'] if resistance else c>row['upper']+e['prominence']
                    beyond=c>row['upper']+e['half'] if resistance else c<row['lower']-e['half']
                    acceptance=beyond and e['beyond'] and t-e['last_t']==1
                    if rejection:
                        inside=row['lower']-1e-10<=e['extreme']<=row['upper']+1e-10
                        self._resolve(i,e,'rejection' if inside else 'unresolved',t,None if inside else 'turning_extreme_outside_band')
                    elif acceptance:self._resolve(i,e,'acceptance',t)
                    else:e['beyond']=beyond;e['last_t']=t;keep.append(e)
                if keep:self.pending[i]=keep
                else:self.pending.pop(i,None)
        if self.previous and not gap:
            prev=self.previous['close']
            # Numeric mask is bounded by book size; Python work only visits
            # departure/touch candidates, not every event of every past session.
            below=prev<self.lower-prominence;above=prev>self.upper+prominence
            self.armed[below|above]=True;self.sides[below]=1;self.sides[above]=-1
            touched=np.flatnonzero((h>=self.lower)&(l<=self.upper))
            for i in touched:
                row=self.rows[i]
                if not self.armed[i]:continue
                side=('resistance' if self.sides[i]==1 else 'support' if self.sides[i]==-1 else None) or ('resistance' if prev<row['lower'] else 'support' if prev>row['upper'] else None)
                if side is None:continue
                self.armed[i]=False
                self.pending.setdefault(int(i),[]).append(dict(at=t,role=side,extreme=h if side=='resistance' else l,prominence=prominence,
                    half=(row['upper']-row['lower'])/2,beyond=False,last_t=t,source='band_contact'))
        if self.high is None:self.high=(h,t);self.low=(l,t)
        if h>self.high[0]:self.high=(h,t)
        if l<self.low[0]:self.low=(l,t)
        if self.trend>=0 and c<=self.high[0]-prominence:
            self._proposal(self.high[0],self.high[1],'resistance',b,half,prominence);self.trend=-1;self.low=(l,t);self.high=(h,t)
        elif self.trend<=0 and c>=self.low[0]+prominence:
            self._proposal(self.low[0],self.low[1],'support',b,half,prominence);self.trend=1;self.high=(h,t);self.low=(l,t)
        self.previous=b;self.as_of=t;self.bars_processed+=1

    def snapshot(self,as_of=None):
        stamp=self.as_of if as_of is None else as_of
        if not self.as_of<=stamp<=self.end:raise ValueError('Snapshot cannot rewind streaming state')
        segments=[]
        for row in self.rows:
            if not row['qualified']:continue
            for i,s in enumerate(row['segments']):
                end=row['segments'][i+1]['start'] if i+1<len(row['segments']) else stamp
                segments.append(dict(id=row['id'],price=row['price'],lower=row['lower'],upper=row['upper'],historical=row['historical'],
                    role=s['role'],valid_from=s['start'],valid_to=end,origin_session=row['origin_session'],model_input=False))
        return dict(version=VERSION,book_version=VERSION,ticker=self.ticker,session_date=self.session,as_of=stamp,max_input_timestamp=self.as_of,
            book_hash=self.prior_hash,split_factor=self.split_factor,historical_count=sum(r['historical'] for r in self.rows),
            current_day_count=sum(r['qualified'] and not r['historical'] for r in self.rows),candidate_count=sum(not r['qualified'] for r in self.rows),
            bars_processed=self.bars_processed,proposals=self.proposals,merged_proposals=self.merged,segments=segments)

    def checkpoint(self):
        state=deepcopy({k:v for k,v in self.__dict__.items() if k not in ('lower','upper','armed','sides')})
        for i,r in enumerate(state['rows']):
            r['armed']=bool(self.armed[i]);r['side']='resistance' if self.sides[i]==1 else 'support' if self.sides[i]==-1 else None
        state['pending']={str(k):v for k,v in state['pending'].items()}
        value=dict(version=VERSION,state=state);value['hash']=digest(value);return value

    @classmethod
    def restore(cls,value):
        if value.get('version')!=VERSION or value.get('hash')!=digest({k:v for k,v in value.items() if k!='hash'}):raise ValueError('Streaming checkpoint integrity/version mismatch')
        engine=cls.__new__(cls);engine.__dict__.update(deepcopy(value['state']));engine.pending={int(k):v for k,v in engine.pending.items()};engine._index();return engine
