"""Capture chronological GPU transitions without a host launch per second."""
import torch
import time
from collections import deque


def replay(position, aggregate, opened, policy, prices, observed, membership,
           signals, values, ids, columns, age_limits, lengths, stateful,
           execution, transition, select_signals, progress=None, block=32, episodes=None):
    device=position.device
    total=prices.shape[0]-1
    index=torch.ones((),dtype=torch.int64,device=device)

    def transition_tick():
        # Device-side index stays dynamic across graph replays. Tail clocks
        # are clamped for safe reads and masked out of every state update.
        clock=index.clamp_max(total)
        take=lambda x,i:torch.index_select(x,0,i.reshape(1)).squeeze(0)
        held=position[...,0]>1e-12
        selected=select_signals(take(signals,clock-1),held,clock-1-opened,age_limits,stateful)
        swing=values[take(ids,clock)[:,None,:],columns[None,:,None]]
        p,a=transition(position,aggregate,take(prices,clock),take(prices,clock-1),
            take(observed,clock),take(membership,clock-1),selected,swing,policy,
            clock.to(torch.float64),(index==lengths-1)[:,None,None],
            execution.entry_dollars,execution.add_dollars,execution.cost_bps/10000.,execution.fee_per_share,execution.minimum_order_fee,execution.risk_protection,execution.maximum_stop_distance,execution.maximum_position_risk)
        active=index<lengths
        closed=held&(p[...,0]<=1e-12)&active[:,None,None]
        profits=p[...,7]-position[...,8]
        entry_clocks=opened.clone() if episodes is not None and episodes.trace else None
        opened.copy_(torch.where(active[:,None,None]&~held&(p[...,0]>1e-12),clock,opened))
        position.copy_(torch.where(active[:,None,None,None],p,position))
        aggregate.copy_(torch.where(active[:,None,None],a,aggregate))
        index.add_(1)
        return (closed,profits,entry_clocks,clock) if episodes is not None and episodes.trace else (closed,profits)

    # Keep scans out of the financial reduction region: Torch's split-scan
    # generator cannot emit fused reductions for production listing widths.
    # Inductor functionalizes ledger scatter mutations and copies the full bounded
    # buffers every tick. Capture direct in-place ledger kernels instead: measured
    # 32x faster ledger appends at 512x622, still no host launch per clock.
    compiled_transition=torch.compile(transition_tick,fullgraph=True,dynamic=False)
    append_ledger=episodes.append_events if episodes is not None else None
    def compiled():
        if episodes is not None and episodes.trace:closed,profits,entry_clocks,exit_clock=compiled_transition()
        else:closed,profits=compiled_transition()
        if append_ledger is not None:
            if episodes.trace:append_ledger(closed,profits,entry_clocks,exit_clock)
            else:append_ledger(closed,profits)
    def reset():
        position.zero_();aggregate.zero_();aggregate[...,8]=float('inf')
        opened.zero_();index.fill_(1)
        if episodes is not None:episodes.reset()

    torch.cuda.synchronize(device)
    stream=torch.cuda.Stream(device=device)
    stream.wait_stream(torch.cuda.current_stream(device))
    with torch.cuda.stream(stream),torch.inference_mode():
        compiled();compiled()
        reset()
        graph=torch.cuda.CUDAGraph()
        with torch.cuda.graph(graph,stream=stream):
            for _ in range(block):compiled()
        reset()
    torch.cuda.current_stream(device).wait_stream(stream)
    pending=deque();reported=0;completed=0;last_report=0.
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
    if progress is not None:publish_completed(force=True)
