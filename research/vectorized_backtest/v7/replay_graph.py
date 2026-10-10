"""Capture chronological GPU transitions without a host launch per second."""
import torch


def replay(position, aggregate, opened, policy, prices, observed, membership,
           signals, values, ids, columns, age_limits, lengths, stateful,
           execution, transition, select_signals, progress=None, block=32):
    device=position.device
    total=prices.shape[0]-1
    index=torch.ones((),dtype=torch.int64,device=device)

    def tick():
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
            execution.entry_dollars,execution.add_dollars,execution.cost_bps/10000.)
        active=index<lengths
        opened.copy_(torch.where(active[:,None,None]&~held&(p[...,0]>1e-12),clock,opened))
        position.copy_(torch.where(active[:,None,None,None],p,position))
        aggregate.copy_(torch.where(active[:,None,None],a,aggregate))
        index.add_(1)

    compiled=torch.compile(tick,fullgraph=True,dynamic=False)
    def reset():
        position.zero_();aggregate.zero_();aggregate[...,8]=float('inf')
        opened.zero_();index.fill_(1)

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
    for begin in range(0,total,block):
        graph.replay()
        done=min(total,begin+block)
        if progress is not None and (done%256==0 or done==total):progress(done,total)
    torch.cuda.synchronize(device)
