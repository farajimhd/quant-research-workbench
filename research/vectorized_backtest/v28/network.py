"""Sparse packed networks; one fused CUDA kernel for all five action graphs."""
import numpy as np
import torch
from .genome import INPUTS,MARKET_INPUTS,STAGES
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
        self.columns=sorted({s%len(CATALOG) for m in members for net in m.networks.values() for n in net.nodes for s in n.sources if s<MARKET_INPUTS})
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
                        sources[b,a,j,e]=(s//len(CATALOG))*c+self.lookup[s%len(CATALOG)] if s<MARKET_INPUTS else 3*c+s-MARKET_INPUTS
                        weights[b,a,j,e]=w
        self.sources,self.weights,self.bias,self.counts,self.threshold=[torch.as_tensor(x,device=device) for x in (sources,weights,bias,counts,threshold)]
        self.logarithmic=torch.tensor([CATALOG[s].unit not in ('ratio','bool') for s in self.columns],device=device)
        self.kernel_nodes=1<<(max(len(n.nodes) for m in members for n in m.networks.values())-1).bit_length()
    def project(self,data,index):
        bank=data.resident_features;a=bank.tensors
        rows=a['source'].index_select(0,index.reshape(1)).T;ids=a['ids'].index_select(0,index.reshape(1)).T.to(torch.int64);clock=a['clocks'].index_select(0,index.reshape(1))[None]
        v,m=bank._values(rows,ids,clock,self.columns)
        v=v[:,0];valid=m[:,0]&torch.isfinite(v)
        # All statistics use only this causal clock's available cross-section.
        # Absolute channels remain available so normalization cannot erase levels.
        clean=torch.where(valid,v,0.)
        absolute=torch.where(self.logarithmic,clean.sign()*clean.abs().log1p()/4,clean).tanh()
        count=valid.sum(0).clamp_min(1)
        center=clean.sum(0)/count
        variance=torch.where(valid,(clean-center).square(),0.).sum(0)/count
        scale=variance.sqrt().clamp_min(1e-6)
        relative=((clean-center)/(2*scale)).tanh()
        return torch.cat((torch.where(valid,absolute,0.),torch.where(valid,relative,0.),valid.to(torch.float32)),1),valid
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
                    z=torch.full((v.shape[0],),n.bias,dtype=torch.float32,device=v.device);ok=torch.zeros_like(z,dtype=torch.bool)
                    for source,w in zip(n.sources,n.weights):
                        if source<MARKET_INPUTS:
                            channel=source//len(CATALOG);column=self.lookup[source%len(CATALOG)]
                            x=v[:,channel*len(self.columns)+column];mask=valid[:,column] if channel<2 else torch.zeros_like(ok)
                        elif source<INPUTS:x=states[b,:,source-MARKET_INPUTS];mask=torch.zeros_like(ok)
                        else:x=outputs[source-INPUTS];mask=masks[source-INPUTS]
                        z=z+x*w;ok=ok|mask
                    outputs.append(z.tanh());masks.append(ok)
                actions.append(masks[-1]&(outputs[-1]>float(np.float32(net.threshold))))
            rows.append(torch.stack(actions))
        value=torch.stack(rows);result=sum(value[:,i].to(torch.int16)*(1<<i) for i in range(5))[None]
        if out is not None:out.copy_(result);return out
        return result
    def close(self):
        self.workspace=None;self.known=None;self.workspace_shape=None
