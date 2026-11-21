import torch
from .objectives import objective
from ..phase1_v12.data import prepare
from ..phase1_v12.step import copy_batch,InferenceReplay
from ..phase1_v10.step import stable_clip


def configure(model,contract):
    model.loss_arm=contract['loss_arm']
    rms=torch.tensor(contract['activity_calibration']['mean_square_log_change'],device=next(model.parameters()).device,dtype=torch.float32)
    model.activity_scale=torch.where(rms>0,rms,1)


def backward(model,teacher,b,pos):
    latent,meta,valid,_,source_valid=prepare(teacher,b)
    prediction=model(latent,meta,pos)
    loss,parts=objective(model,teacher,prediction,latent,valid,pos,b.grid,source_valid,model.loss_arm,b.frames,model.activity_scale)
    loss.backward();norm=stable_clip(model.parameters())
    return loss.detach(),torch.stack([parts[k].detach() for k in ('body','activity_log_mse','activity_delta_mse','activity_tail')]),norm


class Replay:
    def __init__(self,model,teacher,b,pos):
        self.batch=b.map(lambda x:x.clone());self.pos=pos.clone();self.graph=torch.cuda.CUDAGraph();stream=torch.cuda.Stream()
        stream.wait_stream(torch.cuda.current_stream())
        with torch.cuda.stream(stream):
            for _ in range(3):model.zero_grad(set_to_none=True);backward(model,teacher,self.batch,self.pos)
        torch.cuda.current_stream().wait_stream(stream);model.zero_grad(set_to_none=True)
        with torch.cuda.graph(self.graph,stream=stream):self.output=backward(model,teacher,self.batch,self.pos)
        self.params=list(model.parameters());self.grads=[p.grad for p in self.params]

    def __call__(self,b,pos):
        copy_batch(self.batch,b);self.pos.copy_(pos)
        for p,g in zip(self.params,self.grads):p.grad=g
        self.graph.replay();return self.output
