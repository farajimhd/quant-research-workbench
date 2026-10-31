"""Causal, fully evolvable seed graphs; no special template replay path."""
from itertools import product
from math import tanh, atanh
from dataclasses import replace
from research.vectorized_backtest.v60.features import CATALOG
from .genome import Neuron, Network, Individual, INPUTS, MARKET_INPUTS, STAGES
from .config import STATES
from research.vectorized_backtest.v60.policy import Policy

ALLOCATIONS = dict(macd_signal=384, macd_zero=192, ema_cross=256,
    trend_pullback=320, vwap_reclaim=256, vwap_recovery=192, rsi_recovery=320,
    high_breakout=256, compression_breakout=192, support_bounce=256,
    relative_strength=192, activity_surge=128, candle_agreement=128)
FAMILIES = (*ALLOCATIONS, 'random')
from .authority import BY_NAME
INDEX = {name:handle.id for name,handle in BY_NAME.items()}


class Graph:
    """Strict-positive predicates and explicit validity, using existing operators."""
    def __init__(self): self.nodes=[]
    def node(self,sources,weights=None,bias=0.,op='identity'):
        self.nodes.append(Neuron(tuple(sources),tuple(weights or [1.]*len(sources)),float(bias),len(self.nodes)+1,op))
        return INPUTS+len(self.nodes)-1
    def value(self,name,delay=False):
        i=INDEX[name]
        return i
    def known(self,name,delay=False):
        i=2*len(CATALOG)+INDEX[name]

        return self.node([i],bias=-.5)
    def compare(self,name,level=0.,positive=True,delay=False):
        level=BY_NAME[name].clamp(level)
        f=CATALOG[INDEX[name]]
        scaled=tanh(level if f.unit in ('ratio','bool') else __import__('math').copysign(__import__('math').log1p(abs(level))/4,level))
        sign=1. if positive else -1.
        v=self.node([self.value(name,delay)],[sign],-sign*scaled)
        return self.all(v,self.known(name,delay))
    def all(self,*args):return self.node(args,op='min') if len(args)>1 else args[0]
    def any(self,*args):return self.node(args,op='max') if len(args)>1 else args[0]
    def cross(self,name,level=0.,up=True):
        # Strict previous-side comparison deliberately excludes equality; no
        # repeated level-trigger masquerading as a crossover.
        level=BY_NAME[name].clamp(level)
        f=CATALOG[INDEX[name]]
        scaled=tanh(level if f.unit in ('ratio','bool') else __import__('math').copysign(__import__('math').log1p(abs(level))/4,level))
        sign=1. if up else -1.
        return self.node([INDEX[name]],[sign],-sign*scaled,op='cross')
    def pair(self,a,b,up=True,cross=False):
        def diff(delay,sign):
            v=self.node([self.value(a,delay),self.value(b,delay)],[sign,-sign])
            return self.all(v,self.known(a,delay),self.known(b,delay))
        if cross:
            sign=1. if up else -1.
            return self.node([INDEX[a],INDEX[b]],[sign,-sign],op='cross')
        return diff(False,1 if up else -1)
    def finish(self,root,fraction=1.):
        # Constant size head uses zero edge weight; the decision remains market
        # dependent. All nodes remain ordinary mutable graph genes.
        root=self.node([root]);bias=8. if fraction==1 else atanh(2*fraction-1)
        return Network(tuple(self.nodes),0.,len(self.nodes)-1,0.,bias).validate()


def core_parameters(family):
    if family=='macd_signal':return list(product((3,7,12),(15,26,50),(5,9,20)))
    if family=='macd_zero':return list(product((3,7,12),(15,26,50),(False,True)))
    if family=='ema_cross':return [(3,15),(7,26),(12,50),(20,60)]
    if family=='trend_pullback':return list(product(((7,26),(12,50)),(False,True)))
    if family=='vwap_reclaim':return [(False,),(True,)]
    if family=='vwap_recovery':return list(product((7,14,26),(.25,.35,.45)))
    if family=='rsi_recovery':return list(product((3,7,14,26),(.2,.3,.4),(.6,.7,.8),(False,True)))
    if family=='high_breakout':return list(product((5,15,30,60),(0.,.001),(False,True),(False,True)))
    if family=='compression_breakout':return list(product((15,60),(30,60)))
    if family=='support_bounce':return [(5,),(15,)]
    if family=='relative_strength':return list(product((15,60,300),(.7,.8,.9)))
    return [()]


def signals(family,params):
    e=Graph();x=Graph()
    positive=lambda g,h=5:g.compare(f'enriched.return.{h}s')
    negative=lambda g,h=5:g.compare(f'enriched.return.{h}s',positive=False)
    hist=lambda f,s,q:f'indicator.macd_histogram.{f}.{s}.{q}.relative'
    ema=lambda p:f'indicator.ema.{p}.relative'
    if family=='macd_signal':
        name=hist(*params);entry=e.cross(name);exit=x.cross(name,up=False)
    elif family=='macd_zero':
        f,s,confirm=params;name=f'indicator.macd_line.{f}.{s}.relative';entry=e.cross(name)
        if confirm:entry=e.all(entry,e.compare(hist(f,s,9)))
        exit=x.cross(name,up=False)
    elif family=='ema_cross':
        a,b=map(ema,params);entry=e.pair(a,b,cross=True);exit=x.pair(a,b,up=False,cross=True)
    elif family=='trend_pullback':
        (f,s),recovery=params
        entry=e.all(e.pair(ema(f),ema(s)),e.compare(ema(f),positive=True),e.cross(hist(f,s,9)) if recovery else positive(e))
        exit=x.pair(ema(f),ema(s),up=False)
    elif family=='vwap_reclaim':
        entry=e.all(e.cross('session_vwap_rel',up=False),positive(e))
        if params[0]:entry=e.all(entry,e.compare('enriched.activity.volume_acceleration'))
        exit=x.compare('session_vwap_rel')
    elif family=='vwap_recovery':
        p,t=params;entry=e.all(e.compare('session_vwap_rel'),e.cross(f'indicator.rsi.{p}',t),positive(e))
        exit=x.any(x.compare('session_vwap_rel',positive=False),negative(x))
    elif family=='rsi_recovery':
        p,lo,hi,trend=params;entry=e.cross(f'indicator.rsi.{p}',lo)
        if trend:entry=e.all(entry,e.pair(ema(7),ema(26)))
        exit=x.any(x.compare(f'indicator.rsi.{p}',hi),negative(x))
    elif family=='high_breakout':
        h,buffer,activity,macd_exit=params
        entry=e.cross(f'history.recent_high.{h}.relative_to_close',1/(1+buffer)-1,up=False)
        if activity:entry=e.all(entry,e.compare('enriched.activity.volume_acceleration'))
        exit=x.compare(hist(7,26,9),positive=False) if macd_exit else negative(x)
    elif family=='compression_breakout':
        h,lookback=params
        entry=e.all(e.compare(f'enriched.candle.{h}s.range',.02,False),e.cross(f'history.recent_high.{lookback}.relative_to_close',up=False),e.compare('enriched.activity.volume_acceleration'))
        exit=negative(x,15)
    elif family=='support_bounce':
        pivot=f'history.swing_low.{params[0]}.{params[0]}.relative_to_close'
        entry=e.all(e.compare(pivot,-.02),e.compare(pivot,0.,False),positive(e,params[0]))
        exit=negative(x)
    elif family=='relative_strength':
        h,t=params;entry=e.all(e.compare(f'enriched.context.{h}s.relative_return'),e.compare(f'enriched.context.{h}s.return_percentile',t))
        exit=x.compare(f'enriched.context.{h}s.relative_return',positive=False)
    elif family=='activity_surge':
        entry=e.all(e.compare('enriched.activity.volume_acceleration',.5),e.compare('enriched.rank.dollar_volume_60s_percentile',.7),positive(e))
        exit=x.all(x.compare('enriched.activity.volume_acceleration',positive=False),negative(x))
    elif family=='candle_agreement':
        entry=e.all(e.compare('enriched.candle.15s.return'),e.compare('enriched.candle.60s.return'),e.compare('enriched.candle.5s.close_location',.75))
        exit=x.any(x.compare('enriched.candle.5s.return',positive=False),x.compare('enriched.candle.15s.return',positive=False))
    else:raise ValueError(family)
    return e,entry,x,exit


def build(family,index):
    cores=core_parameters(family);params=cores[index%len(cores)];management=index//len(cores)
    e,entry,x,exit=signals(family,params)
    reversion=family in ('rsi_recovery','vwap_recovery','support_bounce')
    pairs=((.005,.005),(.01,.01),(.02,.02)) if reversion else ((.005,.01),(.01,.02),(.02,.04),(.04,.08))
    stop,target=pairs[management%len(pairs)]
    holding=(60,180,300) if reversion else (30,60,120,300,600)
    # Deterministic mixed-radix management grid, not a Cartesian replay sweep.
    hold=holding[management%len(holding)]+management//len(holding)
    # Spread sizes across every family's allocation, rather than exhausting
    # stop/hold combinations before ever reaching the next size fraction.
    fraction=(.25,.5,1.)[management%3]
    reduce=(.25,.5,.75,1.)[(index//3+management)%4]
    # Sparse families need additional distinct seeds. These are explicit bounded
    # holding-time variations, not random unused genes or numerical hash salts.
    age=x.node([MARKET_INPUTS+STATES.index('age_hours')],bias=-tanh(hold/3600))
    exit=x.any(exit,age)
    networks={'entry':e.finish(entry,fraction),'exit':x.finish(exit)}
    # Initially disabled management graphs still read a real market feature and
    # can mutate/cross like any graph. Bracket management remains native.
    for stage in ('add','reduce','trail'):
        g=Graph();n=g.node([INDEX['enriched.return.5s']],[1.],-2.)
        networks[stage]=replace(g.finish(n),threshold=.99)
    swing=(0,0)
    if family in ('trend_pullback','high_breakout','support_bounce') and management%8>=4:
        swing=((3,3),(5,5),(10,10),(20,10))[management%8-4]
    maximum_adds=0
    if management%8>=6:
        maximum_adds=1+management%2;g=Graph()
        profit=g.node([MARKET_INPUTS+STATES.index('profit_fraction')],bias=-tanh(.01))
        n=g.all(profit,g.compare('enriched.return.5s'));networks['add']=g.finish(n,fraction)
    if management%4==3:
        g=Graph();profit=g.node([MARKET_INPUTS+STATES.index('profit_fraction')],bias=-tanh(.02))
        n=g.all(profit,g.compare('enriched.return.5s'));networks['trail']=g.finish(n)
    policy=Policy(stop_fraction=stop,target_fraction=target,reduce_fraction=reduce,maximum_adds=maximum_adds,
        swing_left=swing[0],swing_right=swing[1],trail_fraction=(.005,.01,.02,.04)[management%4])
    return Individual(networks,policy,{}).validate()


def sample_templates():
    members=[];families=[]
    for family,count in ALLOCATIONS.items():
        members.extend(build(family,i) for i in range(count));families.extend([family]*count)
    return members,families
