"""Fixed-shape CPU packing; each candidate allocates exactly one core."""
import numpy as np
from .genome import HIDDEN,FAN_IN,MODES
def pack_networks(members):
    b=len(members)
    s=np.zeros((b,HIDDEN,FAN_IN),np.int32);w=np.zeros_like(s,dtype=np.float32);bias=np.zeros((b,HIDDEN),np.float32)
    hs=np.zeros((b,6,2,FAN_IN),np.int32);hw=np.zeros_like(hs,dtype=np.float32);hb=np.zeros((b,6,2),np.float32);ht=hb.copy()
    modes=np.zeros((b,6),np.int32);sizes=np.zeros((b,6,2),np.float32)
    for j,m in enumerate(members):
        m.validate();n=m.networks['shared']
        for i,x in enumerate(n.nodes):s[j,i,:len(x.sources)]=x.sources;w[j,i,:len(x.weights)]=x.weights;bias[j,i]=x.bias
        for i,h in enumerate(n.heads):
            modes[j,i]=MODES.index(h.mode);sizes[j,i]=(h.size_weight,h.size_bias)
            for k,x in enumerate(h.branches):hs[j,i,k,:len(x.sources)]=x.sources;hw[j,i,k,:len(x.weights)]=x.weights;hb[j,i,k]=x.bias;ht[j,i,k]=x.threshold
    return dict(values=(s,w,bias,hs,hw,hb,ht,modes,sizes),total_nodes=b*HIDDEN,kernel_nodes=HIDDEN,offsets_cpu=np.arange(b)*HIDDEN)
def select_packed(packed,selection):
    i=np.asarray(selection,dtype=np.int64)
    return dict(values=tuple(x[i] for x in packed['values']),total_nodes=len(i)*HIDDEN,kernel_nodes=HIDDEN,offsets_cpu=np.arange(len(i))*HIDDEN)
