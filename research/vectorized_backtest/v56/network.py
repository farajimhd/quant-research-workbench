"""Sparse packed networks; one fused CUDA kernel for all five action graphs."""
import os
import numpy as np
import torch
from .genome import INPUTS,MARKET_INPUTS,STAGES,OPS
from research.vectorized_backtest.v42.features import CATALOG
from .config import MAX_NODES,FAN_IN,STATES

def state_values(position,previous,opened,clock,feedback):
    q,avg,stop,_,_,adds,last,_,_=position.unbind(-1);held=q>1e-12
    known_price=torch.isfinite(previous)&(previous>0)
    values=(held.to(torch.float64),torch.where(held&known_price,previous/avg.clamp_min(1e-12)-1,0.),
        torch.where(held,(clock-opened)/3600,0.),torch.where(held&known_price,q*previous/1000,0.),
        torch.where(held,(avg-stop)/avg.clamp_min(1e-12),0.),adds/20,
        torch.where(held,(clock-last)/300,0.))
    return torch.cat((torch.stack(values,-1).to(torch.float32),feedback[None]),-1).tanh()

def outcome_values(before,after,price,signals,ratios,feedback):
    old=before[0,...,0];new=after[0,...,0];held=old>1e-12
    bought=(new-old).clamp_min(0)*torch.nan_to_num(price[0,0])
    requested=torch.stack(tuple(((signals[0]&(1<<i))!=0).to(torch.float32) for i in (0,2,3)),-1)*ratios[0].to(torch.float32)
    sold=(old-new).clamp_min(0)/old.clamp_min(1e-12)
    reduced=torch.where(held&(new>1e-12),sold,0.).to(torch.float32)
    exited=torch.where(held&(new<=1e-12),sold,0.).to(torch.float32)
    fields=(requested[...,0],requested[...,1],requested[...,2],
        torch.where(~held,bought/1000,0.).to(torch.float32),torch.where(held,bought/200,0.).to(torch.float32),
        reduced,exited,((requested[...,0]>0)&~held&(bought<=0)).to(torch.float32),
        ((requested[...,1]>0)&held&(bought<=0)).to(torch.float32),signals[0].to(torch.float32)/31,feedback[...,-1])
    return torch.stack(fields,-1)

class NetworkPlan:
    builds=hits=evictions=capture_seconds=0
    def __init__(self,members,device,*,packed=None):
        self.members=members;self.device=torch.device(device);self.owner_indices=None
        self.cuda_block=int(os.environ.get('V42_NEURAL_BLOCK','128'))
        if self.cuda_block not in (32,64,128,256):raise ValueError('Unsupported neural CUDA tile')
        # Fixed catalog layout makes normalization independent of cohort membership.
        self.columns=list(range(len(CATALOG)))
        self.lookup={s:i for i,s in enumerate(self.columns)};c=len(self.columns)
        from .network_pack import pack_networks
        packed=pack_networks(members) if packed is None else packed
        self.total_nodes=packed['total_nodes'];self.offsets_cpu=packed['offsets_cpu'];self.kernel_nodes=packed['kernel_nodes']
        self.sources,self.weights,self.bias,self.counts,self.offsets,self.threshold,self.size_source,self.size_weight,self.size_bias=[torch.as_tensor(x,device=device) for x in packed['values']]
        self.operations=torch.as_tensor(packed['operations'],device=device)
        self.edge_counts=torch.as_tensor(packed['edge_counts'],device=device)
        # Execute long action graphs first; genotype and output order stay intact.
        self.cuda_network_order=torch.as_tensor(np.argsort(-packed['values'][3],kind='stable').astype(np.int32),device=device)
        self.logarithmic=torch.tensor([CATALOG[s].unit not in ('ratio','bool') for s in self.columns],device=device)
        self.workspace_shape=None

    def prepare(self,b,u):
        if self.workspace_shape==(b,u):return
        if self.total_nodes*u*10+b*u*len(STATES)*4>4*2**30:raise MemoryError('Neural workspace exceeds explicit 4 GiB admission')
        shape=(self.total_nodes,u)
        self.workspace=torch.zeros(shape,device=self.device,dtype=torch.float32);self.known=torch.zeros(shape,device=self.device,dtype=torch.bool)
        self.memory=torch.zeros_like(self.workspace);self.memory_known=torch.zeros_like(self.known)
        self.feedback=torch.zeros((b,u,len(STATES)-7),device=self.device,dtype=torch.float32)
        self.position_states=torch.empty((b,len(STATES),u),device=self.device,dtype=torch.float32)
        self.previous_clock=torch.full((),-1,device=self.device,dtype=torch.int64)
        self.buffer_phase=torch.zeros((),device=self.device,dtype=torch.int64)
        self.ratios=torch.ones((1,b,u,3),device=self.device,dtype=torch.float64)
        self.workspace_shape=(b,u)

    def reset(self):
        if self.workspace_shape is not None:
            self.memory.zero_();self.memory_known.zero_();self.feedback.zero_();self.previous_clock.fill_(-1);self.ratios.fill_(1.);self.buffer_phase.zero_()

    def export_state(self):
        # Expose logical preceding-clock values, independent of physical buffer.
        odd=self.device.type=='cuda' and bool(self.buffer_phase.item()&1)
        fields=dict(memory=self.workspace if odd else self.memory,memory_known=self.known if odd else self.memory_known,feedback=self.feedback,previous_clock=self.previous_clock)
        return {k:v.detach().cpu().clone() for k,v in fields.items()}

    def restore_state(self,state):
        if set(state)!=set(('memory','memory_known','feedback','previous_clock')):raise ValueError('Incomplete replay memory state')
        for k,value in state.items():
            target=getattr(self,k)
            if target.shape!=value.shape or target.dtype!=value.dtype:raise ValueError('Replay memory state changed')
        self.buffer_phase.zero_()
        for k,value in state.items():
            target=getattr(self,k)
            target.copy_(value.to(self.device))

    def record_outcomes(self,before,after,price,signals):
        self.feedback.copy_(outcome_values(before,after,price,signals,self.ratios,self.feedback))
    def project(self,data,index):
        cache=getattr(self,'market_block',None)
        return cache.read(index) if cache is not None else self._project(data,index)

    def _project(self,data,index):
        bank=data.resident_features;a=bank.tensors
        rows=a['source'].index_select(0,index.reshape(1)).T;ids=a['ids'].index_select(0,index.reshape(1)).T.to(torch.int64);clock=a['clocks'].index_select(0,index.reshape(1))[None]
        v,m=bank._values(rows,ids,clock,self.columns)
        v=v[:,0];valid=m[:,0]&torch.isfinite(v)
        # All statistics use only this causal clock's available cross-section.
        # Absolute channels remain available so normalization cannot erase levels.
        clean=torch.where(valid,v,0.)
        absolute=torch.where(self.logarithmic,clean.sign()*clean.abs().log1p()/4,clean).tanh()
        count=valid.sum(0).clamp_min(1)
        # Shift by the first observed value before summation. This preserves the
        # same causal mean/variance while avoiding cancellation of large levels
        # and exact-constant columns on different CPU/CUDA reduction orders.
        first=valid.to(torch.int32).argmax(0,keepdim=True)
        anchor=clean.gather(0,first).squeeze(0)
        shifted=torch.where(valid,clean-anchor,0.)
        deviation=shifted-shifted.sum(0)/count
        variance=torch.where(valid,deviation.square(),0.).sum(0)/count
        scale=variance.sqrt().clamp_min(1e-6)
        relative=(deviation/(2*scale)).tanh()
        return torch.cat((torch.where(valid,absolute,0.),torch.where(valid,relative,0.),valid.to(torch.float32)),1),valid
    def signals(self,data,index,position,opened,previous,out=None):
        self.prepare(position.shape[1],position.shape[2]);v,valid=self.project(data,index)
        market_clock=data.resident_features.tensors['clocks'].index_select(0,index.reshape(1)).reshape(())
        self.feedback[...,-1].copy_(torch.where(self.previous_clock>=0,(market_clock-self.previous_clock).clamp_min(0)/60,0.))
        self.previous_clock.copy_(market_clock)
        if v.shape[0]<position.shape[2]:
            from torch.nn.functional import pad
            extra=position.shape[2]-v.shape[0];v=pad(v,(0,0,0,extra));valid=pad(valid,(0,0,0,extra),value=False)
        if self.device.type=='cuda':
            from .network_cuda import signals
            return signals(self,v,valid,position,opened,previous,index,out)
        states=state_values(position,previous,opened,index.to(torch.float64),self.feedback)[0];rows=[]
        for b,m in enumerate(self.members):
            actions=[]
            for action,stage in enumerate(STAGES):
                net=m.networks[stage];base=int(self.offsets_cpu[b*5+action]);outputs=[];masks=[]
                for i,n in enumerate(net.nodes):
                    z=torch.full((v.shape[0],),float(np.float32(n.bias)),dtype=torch.float32,device=v.device);ok=torch.zeros_like(z,dtype=torch.bool)
                    terms=[]
                    for source,w in zip(n.sources,n.weights):
                        if w==0 and n.op in ('tanh','conv','identity'):continue
                        if source<0:x=self.memory[base-source-1];mask=self.memory_known[base-source-1]
                        elif source<MARKET_INPUTS:
                            channel=source//len(CATALOG);column=self.lookup[source%len(CATALOG)]
                            x=v[:,channel*len(self.columns)+column];mask=valid[:,column] if channel<2 else torch.zeros_like(ok)
                        elif source<INPUTS:x=states[b,:,source-MARKET_INPUTS];mask=torch.zeros_like(ok)
                        else:x=outputs[source-INPUTS];mask=masks[source-INPUTS]
                        term=x*float(np.float32(w));terms.append(term);z=z+term;ok=ok|(mask if w!=0 else False)
                    from .operators import evaluate
                    if n.op in OPS[9:]:
                        xs=[outputs[source-INPUTS] for source in n.sources]
                        ms=[masks[source-INPUTS] for source in n.sources]
                        if n.op=='decision':
                            age=(index.to(torch.float64)-opened[0,b])
                            value=(ms[0]&(xs[0]>float(np.float32(n.weights[0])))&(age>=round(n.bias*3600))).to(torch.float32);ok=ms[0]
                        elif n.op=='linear':value=xs[0]*float(np.float32(n.weights[0]))+float(np.float32(n.bias));ok=ms[0]
                        elif n.op=='whole_xnor_size':value=torch.maximum(xs[2],xs[3]);ok=ms[0]&ms[1]
                        elif n.op=='whole_size':value=torch.where(xs[0]>.5,torch.where(xs[1]>.5,torch.maximum(xs[2],xs[3]),xs[2]),torch.where(xs[1]>.5,xs[3],0.));ok=ms[0]|ms[1]
                        else:
                            a=xs[0]>.5;b2=xs[1]>.5
                            value=((a|b2) if n.op=='whole_or' else (a&b2) if n.op=='whole_and' else (a==b2) if n.op=='whole_xnor' else (a^b2)).to(torch.float32);ok=(ms[0]&ms[1]) if n.op=='whole_xnor' else (ms[0]|ms[1])
                    else:value=z.tanh() if n.op in ('tanh','conv') else evaluate(n.op,terms,n.bias,z)
                    outputs.append(value);masks.append(ok)
                    self.workspace[base+i].copy_(outputs[-1]);self.known[base+i].copy_(ok)
                actions.append(masks[-1]&(outputs[-1]>float(np.float32(net.threshold))))
                if action in (0,2,3):
                    head=net.size_source if net.size_source>=0 else len(net.nodes)-1
                    fraction=((outputs[head]*float(np.float32(net.size_weight))+float(np.float32(net.size_bias))).tanh()+1)*.5
                    self.ratios[0,b,:,(0,2,3).index(action)].copy_(fraction.to(torch.float64))
            rows.append(torch.stack(actions))
        self.memory.copy_(self.workspace);self.memory_known.copy_(self.known)
        value=torch.stack(rows);result=sum(value[:,i].to(torch.int16)*(1<<i) for i in range(5))[None]
        if out is not None:out.copy_(result);return out
        return result

    def close(self):
        self.workspace=None;self.known=None;self.memory=None;self.memory_known=None;self.feedback=None;self.position_states=None;self.buffer_phase=None;self.workspace_shape=None
