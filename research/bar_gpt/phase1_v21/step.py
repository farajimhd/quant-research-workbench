"""Captured joint backward; optimizer stays outside the graph."""
import torch
from ..phase1_v20.model import set_gradient_scope
from .objectives import loss
from ..phase1_v10.step import stable_clip

PARTS=('range','wick','activity','range_bins','activity_direction','joint_base')


def backward(model,inputs,scale,decoder_visible_only=False):
    set_gradient_scope(model,inputs[4].shape[1],decoder_visible_only)
    value,parts=loss(model,*inputs,scale)
    value.backward();norm=stable_clip(model.parameters())
    return value.detach(),torch.stack([parts[k].detach() for k in PARTS]),norm


class Replay:
    def __init__(self,model,inputs,scale,decoder_visible_only=False):
        self.model=model;self.inputs=tuple(x.clone() for x in inputs);self.decoder_visible_only=decoder_visible_only
        self.graph=torch.cuda.CUDAGraph();stream=torch.cuda.Stream();stream.wait_stream(torch.cuda.current_stream())
        with torch.cuda.stream(stream):
            for _ in range(3):model.zero_grad(set_to_none=True);backward(model,self.inputs,scale,decoder_visible_only)
        torch.cuda.current_stream().wait_stream(stream);model.zero_grad(set_to_none=True)
        with torch.cuda.graph(self.graph,stream=stream):self.output=backward(model,self.inputs,scale,decoder_visible_only)
        self.params=list(model.parameters());self.grads=[p.grad for p in self.params]

    def __call__(self,inputs):
        set_gradient_scope(self.model,inputs[4].shape[1],self.decoder_visible_only)
        for dst,src in zip(self.inputs,inputs):dst.copy_(src)
        for p,g in zip(self.params,self.grads):p.grad=g
        self.graph.replay();return self.output


def qualified_replay(model,inputs,scale,decoder_visible_only=False):
    model.zero_grad(set_to_none=True);expected=backward(model,inputs,scale,decoder_visible_only)
    expected=tuple(x.clone() for x in expected);gradients=[None if p.grad is None else p.grad.clone() for p in model.parameters()]
    replay=Replay(model,inputs,scale,decoder_visible_only);actual=replay(inputs)
    for x,y in zip(actual,expected):torch.testing.assert_close(x,y,rtol=1e-4,atol=2e-5)
    for p,g in zip(model.parameters(),gradients):
        if g is None:
            if p.grad is not None:raise ValueError('Graph activated unused gradient')
        else:torch.testing.assert_close(p.grad,g,rtol=1e-3,atol=2e-5)
    return replay,actual
