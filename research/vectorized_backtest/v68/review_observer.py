"""Batched endpoint telemetry; reduce on device instead of copying every neuron.

Packets are returned by the caller unchanged. All counts retain the original
clock/slot universe; price-rejection causes intentionally overlap.
"""
import torch
from .genome import STAGES
from .lineage import program_key

CAUSES=('missing_buy','missing_stop','missing_target','invalid_buy','invalid_stop',
        'invalid_target','invalid_quote','excessive_spread')
FUNNEL=('eligible','entry_signals','price_rejected','guard_pass')
TRACES=('entry_signal','guard_pass','price_rejected','exit','add','reduce','trail')


class Observer:
    def __init__(self):self.ready=False

    def capture(self,engine,indices,packet):
        signals,ratios,prices,owners,abstain=packet
        device=signals.device;slots=engine.slots.index_select(0,indices);eligible=slots>=0
        safe=slots.clamp_min(0)
        bid=engine.data.tensors['bid'].index_select(0,indices).gather(1,safe)
        ask=engine.data.tensors['ask'].index_select(0,indices).gather(1,safe)
        if not self.ready:
            self.keys=[program_key(m) for m in engine.base.members];self.tickers=list(engine.data.tickers)
            self.n=len(self.keys);self.width=len(self.tickers)
            self.offsets=torch.as_tensor(engine.base.offsets_cpu,device=device,dtype=torch.long).reshape(self.n,5)
            self.counts=engine.base.counts.to(device=device,dtype=torch.long).reshape(self.n,5)
            self.ends=self.offsets+self.counts
            self.funnel=torch.zeros((self.n,4),device=device,dtype=torch.int64)
            self.causes=torch.zeros((self.n,8),device=device,dtype=torch.int64)
            self.actions=torch.zeros((self.n,5),device=device,dtype=torch.int64)
            self.node_stats=torch.zeros((self.n,5,3),device=device,dtype=torch.int64)
            self.by_ticker=torch.zeros((4,self.n*self.width),device=device,dtype=torch.int64)
            self.samples=torch.zeros((self.n,7,23),device=device,dtype=torch.float64)
            self.seen=torch.zeros((self.n,7),device=device,dtype=torch.bool)
            self.ready=True
        entry=(((signals&1)!=0)|abstain)&eligible[:,None]
        masks=(eligible[:,None].expand_as(entry),entry,abstain&eligible[:,None],((signals&1)!=0)&eligible[:,None])
        self.funnel+=torch.stack([m.sum((0,2)) for m in masks],-1)
        buy,_,stop,target,_=prices.unbind(-1);b=bid[:,None];a=ask[:,None]
        causes=(~torch.isfinite(buy),~torch.isfinite(stop),~torch.isfinite(target),
            torch.isfinite(buy)&((buy<=0)|(buy>1000)),
            torch.isfinite(stop)&((stop<=0)|(stop>=buy)|(stop>b-.03)),
            torch.isfinite(target)&(target<=buy),
            (~torch.isfinite(b)|~torch.isfinite(a)|(b<=0)|(a<b)).expand_as(entry),
            ((a-b)>b*.05).expand_as(entry))
        self.causes+=torch.stack([(m&entry).sum((0,2)) for m in causes],-1)
        actions=[entry]+[((signals&(1<<stage))!=0)&eligible[:,None] for stage in range(1,5)]
        self.actions+=torch.stack([m.sum((0,2)) for m in actions],-1)
        work=engine.work[:len(indices)];known=engine.known[:len(indices)]&eligible[:,None]
        for column,values in enumerate((known,known&~torch.isfinite(work))):
            per_node=values.sum((0,2));prefix=torch.cat((per_node.new_zeros(1),per_node.cumsum(0)))
            self.node_stats[:,:,column]+=prefix[self.ends]-prefix[self.offsets]
        self.node_stats[:,:,2]+=eligible.sum()*self.counts
        listing=(safe[:,None]+torch.arange(self.n,device=device)[None,:,None]*self.width).reshape(-1)
        for column,mask in enumerate(masks):self.by_ticker[column].scatter_add_(0,listing,mask.reshape(-1).long())
        leaf=torch.arange(self.n,device=device)
        for column,mask in enumerate((entry,masks[3],masks[2],*actions[1:])):
            flat=mask.permute(1,0,2).reshape(self.n,-1);first=flat.int().argmax(-1)
            found=flat.any(-1)&~self.seen[:,column];t=first//slots.shape[1];slot=first%slots.shape[1]
            roots=(self.ends-1).clamp_min(0)
            root_values=work[t[:,None],roots,slot[:,None]]
            root_known=engine.known[t[:,None],roots,slot[:,None]]&(self.counts>0)
            sample=torch.cat((indices[t,None],slots[t,slot,None],bid[t,slot,None],ask[t,slot,None],
                signals[t,leaf,slot,None],ratios[t,leaf,slot],prices[t,leaf,slot],root_values,root_known),-1).double()
            # JSON evidence represents unavailable/nonfinite sample values explicitly.
            self.samples[:,column]=torch.where(found[:,None],sample,self.samples[:,column]);self.seen[:,column]|=found

    def finish(self):
        if not self.ready:return {}
        f,c,a,n=[v.cpu().tolist() for v in (self.funnel,self.causes,self.actions,self.node_stats)]
        tickers=self.by_ticker.reshape(4,self.n,self.width).cpu().numpy()
        samples=self.samples.cpu().numpy();seen=self.seen.cpu().numpy();rows={}
        import math
        for i,key in enumerate(self.keys):
            if key in rows:continue # Same leaf replayed in multiple candidates is one observation universe.
            traces={name:[float(v) if math.isfinite(float(v)) else None for v in samples[i,j]]
                    for j,name in enumerate(TRACES) if seen[i,j]}
            rows[key]=dict(zip(FUNNEL,f[i]),causes=dict(zip(CAUSES,c[i])),actions=dict(zip(STAGES,a[i])),
                nodes={stage:dict(zip(('known','nonfinite_known','observations'),n[i][j])) for j,stage in enumerate(STAGES)},
                tickers={ticker:dict(zip(FUNNEL,map(int,tickers[:,i,j]))) for j,ticker in enumerate(self.tickers) if tickers[0,i,j]},
                bounded_traces=traces,trace_columns=['clock','listing_column','bid','ask','signals','entry_ratio','add_ratio','reduce_ratio',
                    'buy','sell','stop','target','update',*[s+'_root' for s in STAGES],*[s+'_known' for s in STAGES]])
        return rows
