"""Persistent execution transitions shared by eager and captured replay paths."""
import torch
import time
from collections import OrderedDict
from concurrent.futures import ThreadPoolExecutor
from contextlib import nullcontext
from .orders import transition
from .order_accounting import account
from .evaluator import state_signals
from .ticker_selection import update_equity


def financial_tick(index,total,position,orders,aggregate,opened,policy,prices,
                   observed,membership,signals,values,ids,columns,limits,ratios,
                   ticker_state,bids,asks,valid,capacity,execution,tail=False,offset=0):
    """One causal clock; graph padding cannot submit orders or change accounts."""
    clock=index.clamp_max(total)
    take=lambda x,i:torch.index_select(x,0,i.reshape(1)).squeeze(0)
    active=index<=total
    held=position[...,0]>1.e-12
    if tail:
        selected=torch.zeros_like(signals);swing=torch.zeros_like(position[...,0])
        terminal=torch.ones((),device=index.device,dtype=torch.bool)
        member=torch.zeros_like(held)
    else:
        selected=state_signals(signals,held,clock-1-opened,limits,False)
        swing=values[take(ids,clock)[:,None,:],columns[None] if columns.ndim==2 else columns[None,:,None]]
        terminal=clock==total;member=take(membership,clock-1)
    elapsed=(clock+offset).to(torch.float64)
    p,o,complete,record=transition(position,orders,signals=selected,ratios=ratios,policy=policy,
        mark=take(prices,clock),previous=take(prices,(clock-1).clamp_min(0)),membership=member,
        observed=take(observed,clock),swing=swing,bid=take(bids,clock),ask=take(asks,clock),
        quote_valid=take(valid,clock),capacity=take(capacity,clock),clock=elapsed,terminal=terminal,
        entry_dollars=execution.entry_dollars,add_dollars=execution.add_dollars,
        fee_per_share=execution.fee_per_share,minimum_order_fee=execution.minimum_order_fee,
        cost=execution.cost_bps/10000.,minimum_order_dollars=execution.minimum_order_dollars)
    a=account(position,p,orders,o,aggregate,take(prices,clock),complete,record)
    update_equity(ticker_state,p,take(prices,clock),active)
    closed=held&(p[...,0]<=1.e-12)&active
    profits=p[...,7]-position[...,8];entries=opened.clone()
    opened.copy_(torch.where(active&~held&(p[...,0]>1.e-12),elapsed,opened))
    position.copy_(torch.where(active,p,position))
    orders.copy_(torch.where(active,o,orders))
    aggregate.copy_(torch.where(active,a,aggregate))
    index.add_(1)
    return closed,profits,entries,elapsed,complete&active,record


_PROGRAMS=OrderedDict()


def program(arguments):
    from research.vectorized_backtest.v6.torch_backtest.compile_lifecycle import scoped_compile
    def key(x):
        return (tuple(x.shape),x.stride(),x.dtype,x.device) if isinstance(x,torch.Tensor) else x
    identity=tuple(key(x) for x in arguments)
    if identity not in _PROGRAMS:
        if len(_PROGRAMS)>=16:_PROGRAMS.popitem(last=False)
        _PROGRAMS[identity]=scoped_compile(financial_tick,fullgraph=True,dynamic=False)
    _PROGRAMS.move_to_end(identity)
    return _PROGRAMS[identity]


def replay(*,item,plan,position,aggregate,opened,policy,prices,observed,membership,
           signals,values,ids,columns,limits,ticker_state,bids,asks,quote_valid,
           capacity,execution,episodes,provide,progress,block,gate,backend):
    """Replay decisions once, then drain orders using execution-only liquidity.

    The next bounded host page is prefetched while GPU clocks execute. No tail
    prices enter the neural feature bank. A missing tail blocks publication.
    """
    from .orders import ORDER_WIDTH,require_complete
    from .order_records import OrderRecords
    from .order_tail import Tail
    from torch.nn.functional import pad
    device=position.device;u=position.shape[-2];b=position.shape[1]
    orders=torch.zeros((*position.shape[:-1],ORDER_WIDTH),dtype=position.dtype,device=device)
    records=OrderRecords(b,u,device)
    if hasattr(plan,'branch_index'):plan.pending_orders=orders
    tail=Tail(item.execution_tail,item)
    profile={};started=time.perf_counter()
    pool=ThreadPoolExecutor(max_workers=1,thread_name_prefix='execution-tail')
    future=pool.submit(tail.load,0) if tail.receipt['pages'] else None
    use_cuda=backend=='compile' and device.type=='cuda'

    def run_page(arrays,tail_mode=False,offset=0):
        nonlocal profile
        p,ob,mem,bb,aa,vv,cap=arrays;total=len(p)-1
        index=torch.ones((),device=device,dtype=torch.int64)
        args=(index,total,position,orders,aggregate,opened,policy,p,ob,mem,signals,
              values,ids,columns,limits,plan.ratios,ticker_state,bb,aa,vv,cap,execution,tail_mode,offset)
        fn=program(args) if use_cuda else financial_tick
        def tick():
            if not tail_mode:provide(index.clamp_max(total)-1,position,opened)
            closed,profits,entries,clock,complete,record=fn(*args)
            episodes.append_events(closed,profits,entries,clock)
            records.append(complete,record)
        if use_cuda:
            # Warmup/capture are stateful: snapshot every mutable replay buffer,
            # including route ownership and ledger counts, then restore exactly.
            buffers=[position,orders,aggregate,opened,ticker_state,records.counts,episodes.counts]
            if hasattr(plan,'branch_index'):buffers.extend([plan.owner,policy,limits,columns])
            buffers.extend([plan.ratios,plan.feedback])
            saved=[x.clone() for x in buffers]
            def restore():
                for target,value in zip(buffers,saved):target.copy_(value)
                index.fill_(1)
            stream=torch.cuda.Stream(device=device);stream.wait_stream(torch.cuda.current_stream(device))
            with torch.cuda.stream(stream),torch.inference_mode():
                clock=time.perf_counter();tick();tick();stream.synchronize()
                profile['compiler_warmup_seconds']=profile.get('compiler_warmup_seconds',0.)+time.perf_counter()-clock
                restore();graph=torch.cuda.CUDAGraph();clock=time.perf_counter()
                with torch.cuda.graph(graph,stream=stream):
                    for _ in range(block):tick()
                restore()
                profile['graph_capture_seconds']=profile.get('graph_capture_seconds',0.)+time.perf_counter()-clock
            torch.cuda.current_stream(device).wait_stream(stream)
        chunk=max(block,(512//block)*block)
        for begin in range(0,total,chunk):
            with gate.turn() if gate is not None else nullcontext():
                for _ in range(begin,min(total,begin+chunk),block if use_cuda else 1):
                    if use_cuda:graph.replay()
                    else:tick()
                if device.type=='cuda':torch.cuda.current_stream(device).synchronize()
            done=min(total,begin+chunk)
            if progress:progress(done,total)
            if tail_mode and not bool(((position[...,0]>1.e-12)|(orders[...,0]!=0)).any()):break

    try:
        run_page((prices,observed,membership,bids,asks,quote_valid,capacity))
        main_seconds=time.perf_counter()-started
        for page in range(len(tail.receipt['pages'])):
            if not bool(((position[...,0]>1.e-12)|(orders[...,0]!=0)).any()):break
            host=future.result()
            future=pool.submit(tail.load,page+1) if page+1<len(tail.receipt['pages']) else None
            def tensor(name,fill=0.):
                value=torch.from_numpy(host[name]).to(device)
                value=pad(value,(0,u-len(item.listing_ids)),value=fill)
                return torch.cat((value[:1],value),0)[:,None,None]
            p=tensor('mark',float('nan'));bb=tensor('bid');aa=tensor('ask')
            vv=tensor('quote_valid',False);cap=tensor('capacity')
            ob=torch.isfinite(p)&(p>0);mem=torch.zeros_like(vv)
            offset=int(host['clocks'][0])-int(item.arrays['clocks'][0])-1
            run_page((p,ob,mem,bb,aa,vv,cap),True,offset)
        require_complete(position,orders)
        result=records.export()
        profile.update(chronological_replay_seconds=time.perf_counter()-started,
                       decision_window_seconds=main_seconds,execution_tail_seconds=time.perf_counter()-started-main_seconds)
        plan.last_replay_profile=profile
        # Each metric remains indexed by candidate, matching result-cache slicing.
        # Row schema: listing,kind,requested qty,filled qty,notional,fees,
        # submitted clock,first fill clock,completion clock.
        return dict(execution_orders=result)
    finally:
        pool.shutdown(wait=True,cancel_futures=True)
        if hasattr(plan,'branch_index'):del plan.pending_orders
