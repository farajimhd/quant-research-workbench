"""Bounded CPU-only activation scan. No fastmath, source cache or CUDA context."""
import numpy as np
from numba import njit

@njit(cache=False,fastmath=False)
def scan_modules(inputs,known,held,src,weights,bias,counts,ops,market_inputs,features,entry):
    windows,ticks,width,_=inputs.shape;nodes=len(counts)
    memory=np.zeros((nodes,windows,width),np.float32);valid_memory=np.zeros((nodes,windows,width),np.bool_)
    values=np.zeros_like(memory);valid_values=np.zeros_like(valid_memory)
    sums=np.zeros(nodes,np.float64);squares=np.zeros(nodes,np.float64);counts_valid=np.zeros(nodes,np.int64)
    for tick in range(ticks):
        for node in range(nodes):
            terms=np.zeros(counts[node],np.float32)
            for window in range(windows):
                for listing in range(width):
                    z=bias[node];ok=False
                    for edge in range(counts[node]):terms[edge]=0
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
                        product=np.float32(v*w);terms[edge]=product;z=np.float32(z+product);ok=ok or mask
                    op=ops[node]
                    if op==1:z=np.float32(bias[node]+np.max(terms))
                    elif op==2:z=np.float32(bias[node]+np.min(terms))
                    elif op==3:
                        z=np.float32(0)
                        for term in terms:z=np.float32(z+term)
                        z=np.float32(bias[node]+z/np.float32(len(terms)))
                    elif op==4:
                        z=terms[0]
                        for edge in range(1,len(terms)):z=np.float32(z*terms[edge])
                        z=np.float32(z+bias[node])
                    elif op==5:
                        z=bias[node]
                        for edge in range(1,len(terms)):z=np.float32(z+terms[edge])
                    elif op==6:
                        peak=np.float32(-np.inf)
                        for edge in range(1,len(terms),2):peak=max(peak,np.float32(terms[0]*terms[edge]))
                        den=np.float32(0);num=np.float32(0)
                        for edge in range(1,len(terms),2):
                            w=np.exp(np.float32(terms[0]*terms[edge]-peak));den=np.float32(den+w);num=np.float32(num+np.float32(w*terms[edge+1]))
                        z=np.float32(bias[node]+num/den)
                    value=np.tanh(z)
                    if op==5:value=np.float32(value/np.float32(1+np.exp(-terms[0])))
                    elif op==7:value=min(np.float32(1),max(np.float32(-1),z))
                    values[node,window,listing]=value;valid_values[node,window,listing]=ok
                    if ok:
                        sums[node]+=value;squares[node]+=np.float64(value)*value;counts_valid[node]+=1
        memory[:]=values;valid_memory[:]=valid_values
    return sums,squares,counts_valid
