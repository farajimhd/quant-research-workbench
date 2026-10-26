"""Shared RNN oracle and causal feature projection; one update before six heads."""
import os
import torch
from .genome import HIDDEN,FAN_IN,MARKET_INPUTS,INPUTS
from research.vectorized_backtest.v42.config import STATES
from research.vectorized_backtest.v42.features import CATALOG

def state_values(position,previous,opened,clock,feedback):
    q,avg,stop,_,_,adds,last,_,_=position.unbind(-1);held=q>1e-12
    known_price=torch.isfinite(previous)&(previous>0)
    values=(held.to(torch.float64),torch.where(held&known_price,previous/avg.clamp_min(1e-12)-1,0.),
        torch.where(held,(clock-opened)/3600,0.),torch.where(held&known_price,q*previous/1000,0.),
        torch.where(held,(avg-stop)/avg.clamp_min(1e-12),0.),adds/20,torch.where(held,(clock-last)/300,0.))
    return torch.cat((torch.stack(values,-1).to(torch.float32),feedback[None]),-1).tanh()

def outcome_values(before,after,price,signals,ratios,feedback):
    old=before[0,...,0];new=after[0,...,0];held=old>1e-12
    bought=(new-old).clamp_min(0)*torch.nan_to_num(price[0,0])
    requested=torch.stack(tuple(((signals[0]&(1<<i))!=0).to(torch.float32) for i in (0,2,3)),-1)*ratios[0,...,:3].to(torch.float32)
    sold=(old-new).clamp_min(0)/old.clamp_min(1e-12)
    fields=(requested[...,0],requested[...,1],requested[...,2],torch.where(~held,bought/1000,0.).to(torch.float32),
        torch.where(held,bought/200,0.).to(torch.float32),torch.where(held&(new>1e-12),sold,0.).to(torch.float32),
        torch.where(held&(new<=1e-12),sold,0.).to(torch.float32),((requested[...,0]>0)&~held&(bought<=0)).to(torch.float32),
        ((requested[...,1]>0)&held&(bought<=0)).to(torch.float32),signals[0].to(torch.float32)/63,feedback[...,-1])
    return torch.stack(fields,-1)

class NetworkPlan:
    builds=hits=evictions=capture_seconds=0
    def __init__(self,members,device,*,packed=None):
        from .network_pack import pack_networks
        self.members=members;self.device=torch.device(device);self.owner_indices=None
        self.columns=list(range(len(CATALOG)));self.lookup={s:s for s in self.columns}
        self.logarithmic=torch.tensor([f.unit not in ('ratio','bool') for f in CATALOG],device=device)
        p=pack_networks(members) if packed is None else packed
        self.sources,self.weights,self.bias,self.hs,self.hw,self.hb,self.ht,self.modes,self.sizes=[torch.as_tensor(x,device=device) for x in p['values']]
        self.total_nodes=p['total_nodes'];self.kernel_nodes=HIDDEN;self.offsets_cpu=p['offsets_cpu'];self.workspace_shape=None
        self.cuda_block=int(os.environ.get('V54_NEURAL_BLOCK','16'))
        if self.cuda_block not in (16,32,64,128):raise ValueError('Invalid V54 neural tile')
    def prepare(self,b,u):
        if self.workspace_shape==(b,u):return
        if b*u*(HIDDEN*10+len(STATES)*4+32)>4*2**30:raise MemoryError('Neural workspace exceeds 4 GiB admission')
        self.memory=torch.zeros((b,HIDDEN,u),device=self.device);self.memory_known=torch.zeros_like(self.memory,dtype=torch.bool)
        self.workspace=torch.zeros_like(self.memory);self.known=torch.zeros_like(self.memory_known)
        self.feedback=torch.zeros((b,u,len(STATES)-7),device=self.device)
        self.position_states=torch.empty((b,len(STATES),u),device=self.device)
        self.previous_clock=torch.full((),-1,device=self.device,dtype=torch.int64);self.buffer_phase=torch.zeros((),device=self.device,dtype=torch.int64)
        self.ratios=torch.ones((1,b,u,4),device=self.device,dtype=torch.float64);self.ratios[...,3]=.03
        self.workspace_shape=(b,u)
    def reset(self):
        if self.workspace_shape is not None:
            self.memory.zero_();self.memory_known.zero_();self.workspace.zero_();self.known.zero_();self.feedback.zero_();self.previous_clock.fill_(-1);self.buffer_phase.zero_();self.ratios.fill_(1.);self.ratios[...,3]=.03
    def export_state(self):
        odd=self.device.type=='cuda' and bool(self.buffer_phase.item()&1)
        fields=dict(memory=self.workspace if odd else self.memory,memory_known=self.known if odd else self.memory_known,feedback=self.feedback,previous_clock=self.previous_clock)
        return {k:v.detach().cpu().clone() for k,v in fields.items()}
    def restore_state(self,state):
        if set(state)!={'memory','memory_known','feedback','previous_clock'}:raise ValueError('Incomplete recurrent state')
        for k,v in state.items():
            target=getattr(self,k)
            if v.shape!=target.shape or v.dtype!=target.dtype:raise ValueError('Recurrent state shape/type changed')
        self.buffer_phase.zero_()
        for k,v in state.items():getattr(self,k).copy_(v.to(self.device))
    def record_outcomes(self,before,after,price,signals):self.feedback.copy_(outcome_values(before,after,price,signals,self.ratios,self.feedback))
    def project(self,data,index):
        cache=getattr(self,'market_block',None)
        return cache.read(index) if cache is not None else self._project(data,index)
    def _project(self,data,index):
        bank=data.resident_features;a=bank.tensors
        rows=a['source'].index_select(0,index.reshape(1)).T;ids=a['ids'].index_select(0,index.reshape(1)).T.to(torch.int64);clock=a['clocks'].index_select(0,index.reshape(1))[None]
        v,m=bank._values(rows,ids,clock,self.columns);v=v[:,0];valid=m[:,0]&torch.isfinite(v)
        clean=torch.where(valid,v,0.);absolute=torch.where(self.logarithmic,clean.sign()*clean.abs().log1p()/4,clean).tanh()
        count=valid.sum(0).clamp_min(1);first=valid.to(torch.int32).argmax(0,keepdim=True);anchor=clean.gather(0,first).squeeze(0)
        shifted=torch.where(valid,clean-anchor,0.);deviation=shifted-shifted.sum(0)/count
        scale=(torch.where(valid,deviation.square(),0.).sum(0)/count).sqrt().clamp_min(1e-6);relative=(deviation/(2*scale)).tanh()
        return torch.cat((torch.where(valid,absolute,0.),torch.where(valid,relative,0.),valid.to(torch.float32)),1),valid
    def signals(self,data,index,position,opened,previous,out=None):
        self.prepare(position.shape[1],position.shape[2]);v,valid=self.project(data,index)
        clock=data.resident_features.tensors['clocks'].index_select(0,index.reshape(1)).reshape(())
        self.feedback[...,-1].copy_(torch.where(self.previous_clock>=0,(clock-self.previous_clock).clamp_min(0)/60,0.));self.previous_clock.copy_(clock)
        if v.shape[0]<position.shape[2]:
            from torch.nn.functional import pad
            extra=position.shape[2]-v.shape[0];v=pad(v,(0,0,0,extra));valid=pad(valid,(0,0,0,extra),value=False)
        if self.device.type=='cuda' and os.environ.get('V54_NEURAL_REFERENCE','0')!='1':
            from .network_cuda import signals
            return signals(self,v,valid,position,opened,previous,index,out)
        states=state_values(position,previous,opened,index.to(torch.float64),self.feedback)[0]
        result=self.reference(v,valid,states)
        if out is not None:out.copy_(result);return out
        return result
    def reference(self,v,valid,states):
        b,u,_=states.shape
        bank=torch.cat((v[None].expand(b,-1,-1),states,self.memory.transpose(1,2)),-1)
        mask=torch.cat((valid,valid,torch.zeros_like(valid)),1)
        known=torch.cat((mask[None].expand(b,-1,-1),torch.zeros_like(states,dtype=torch.bool),self.memory_known.transpose(1,2)),-1)
        sources=torch.where(self.sources<0,INPUTS-self.sources-1,self.sources).long()
        z=self.bias[:,:,None].expand(-1,-1,u).clone();ok=torch.zeros_like(z,dtype=torch.bool)
        for e in range(FAN_IN):
            idx=sources[:,:,e][:,None,:].expand(-1,u,-1);w=self.weights[:,:,e,None]
            z=z+bank.gather(2,idx).transpose(1,2)*w;ok=ok|(known.gather(2,idx).transpose(1,2)&(w!=0))
        h=z.tanh();bits=torch.zeros((b,u),device=v.device,dtype=torch.int16)
        for a in range(6):
            zs=[];ks=[]
            for k in range(2):
                value=self.hb[:,a,k,None].expand(-1,u).clone();good=torch.zeros_like(value,dtype=torch.bool)
                for e in range(FAN_IN):
                    ix=self.hs[:,a,k,e].long()[:,None,None].expand(-1,1,u);w=self.hw[:,a,k,e,None]
                    value=value+h.gather(1,ix).squeeze(1)*w;good=good|(ok.gather(1,ix).squeeze(1)&(w!=0))
                zs.append(value.tanh());ks.append(good)
            x=ks[0]&(zs[0]>self.ht[:,a,0,None]);y=ks[1]&(zs[1]>self.ht[:,a,1,None]);mode=self.modes[:,a,None]
            decision=torch.where(mode==0,x,torch.where(mode==1,x|y,torch.where(mode==2,x&y,torch.where(mode==3,x^y,(x==y)&ks[0]&ks[1]))))
            if a<5:bits=bits|(decision.to(torch.int16)<<a)
            selected=torch.where(mode==0,zs[0],torch.where(x,torch.where(y,torch.maximum(zs[0],zs[1]),zs[0]),torch.where(y,zs[1],torch.maximum(zs[0],zs[1]))))
            ratio=(selected*self.sizes[:,a,0,None]+self.sizes[:,a,1,None]).tanh()*.5+.5
            if a in (0,2,3):self.ratios[0,...,(0,2,3).index(a)].copy_(ratio)
            if a==5:
                self.ratios[0,...,3].copy_(.001+.199*ratio);bits=bits|(ks[0].to(torch.int16)<<5)
        self.memory.copy_(h);self.memory_known.copy_(ok)
        return bits[None]
    def close(self):
        for k in ('workspace','known','memory','memory_known','feedback','position_states','buffer_phase'):setattr(self,k,None)
        self.workspace_shape=None
