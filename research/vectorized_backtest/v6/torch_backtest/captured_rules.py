"""Fixed-shape native lifecycle rule capture; identical causal Torch arithmetic.

The caller must hold the global preparation barrier: no concurrent replay or
capture may run while constructing this object. Input buffers and rule tensors
remain owned until the caller synchronizes and releases the captured graph.
"""
import torch
from collections import OrderedDict
from .evolution import STAGES
from .feature_bank import CATALOG
from .program import TorchPrograms


class SharedRuleBatch:
    """Immutable population tensors and bounded, serial-only capture reuse."""
    def __init__(self, members, device, maximum_gib=4., maximum_shapes=8):
        self.members=tuple(members);self.device=torch.device(device)
        if self.device.type=='cuda' and self.device.index is None:
            self.device=torch.device('cuda',torch.cuda.current_device())
        self.programs={stage:TorchPrograms([m.programs()[stage] for m in members],CATALOG,self.device) for stage in STAGES}
        self.captures=OrderedDict();self.maximum_bytes=int(maximum_gib*1024**3)
        self.maximum_shapes=maximum_shapes;self.bytes=0;self.hits=0;self.builds=0

    def capture(self, shape):
        if shape in self.captures:
            self.hits+=1;self.captures.move_to_end(shape)
            return self.captures[shape][0]
        torch.cuda.synchronize(self.device)
        while self.captures:
            if len(self.captures)<self.maximum_shapes and self.bytes<self.maximum_bytes//2:break
            _,(_,size)=self.captures.popitem(last=False);self.bytes-=size
        before=torch.cuda.memory_allocated(self.device)
        capture=CapturedRules(self.programs,shape,self.device)
        torch.cuda.synchronize(self.device)
        size=max(0,torch.cuda.memory_allocated(self.device)-before)
        if size>self.maximum_bytes:raise MemoryError('One shared rule capture exceeds its memory envelope')
        while self.captures and self.bytes+size>self.maximum_bytes:
            _,(_,old)=self.captures.popitem(last=False);self.bytes-=old
        self.captures[shape]=(capture,size);self.bytes+=size;self.builds+=1
        return capture

    def close(self):
        torch.cuda.synchronize(self.device);self.captures.clear();self.programs.clear();self.bytes=0


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
