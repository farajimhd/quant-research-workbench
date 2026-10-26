"""Ragged recurrent storage; tile launch lists avoid cohort-maximum padding."""
import numpy as np
from .genome import FAN_IN,MODES

def tile_rows(counts,tile):
    return np.asarray([(b,h) for b,n in enumerate(counts) for h in range(0,int(n),tile)],np.int32).reshape(-1,2)

def pack_networks(members):
    counts=np.asarray([len(m.networks['shared'].nodes) for m in members],np.int32)
    offsets=np.cumsum(np.r_[0,counts[:-1]]).astype(np.int32);total=int(counts.sum());b=len(members)
    s=np.zeros((total,FAN_IN),np.int32);w=np.zeros_like(s,dtype=np.float32);bias=np.zeros(total,np.float32);uids=np.zeros(total,np.int64)
    hs=np.zeros((b,6,2,FAN_IN),np.int32);hw=np.zeros_like(hs,dtype=np.float32);hb=np.zeros((b,6,2),np.float32);ht=hb.copy()
    modes=np.zeros((b,6),np.int32);sizes=np.zeros((b,6,2),np.float32)
    for j,m in enumerate(members):
        m.validate();n=m.networks['shared'];base=int(offsets[j])
        for i,x in enumerate(n.nodes):s[base+i,:len(x.sources)]=x.sources;w[base+i,:len(x.weights)]=x.weights;bias[base+i]=x.bias;uids[base+i]=x.uid
        for i,h in enumerate(n.heads):
            modes[j,i]=MODES.index(h.mode);sizes[j,i]=(h.size_weight,h.size_bias)
            for k,x in enumerate(h.branches):hs[j,i,k,:len(x.sources)]=x.sources;hw[j,i,k,:len(x.weights)]=x.weights;hb[j,i,k]=x.bias;ht[j,i,k]=x.threshold
    return dict(values=(s,w,bias,hs,hw,hb,ht,modes,sizes),counts=counts,offsets_cpu=offsets,uids=uids,total_nodes=total,kernel_nodes=int(counts.max()))

def select_packed(packed,selection):
    ix=np.asarray(selection,dtype=np.int64);counts=packed['counts'][ix];offsets=np.cumsum(np.r_[0,counts[:-1]]).astype(np.int32)
    rows=np.concatenate([np.arange(packed['offsets_cpu'][i],packed['offsets_cpu'][i]+packed['counts'][i]) for i in ix])
    values=packed['values']
    return dict(values=tuple(x[rows] if j<3 else x[ix] for j,x in enumerate(values)),counts=counts,offsets_cpu=offsets,uids=packed['uids'][rows],total_nodes=int(counts.sum()),kernel_nodes=int(counts.max()))
