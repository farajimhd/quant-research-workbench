"""Fixed-shape native lifecycle rule capture; identical causal Torch arithmetic.

The caller must hold the global preparation barrier: no concurrent replay or
capture may run while constructing this object. Input buffers and rule tensors
remain owned until the caller synchronizes and releases the captured graph.
"""
import torch
from .evolution import STAGES


class CapturedRules:
    def __init__(self,programs,shape,device):
        self.values=torch.zeros(shape,device=device)
        self.valid=torch.zeros(shape,device=device,dtype=torch.bool)
        self.programs=programs
        stream=torch.cuda.Stream(device=device)
        stream.wait_stream(torch.cuda.current_stream(device))
        with torch.cuda.stream(stream),torch.inference_mode():
            for _ in range(2):self._evaluate()
            self.graph=torch.cuda.CUDAGraph()
            with torch.cuda.graph(self.graph,stream=stream):self.output=self._evaluate()
        torch.cuda.current_stream(device).wait_stream(stream)

    def _evaluate(self):
        batch=len(self.programs[STAGES[0]].programs)
        packed=torch.zeros((batch,*self.values.shape[:-1]),dtype=torch.uint8,device=self.values.device)
        for bit,stage in enumerate(STAGES):
            value,known=self.programs[stage](self.values,self.valid)
            packed.bitwise_or_(((value!=0)&known).to(torch.uint8)*(1<<bit))
        return packed

    def replay(self):
        self.graph.replay()
        return self.output
