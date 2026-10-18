"""Sparse packed networks; one fused CUDA kernel for all five action graphs."""
import numpy as np
import torch
from .genome import INPUTS,STAGES
from .features import CATALOG
from .config import MAX_NODES,FAN_IN,STATES

def state_values(position,previous,opened,clock):
    q,avg,stop,_,_,adds,last,_,_=position.unbind(-1);held=q>1e-12
    values=(held.to(torch.float64),torch.where(held,previous/avg.clamp_min(1e-12)-1,0.),
        torch.where(held,(clock-opened)/3600,0.),q*previous/1000,
        torch.where(held,(avg-stop)/avg.clamp_min(1e-12),0.),adds/20,
        torch.where(held,(clock-last)/300,0.))
    return torch.stack(values,-1).to(torch.float32).tanh()

class NetworkPlan:
    builds=hits=evictions=capture_seconds=0
    def __init__(self,members,device):
        self.members=members;self.device=torch.device(device)
        self.columns=sorted({s for m in members for net in m.networks.values() for n in net.nodes for s in n.sources if s<len(CATALOG)})
        self.lookup={s:i for i,s in enumerate(self.columns)};c=len(self.columns)
        shape=(len(members),5,MAX_NODES,FAN_IN)
        sources=np.zeros(shape,np.int32);weights=np.zeros(shape,np.float32);bias=np.zeros(shape[:3],np.float32);counts=np.zeros(shape[:2],np.int32);threshold=np.zeros(shape[:2],np.float32)
        for b,m in enumerate(members):
            m.validate()
            for a,stage in enumerate(STAGES):
                net=m.networks[stage];counts[b,a]=len(net.nodes);threshold[b,a]=net.threshold
                for j,n in enumerate(net.nodes):
                    bias[b,a,j]=n.bias
                    for e,(s,w) in enumerate(zip(n.sources,n.weights)):
                        sources[b,a,j,e]=self.lookup[s] if s<len(CATALOG) else c+s-len(CATALOG)
                        weights[b,a,j,e]=w
        self.sources,self.weights,self.bias,self.counts,self.threshold=[torch.as_tensor(x,device=device) for x in (sources,weights,bias,counts,threshold)]
        self.center=torch.tensor([(CATALOG[s].upper+CATALOG[s].lower)/2 for s in self.columns],device=device,dtype=torch.float32)
        self.scale=torch.tensor([max((CATALOG[s].upper-CATALOG[s].lower)/2,1e-6) for s in self.columns],device=device,dtype=torch.float32)
    def project(self,data,index):
        bank=data.resident_features;a=bank.tensors
        rows=a['source'].index_select(0,index.reshape(1)).T;ids=a['ids'].index_select(0,index.reshape(1)).T.to(torch.int64);clock=a['clocks'].index_select(0,index.reshape(1))[None]
        v,m=bank._values(rows,ids,clock,self.columns)
        return ((v[:,0]-self.center)/self.scale).tanh(),m[:,0]
    def signals(self,data,index,position,opened,previous,out=None):
        v,valid=self.project(data,index)
        if v.shape[0]<position.shape[2]:
            from torch.nn.functional import pad
            extra=position.shape[2]-v.shape[0];v=pad(v,(0,0,0,extra));valid=pad(valid,(0,0,0,extra),value=False)
        if self.device.type=='cuda':
            from .network_cuda import signals
            return signals(self,v,valid,position,opened,previous,index,out)
        states=state_values(position,previous,opened,index.to(torch.float64))[0];rows=[]
        for b,m in enumerate(self.members):
            actions=[]
            for stage in STAGES:
                net=m.networks[stage]
                outputs=[];masks=[]
                for n in net.nodes:
                    z=torch.full((v.shape[0],),n.bias,dtype=torch.float32,device=v.device);ok=torch.ones_like(z,dtype=torch.bool)
                    for source,w in zip(n.sources,n.weights):
                        if source<len(CATALOG):x=v[:,self.lookup[source]];mask=valid[:,self.lookup[source]]
                        elif source<INPUTS:x=states[b,:,source-len(CATALOG)];mask=torch.ones_like(ok)
                        else:x=outputs[source-INPUTS];mask=masks[source-INPUTS]
                        z=z+x*w;ok=ok&mask
                    outputs.append(z.tanh());masks.append(ok)
                actions.append(masks[-1]&(outputs[-1]>float(np.float32(net.threshold))))
            rows.append(torch.stack(actions))
        value=torch.stack(rows);result=sum(value[:,i].to(torch.int16)*(1<<i) for i in range(5))[None]
        if out is not None:out.copy_(result);return out
        return result
    def close(self):pass
