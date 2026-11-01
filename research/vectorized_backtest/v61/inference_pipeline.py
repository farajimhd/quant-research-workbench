"""Two bounded signal packets, independent producer and execution CUDA streams."""
import time,threading
import torch
from .market_inference import Inference


class Pipeline:
    def __init__(self,plan,data,total,block,signals,policy,columns,profile,*,packet_blocks=1):
        self.routed=hasattr(plan,'base');b,u=signals.shape[1:]
        maximum=1
        for multiplier in (16,8,4,2):
            bytes_needed=3*block*multiplier*b*u*(34 if self.routed else 26)
            if multiplier<=packet_blocks and bytes_needed<torch.cuda.mem_get_info(data.device)[0]*.20:
                maximum=multiplier;break
        block*=maximum
        self.plan=plan;self.total=total;self.block=block;self.signals=signals
        self.policy=policy;self.columns=columns;self.profile=profile
        self.engine=Inference(plan,data,signals.shape[-1],compact_routing=True)
        self.stream=torch.cuda.Stream(device=data.device)
        self.stream.wait_stream(torch.cuda.current_stream())
        b,u=signals.shape[1:];self.routed=hasattr(plan,'base')
        def packet():
            return [torch.empty((block,b,u),device=data.device,dtype=torch.int16),
                    torch.empty((block,b,u,3),device=data.device,dtype=torch.float64),
                    None,None,
                    torch.empty((block,b,u),device=data.device,dtype=torch.int64) if self.routed else None]
        self.active=packet();self.slots=[packet(),packet()]
        self.starts=[torch.zeros((),device=data.device,dtype=torch.int64) for _ in range(2)]
        self.ready=[torch.cuda.Event() for _ in range(2)];self.free=[None,None]
        self.graphs=[];self.submitted={}
        self.condition=threading.Condition();self.stop=threading.Event();self.producer=None;self.error=None
        self.device=data.device;self.measurements=[];self.enqueued=0
        def infer(slot):
            for offset in range(0,block,self.engine.width):
                width=min(self.engine.width,block-offset)
                indices=(torch.arange(width,device=data.device)+offset+self.starts[slot]).clamp_max(total)
                values=self.engine(indices)
                for target,value in zip(self.slots[slot],values):
                    if target is not None:target[offset:offset+width].copy_(value)
        started=time.perf_counter()
        with torch.cuda.stream(self.stream),torch.inference_mode():
            infer(0);self.stream.synchronize()
            for slot in range(2):
                graph=torch.cuda.CUDAGraph()
                with torch.cuda.graph(graph,stream=self.stream):infer(slot)
                self.graphs.append(graph)
        profile['inference_setup_seconds']=time.perf_counter()-started
        profile['inference_time_batch']=self.engine.width
        profile['entry_listing_slots']=self.engine.entry_indices.shape[1]
        profile['model_base_features']=len(self.engine.base.columns)
        profile['feature_layout_seconds']=self.engine.base.feature_layout_seconds
        profile['lag_features']=len(self.engine.base.cross_columns)
        profile['inference_workspace_bytes']=self.engine.work.numel()*5
        profile['inference_packet_clocks']=block
        profile['inference_execution_overlap']=True
        profile['inference_position_feedback']=False
        profile['inference_network_recurrence']=False
        self.submit(0);self.submit(block)
        self.stage(0,consume=False)

    def submit(self,begin):
        # Compatibility for synchronous consumers; the independent producer
        # owns all submissions once started. Execution never schedules it.
        if self.producer is not None:return
        self._submit(begin)

    def _submit(self,begin):
        if begin>self.total or begin in self.submitted:return
        slot=(begin//self.block)%2
        with torch.cuda.stream(self.stream):
            if self.free[slot] is not None:self.stream.wait_event(self.free[slot])
            start=torch.cuda.Event(enable_timing=True);end=torch.cuda.Event(enable_timing=True)
            start.record(self.stream)
            self.starts[slot].fill_(begin);self.graphs[slot].replay();self.ready[slot].record(self.stream)
            end.record(self.stream);self.measurements.append((start,end));self.enqueued+=1
        self.submitted[begin]=slot

    def start(self,begin=None):
        """Market-only producer; bounded storage is its sole consumer dependency.

        Start after execution graph capture, because CUDA forbids concurrent
        capture-time submissions from another thread in the same context.
        """
        if self.producer is not None:return
        self.stop.clear();self.error=None
        first=2*self.block if begin is None else begin
        def produce():
            try:
                with torch.cuda.device(self.device),torch.inference_mode():
                    for at in range(first,self.total+1,self.block):
                        slot=(at//self.block)%2
                        with self.condition:
                            self.condition.wait_for(lambda:self.stop.is_set() or slot not in self.submitted.values())
                            if self.stop.is_set():return
                            self._submit(at);self.condition.notify_all()
            except BaseException as exc:
                with self.condition:self.error=exc;self.condition.notify_all()
        self.producer=threading.Thread(target=produce,name='market-inference-producer',daemon=True)
        self.producer.start();self.profile['inference_independent_producer']=True

    def await_ready(self,begin):
        """CPU readiness only; the GPU event still protects packet consumption."""
        if begin%self.block:return
        started=time.perf_counter()
        with self.condition:
            if self.producer is not None:
                self.condition.wait_for(lambda:begin in self.submitted or self.error is not None or self.stop.is_set())
                if self.error is not None:raise self.error
                if self.stop.is_set():raise InterruptedError('Inference producer stopped')
        self.profile['inference_pre_gate_wait_seconds']=self.profile.get('inference_pre_gate_wait_seconds',0.)+time.perf_counter()-started

    def stage(self,begin,consume=True):
        if begin%self.block:return
        started=time.perf_counter()
        with self.condition:
            if self.producer is not None:
                self.condition.wait_for(lambda:begin in self.submitted or self.error is not None or self.stop.is_set())
                if self.error is not None:raise self.error
                if self.stop.is_set():raise InterruptedError('Inference producer stopped')
            slot=self.submitted[begin]
            current=torch.cuda.current_stream();current.wait_event(self.ready[slot])
            for target,value in zip(self.active,self.slots[slot]):
                if target is not None:target.copy_(value)
            event=torch.cuda.Event();event.record(current);self.free[slot]=event
            if consume:del self.submitted[begin]
            self.condition.notify_all()
        self.profile['inference_consumer_host_wait_seconds']=self.profile.get('inference_consumer_host_wait_seconds',0.)+time.perf_counter()-started

    def rewind(self,begin):
        # Journal retry may revisit already consumed packets. Recompute market
        # signals without changing strategy state, which no longer exists.
        independent=self.producer is not None
        self._stop_producer();self.stream.synchronize();self.submitted.clear()
        self.submit(begin);self.submit(begin+self.block)
        if independent:self.start(begin+2*self.block)

    def provide(self,index):
        from .inference_packet import consume
        consume(self,index)

    def close(self):
        self._stop_producer()
        self.stream.synchronize()
        self.profile['inference_stream_seconds']=sum(a.elapsed_time(b) for a,b in self.measurements)/1000
        self.profile['inference_packets_submitted']=self.enqueued

    def _stop_producer(self):
        self.stop.set()
        with self.condition:self.condition.notify_all()
        if self.producer is not None:self.producer.join();self.producer=None
