import torch
from .objectives import loss
from ..phase1_v10.step import stable_clip
from ..phase1_v25.step import rate


def backward(model,b):
    x,v,raw,frames,teacher,pp,ap=b;out,_=model(x,v,(pp,ap));value,parts=loss(model,out,x,v,raw,teacher,pp,ap);value.backward()
    norm=torch.stack([stable_clip(g) for _,g in model.groups()]).max()
    return value.detach(),torch.stack([v.detach() for v in parts.values()]),norm.detach()


class Replay:
    def __init__(self,model,b):
        self.b=tuple(t.clone() for t in b);self.graph=torch.cuda.CUDAGraph();stream=torch.cuda.Stream();stream.wait_stream(torch.cuda.current_stream())
        with torch.cuda.stream(stream):
            for _ in range(3):model.zero_grad(set_to_none=True);backward(model,self.b)
        torch.cuda.current_stream().wait_stream(stream);model.zero_grad(set_to_none=True)
        with torch.cuda.graph(self.graph,stream=stream):self.outputs=backward(model,self.b)
        self.params=list(model.parameters());self.grads=[p.grad for p in self.params]
    def __call__(self,b):
        for d,s in zip(self.b,b):d.copy_(s)
        for p,g in zip(self.params,self.grads):p.grad=g
        self.graph.replay();return self.outputs


def qualified(model,b):
    model.zero_grad(set_to_none=True);expected=backward(model,b);grads=[None if p.grad is None else p.grad.clone() for p in model.parameters()]
    replay=Replay(model,b);actual=replay(b);torch.cuda.synchronize()
    torch.testing.assert_close(actual[0],expected[0],rtol=2e-4,atol=2e-4)
    for p,g in zip(model.parameters(),grads):
        if g is not None:torch.testing.assert_close(p.grad,g,rtol=3e-3,atol=2e-5)
    return replay
