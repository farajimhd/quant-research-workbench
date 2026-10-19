"""Bounded neural operators; edge order and neutral missing inputs are explicit."""
import numpy as np

def evaluate(op,terms,bias,like):
    """Terms include zero weights (attention slots retain their role)."""
    import torch
    tensor=isinstance(like,torch.Tensor)
    tanh=torch.tanh if tensor else np.tanh
    exp=torch.exp if tensor else np.exp
    z=like*0+np.float32(bias).item()
    if op=='mean':
        total=like*0
        for x in terms:total=total+x
        return tanh(total/max(1,len(terms))+bias)
    if op in ('tanh','conv','identity'):
        for x in terms:z=z+x
        if op=='identity':return z.clamp(-1,1) if tensor else np.clip(z,-1,1)
        if op=='mean':z=(z-bias)/max(1,len(terms))+bias
        return tanh(z)
    if op in ('max','min'):
        value=terms[0]
        for x in terms[1:]:value=(torch.maximum(value,x) if op=='max' else torch.minimum(value,x)) if tensor else (np.maximum(value,x) if op=='max' else np.minimum(value,x))
        return tanh(z+value)
    if op=='product':
        value=terms[0]
        for x in terms[1:]:value=value*x
        return tanh(z+value)
    if op=='gate':
        for x in terms[1:]:z=z+x
        return tanh(z)/(1+exp(-terms[0]))
    if op=='attention':
        logits=[terms[0]*terms[i] for i in range(1,len(terms),2)]
        peak=logits[0]
        for x in logits[1:]:peak=torch.maximum(peak,x) if tensor else np.maximum(peak,x)
        denominator=like*0;numerator=like*0
        for i,x in enumerate(logits):
            w=exp(x-peak);denominator=denominator+w;numerator=numerator+w*terms[2+2*i]
        return tanh(z+numerator/denominator)
    raise ValueError('Unknown operator '+op)
