"""Bounded, worker-isolated replay of unchanged fixed-tile frozen V4 encoding."""
import threading
import torch


class TeacherReplay:
    def __init__(self,teacher,tile,workers,*,memory_mib=16384):
        if teacher.training or any(p.requires_grad for p in teacher.parameters()):
            raise ValueError('Teacher replay requires a frozen evaluation encoder')
        device=next(teacher.parameters()).device
        if device.type!='cuda' or not 1<=workers<=8 or tile<1 or memory_mib<1:
            raise ValueError('Invalid CUDA teacher-replay resources')
        self.original=teacher.encode_full;self.graphs=[];self.bindings={}
        self.lock=threading.Lock();self.leased=False
        cpu=torch.get_rng_state();gpu=torch.cuda.get_rng_state_all()
        initial=torch.cuda.memory_allocated(device)
        try:
            # Capture BEFORE preparation workers exist. Each replay instance owns
            # private buffers, output and graph pool; none is shared by workers.
            for _ in range(workers):
                market=torch.zeros((tile,64,18),device=device)
                valid=torch.ones_like(market,dtype=torch.bool)
                temporal=torch.zeros((tile,64,27),device=device);time=temporal[...,18:]
                temporal_valid=torch.ones((tile,64,27),device=device,dtype=torch.bool);tv=temporal_valid[...,18:]
                stream=torch.cuda.Stream(device=device);stream.wait_stream(torch.cuda.current_stream(device))
                with torch.cuda.stream(stream),torch.no_grad(),torch.autocast('cuda',dtype=torch.bfloat16,cache_enabled=False):
                    for _ in range(3):self.original(market,valid,time,tv)
                stream.synchronize()
                graph=torch.cuda.CUDAGraph()
                with torch.cuda.graph(graph,stream=stream),torch.no_grad(),torch.autocast('cuda',dtype=torch.bfloat16,cache_enabled=False):
                    result=self.original(market,valid,time,tv)
                self.graphs.append((graph,(market,valid,time,tv),result))
                if torch.cuda.memory_allocated(device)-initial>memory_mib*1024**2:
                    raise MemoryError('Frozen encoder replay exceeds its explicit device budget')
        finally:
            torch.set_rng_state(cpu);torch.cuda.set_rng_state_all(gpu)

    def begin(self):
        with self.lock:
            if self.leased:raise RuntimeError('Teacher replay already belongs to an active publication pool')
            self.leased=True;self.bindings.clear()

    def end(self):
        # Caller drains ALL preparation streams before releasing these buffers.
        with self.lock:self.bindings.clear();self.leased=False

    def encode(self,*values):
        example=self.graphs[0][1]
        if any(a.shape!=b.shape or a.stride()!=b.stride() or a.dtype!=b.dtype or a.device!=b.device
               for a,b in zip(values,example)):
            return self.original(*values)  # Original partial-tile/layout behavior.
        stream=torch.cuda.current_stream(values[0].device).cuda_stream
        with self.lock:
            if not self.leased:raise RuntimeError('Teacher replay requires an explicit worker-pool lease')
            if stream not in self.bindings:
                if len(self.bindings)>=len(self.graphs):raise RuntimeError('Too many encoder preparation streams')
                self.bindings[stream]=len(self.bindings)
            graph,buffers,result=self.graphs[self.bindings[stream]]
        for a,b in zip(buffers,values):a.copy_(b)
        graph.replay()
        # Return independent ownership. A later replay cannot mutate an embedding
        # retained by a caller; copy is ordered on this same worker CUDA stream.
        return result.clone()
