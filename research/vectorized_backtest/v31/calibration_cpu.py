"""Bounded CPU-only activation scan. No fastmath, source cache or CUDA context."""
import numpy as np
from numba import njit

@njit(cache=False,fastmath=False)
def scan(inputs,known,held,src,weights,bias,counts,market_inputs,features,entry):
    windows,ticks,width,_=inputs.shape;nodes=len(counts)
    memory=np.zeros((nodes,windows,width),np.float32);valid_memory=np.zeros((nodes,windows,width),np.bool_)
    values=np.zeros_like(memory);valid_values=np.zeros_like(valid_memory)
    outputs=np.empty((windows,ticks,width),np.float32);valid=np.empty((windows,ticks,width),np.bool_)
    saturated=0;total=0
    for tick in range(ticks):
        for node in range(nodes):
            for window in range(windows):
                for listing in range(width):
                    z=bias[node];ok=False
                    for edge in range(counts[node]):
                        w=weights[node,edge]
                        if w==0:continue
                        s=src[node,edge];mask=False
                        if s<0:
                            v=memory[-s-1,window,listing];mask=valid_memory[-s-1,window,listing]
                        elif s<market_inputs:
                            v=inputs[window,tick,listing,s]
                            if s<2*features:mask=known[window,tick,listing,s%features]
                        elif s<inputs.shape[-1]:
                            v=inputs[window,tick,listing,s] if entry else held[window,tick,listing,s-market_inputs]
                        else:
                            v=values[s-inputs.shape[-1],window,listing];mask=valid_values[s-inputs.shape[-1],window,listing]
                        product=np.float32(v*w);z=np.float32(z+product);ok=ok or mask
                    value=np.tanh(z);values[node,window,listing]=value;valid_values[node,window,listing]=ok
                    if ok:
                        total+=1
                        if abs(value)>.99:saturated+=1
        for window in range(windows):
            for listing in range(width):
                outputs[window,tick,listing]=values[nodes-1,window,listing];valid[window,tick,listing]=valid_values[nodes-1,window,listing]
        memory[:]=values;valid_memory[:]=valid_values
    return outputs,valid,saturated,max(1,total)

def observe(net,probe,stage,market_inputs,features):
    nodes=len(net.nodes);width=max(len(n.sources) for n in net.nodes)
    sources=np.zeros((nodes,width),np.int32);weights=np.zeros((nodes,width),np.float32)
    for i,n in enumerate(net.nodes):sources[i,:len(n.sources)]=n.sources;weights[i,:len(n.weights)]=n.weights
    return scan(probe['inputs'],probe['known'],probe['held_states'],sources,weights,
        np.array([n.bias for n in net.nodes],np.float32),np.array([len(n.sources) for n in net.nodes],np.int32),market_inputs,features,stage=='entry')
