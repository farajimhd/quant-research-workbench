"""Capture chronological GPU transitions without a host launch per second."""
from research.vectorized_backtest.v6.torch_backtest.compile_lifecycle import scoped_compile
import torch
import time
import os
from collections import deque
from dataclasses import dataclass


@dataclass
class CapturedReplay:
    """Independent replay state retained for finite scheduling experiments."""
    graph: object
    reset: object
    total: int
    block: int
    keepalive: object
    index: object
    preparation_profile: object = None
    execution_gate: object = None

    def enqueue(self):
        if self.execution_gate is not None:
            from .gpu_gate import enqueue_turns
            return enqueue_turns(self.graph,self.total,self.block,self.execution_gate,self.index.device)
        for _ in range(0, self.total, self.block):
            self.graph.replay()


def replay(position, aggregate, opened, policy, prices, observed, membership,
           signals, values, ids, columns, age_limits, lengths, stateful,
           execution, transition, select_signals, progress=None, block=32, episodes=None,protected_kernel=True,signal_provider=None,network_plan=None,capture_only=False,*,bids,asks,quote_valid,ticker_state=None,execution_gate=None):
    device=position.device
    total=prices.shape[0]-1
    index=torch.ones((),dtype=torch.int64,device=device)
    if execution.risk_protection and protected_kernel:
        from .protected_transition import transition as protected_transition

    from .network import outcome_values
    ratios=network_plan.ratios;feedback=network_plan.feedback

    def transition_tick():
        # Device-side index stays dynamic across graph replays. Tail clocks
        # are clamped for safe reads and masked out of every state update.
        clock=index.clamp_max(total)
        take=lambda x,i:torch.index_select(x,0,i.reshape(1)).squeeze(0)
        held=position[...,0]>1e-12
        selected=select_signals(signals,held,clock-1-opened,age_limits,False)
        swing=values[take(ids,clock)[:,None,:],columns[None,:,None]]
        selected_policy=policy
        args=(position,aggregate,take(prices,clock),take(prices,clock-1),take(observed,clock),take(membership,clock-1),selected,swing,selected_policy,clock.to(torch.float64),(index==lengths-1)[:,None,None],execution.entry_dollars,execution.add_dollars,execution.cost_bps/10000.,execution.fee_per_share,execution.minimum_order_fee)
        if execution.risk_protection and protected_kernel:
            p,a=protected_transition(*args,execution.admission_distance,execution.admission_budget,ratios,execution.minimum_order_dollars,take(bids,clock),take(asks,clock),take(quote_valid,clock))
        else:
            p,a=transition(*args,execution.risk_protection,execution.admission_distance,execution.admission_budget,ratios,execution.minimum_order_dollars,bid=take(bids,clock),ask=take(asks,clock),quote_valid=take(quote_valid,clock))
        active=index<lengths
        if ticker_state is not None:
            from .ticker_selection import update_equity
            update_equity(ticker_state,p,take(prices,clock),active[:,None,None])
        closed=held&(p[...,0]<=1e-12)&active[:,None,None]
        profits=p[...,7]-position[...,8]
        entry_clocks=opened.clone() if episodes is not None and episodes.trace else None
        opened.copy_(torch.where(active[:,None,None]&~held&(p[...,0]>1e-12),clock,opened))
        feedback.copy_(outcome_values(position,p,take(asks,clock),selected,ratios,feedback))
        position.copy_(torch.where(active[:,None,None,None],p,position))
        aggregate.copy_(torch.where(active[:,None,None],a,aggregate))
        index.add_(1)
        return (closed,profits,entry_clocks,clock) if episodes is not None and episodes.trace else (closed,profits)

    # Keep scans out of the financial reduction region: Torch's split-scan
    # generator cannot emit fused reductions for production listing widths.
    # Inductor functionalizes ledger scatter mutations and copies the full bounded
    # buffers every tick. Capture direct in-place ledger kernels instead: measured
    # 32x faster ledger appends at 512x622, still no host launch per clock.
    dynamic=os.environ.get('V42_TRANSITION_DYNAMIC','0')=='1'
    compiled_transition=scoped_compile(transition_tick,fullgraph=True,dynamic=dynamic)
    append_ledger=episodes.append_events if episodes is not None else None
    def compiled():
        if signal_provider is not None:signal_provider(index.clamp_max(total)-1,position,opened)
        if episodes is not None and episodes.trace:closed,profits,entry_clocks,exit_clock=compiled_transition()
        else:closed,profits=compiled_transition()
        if append_ledger is not None:
            if episodes.trace:append_ledger(closed,profits,entry_clocks,exit_clock)
            else:append_ledger(closed,profits)
    def reset():
        network_plan.reset()
        if ticker_state is not None:ticker_state.zero_()
        position.zero_();aggregate.zero_();aggregate[...,8]=float('inf')
        opened.zero_();index.fill_(1)
        if episodes is not None:episodes.reset()

    torch.cuda.synchronize(device)
    stream=torch.cuda.Stream(device=device)
    stream.wait_stream(torch.cuda.current_stream(device))
    with torch.cuda.stream(stream),torch.inference_mode():
        warm_started=time.perf_counter()
        compiled();compiled()
        warm_seconds=time.perf_counter()-warm_started
        reset()
        graph=torch.cuda.CUDAGraph()
        capture_started=time.perf_counter()
        with torch.cuda.graph(graph,stream=stream):
            for _ in range(block):compiled()
        capture_seconds=time.perf_counter()-capture_started
        reset()
    torch.cuda.current_stream(device).wait_stream(stream)
    if capture_only:
        torch.cuda.synchronize(device)
        return CapturedReplay(graph, reset, total, block, compiled, index,
                              dict(compiler_dynamic=dynamic,compiler_warmup_host_seconds=warm_seconds,
                                   graph_capture_host_seconds=capture_seconds),execution_gate)
    if execution_gate is not None:
        from .gpu_gate import enqueue_turns
        return enqueue_turns(graph,total,block,execution_gate,device,progress)
    pending=deque();reported=0;completed=0;last_report=0.
    replay_started=time.perf_counter()
    gpu_start=torch.cuda.Event(enable_timing=True);gpu_end=torch.cuda.Event(enable_timing=True);gpu_start.record()
    def publish_completed(force=False):
        nonlocal reported,completed,last_report
        while pending and pending[0][1].query():completed=pending.popleft()[0]
        now=time.perf_counter()
        if completed>reported and (force or now-last_report>=.5 or completed==total):
            progress(completed,total);reported=completed;last_report=now
    for begin in range(0,total,block):
        graph.replay()
        done=min(total,begin+block)
        if progress is not None and (done%256==0 or done==total):
            event=torch.cuda.Event();event.record();pending.append((done,event));publish_completed()
    while pending:
        publish_completed()
        if pending:time.sleep(.025)
    torch.cuda.synchronize(device)
    gpu_end.record();gpu_end.synchronize()
    network_plan.last_replay_profile=dict(compiler_warmup_seconds=warm_seconds,graph_capture_seconds=capture_seconds,
        chronological_replay_seconds=time.perf_counter()-replay_started,gpu_elapsed_seconds=gpu_start.elapsed_time(gpu_end)/1000)
    if progress is not None:publish_completed(force=True)
