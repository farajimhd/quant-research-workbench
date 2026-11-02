"""Demand-only price references derived in RAM from the existing certified shard."""
import numpy as np
from numba import njit
from .price_program import REFERENCES


@njit(nogil=True)
def confirmed_selected(source,slots,rows,columns,left,right,is_low):
    """Visit observations only; emit the prior confirmed price for ranked slots."""
    t,u=source.shape
    latest=np.full(u,np.nan);last_tick=np.full(u,-2,np.int64)
    consecutive=np.zeros(u,np.int64);out=np.full(slots.shape,np.nan)
    cursor=0;width=left+right+1
    for clock in range(t):
        # Confirmation becomes visible on the next clock, never retrospectively.
        for k in range(slots.shape[1]):
            col=slots[clock,k]
            if col>=0:out[clock,k]=latest[col]
        while cursor<len(rows) and rows[cursor]==clock:
            col=columns[cursor];cursor+=1
            consecutive[col]=consecutive[col]+1 if last_tick[col]==clock-1 else 1
            last_tick[col]=clock
            if consecutive[col]<width:continue
            pivot=source[clock-right,col];ok=True
            for j in range(width):
                value=source[clock-j,col]
                if (is_low and value<pivot) or (not is_low and value>pivot):
                    ok=False;break
            if ok:latest[col]=pivot
    return out


class References:
    def __init__(self,data,slots):
        self.data=data;self.slots=np.asarray(slots)
        if not hasattr(data,'v65_price_reference_cache'):data.v65_price_reference_cache={}
        self.cache=data.v65_price_reference_cache
        self.times=np.arange(len(slots))[:,None]

    def values(self,name):
        if name in self.cache:return self.cache[name]
        if name not in REFERENCES and name!='volatility':raise ValueError('Unsupported price reference')
        slots=self.slots;safe=slots.clip(0);data=self.data
        if name in ('ask','bid','close'):
            source=data.host_tensors['mark' if name=='close' else name].numpy()
            result=source[self.times,safe]
        elif name=='volatility':
            result=self.values('rolling.high.5')-self.values('rolling.low.5')
        else:
            parts=name.split('.');side=parts[1]
            source=self.extremes(side)
            if parts[0]=='swing':
                left,right=map(int,parts[2:])
                key='_observations_'+side
                if key not in self.cache:self.cache[key]=np.nonzero(np.isfinite(source))
                rows,columns=self.cache[key]
                result=confirmed_selected(source,slots,rows,columns,left,right,side=='low')
            else:
                seconds=int(parts[2]);end=np.broadcast_to(self.times,slots.shape)
                if parts[0] in ('candle','previous'):
                    clocks=np.asarray(data.arrays['clocks'])
                    ends=((clocks+1)//seconds)*seconds-1-clocks[0]
                    if parts[0]=='previous':ends-=seconds
                    end=np.broadcast_to(ends[:,None],slots.shape)
                offsets=np.arange(seconds)
                ix=end[...,None]-offsets
                v=source[ix.clip(0),safe[...,None]]
                valid=(ix>=0)&np.isfinite(v)
                filled=np.where(valid,v,np.inf if side=='low' else -np.inf)
                result=np.min(filled,axis=-1) if side=='low' else np.max(filled,axis=-1)
                result=np.where((ix>=0).all(-1)&valid.any(-1),result,np.nan)
        result=np.where(slots>=0,result,np.nan)
        self.cache[name]=result
        return result

    def selected(self,name,ticks):
        """Bounded calibration projection; no full rolling-window temporary."""
        if name=='volatility':return self.selected('rolling.high.5',ticks)-self.selected('rolling.low.5',ticks)
        if name.startswith(('candle.','previous.','rolling.')):
            kind,side,seconds=name.split('.');seconds=int(seconds)
            slots=self.slots[ticks];safe=slots.clip(0);end=np.asarray(ticks)
            if kind!='rolling':
                clocks=np.asarray(self.data.arrays['clocks'])
                end=((clocks[ticks]+1)//seconds)*seconds-1-clocks[0]
                if kind=='previous':end-=seconds
            ix=end[:,None,None]-np.arange(seconds)
            v=self.extremes(side)[ix.clip(0),safe[...,None]]
            valid=(ix>=0)&np.isfinite(v)
            filled=np.where(valid,v,np.inf if side=='low' else -np.inf)
            result=filled.min(-1) if side=='low' else filled.max(-1)
            return np.where((slots>=0)&(ix>=0).all(-1)&valid.any(-1),result,np.nan)
        # Confirmed swings must scan their causal past, never just probe windows.
        result=self.values(name)[ticks].copy()
        self.cache.pop(name,None)
        return result

    def extremes(self,side):
        if hasattr(self.data,'raw_extremes'):return self.data.raw_extremes[side]
        key='_raw_'+side
        if key not in self.cache:
            data=self.data;rows=np.asarray(data.bank['source_rows'])
            observed=data.host_tensors['observed'].numpy()
            # Exact existing shard extremes; never reconstruct prices from logs.
            self.cache[key]=np.where((rows>=0)&observed,data.market[side][rows.clip(0)],np.nan)
        return self.cache[key]


class PricePlan:
    """Vectorized expression evaluation across all candidates and ranked slots."""
    def __init__(self,members,data,slots):
        import torch
        from .price_program import Prices
        expressions=[e for m in members for e in vars(Prices.restore(m.policy.price_program)).values()]
        names=sorted({e.reference for e in expressions}|{e.other for e in expressions if e.operation!='reference'}|
                     ({'volatility'} if any(e.volatility_offset for e in expressions) else set())|{'ask'})
        references=References(data,slots)
        self.bank=torch.as_tensor(np.stack([references.values(n) for n in names],-1),device=data.device)
        lookup={n:i for i,n in enumerate(names)};shape=(len(members),5)
        def tensor(v,dtype=None):return torch.tensor(v,device=data.device,dtype=dtype).reshape(shape)
        self.a=tensor([lookup[e.reference] for e in expressions],torch.long)
        self.b=tensor([lookup.get(e.other,lookup[e.reference]) for e in expressions],torch.long)
        self.op=tensor([('reference','min','max','blend','available').index(e.operation) for e in expressions])
        self.weight=tensor([e.weight for e in expressions]);self.offset=tensor([e.offset for e in expressions])
        self.volatility=tensor([e.volatility_offset for e in expressions]);self.vol_index=lookup.get('volatility')
        self.ask_index=lookup['ask'];self.names=names

    def __call__(self,indices):
        import torch
        bank=self.bank.index_select(0,indices)
        if bank.is_cuda:
            from .price_cuda import evaluate
            out=evaluate(self,indices)
        else:
            a=bank[:,:,self.a].permute(0,2,1,3);b=bank[:,:,self.b].permute(0,2,1,3)
            op=self.op[None,:,None];w=self.weight[None,:,None]
            out=torch.where(op==0,a,torch.where(op==1,torch.minimum(a,b),torch.where(op==2,torch.maximum(a,b),
                torch.where(op==3,w*a+(1-w)*b,torch.where(torch.isfinite(a)&(a>0),a,b)))))
            out=out*(1+self.offset[None,:,None])
            if self.vol_index is not None:out=out+bank[:,:,self.vol_index][:,None,:,None]*self.volatility[None,:,None]
        buy,sell,stop,target,update=out.unbind(-1)
        from .price_program import entry_guard
        guard=entry_guard(buy,stop,target,bank[:,:,self.ask_index][:,None],xp=torch)
        return out,guard
