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


def financial_tick(index,total,position,orders,book,aggregate,opened,policy,prices,
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
    snapshot=take(capacity,clock)
    if position.device.type=='cuda':
        from .order_step import step
        a,closed,profits,entries,complete,record=step(position,orders,book,opened,ticker_state,aggregate,
            selected,ratios,policy,take(prices,(clock-1).clamp_min(0)),take(prices,clock),member,swing,
            take(bids,clock),take(asks,clock),take(valid,clock),snapshot,elapsed,terminal,active,
            execution.entry_dollars,execution.add_dollars,execution.fee_per_share,execution.minimum_order_fee,
            execution.cost_bps/10000.,execution.minimum_order_dollars)
        if tail:a[...,7]=aggregate[...,7]
        aggregate.copy_(torch.where(active,a,aggregate));index.add_(1)
        return closed,profits,entries,elapsed,complete,record
    newer=snapshot[...,2]>book[...,0]
    ask_remaining=torch.where(newer,snapshot[...,0],book[...,1]).clamp_min(0)
    bid_remaining=torch.where(newer,snapshot[...,1],book[...,2]).clamp_min(0)
    available=torch.stack((ask_remaining,bid_remaining),-1)
    p,o,complete,record=transition(position,orders,signals=selected,ratios=ratios,policy=policy,
        mark=take(prices,clock),previous=take(prices,(clock-1).clamp_min(0)),membership=member,
        observed=take(observed,clock),swing=swing,bid=take(bids,clock),ask=take(asks,clock),
        quote_valid=take(valid,clock),capacity=available,clock=elapsed,terminal=terminal,
        entry_dollars=execution.entry_dollars,add_dollars=execution.add_dollars,
        fee_per_share=execution.fee_per_share,minimum_order_fee=execution.minimum_order_fee,
        cost=execution.cost_bps/10000.,minimum_order_dollars=execution.minimum_order_dollars)
    a=account(position,p,orders,o,aggregate,take(prices,clock),complete,record)
    if tail:a[...,7]=aggregate[...,7]  # Inactivity describes only the decision window.
    update_equity(ticker_state,p,take(prices,clock),active)
    closed=held&(p[...,0]<=1.e-12)&active
    profits=p[...,7]-position[...,8];entries=opened.clone()
    opened.copy_(torch.where(active&~held&(p[...,0]>1.e-12),elapsed,opened))
    delta=p[...,0]-position[...,0]
    next_book=torch.stack((torch.where(newer,snapshot[...,2],book[...,0]),
        (ask_remaining-delta.clamp_min(0)).clamp_min(0),(bid_remaining-(-delta).clamp_min(0)).clamp_min(0)),-1)
    book.copy_(torch.where(active,next_book,book))
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
           capacity,execution,episodes,provide,progress,block,gate,backend,record_capacity=None):
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
    book=torch.zeros((*position.shape[:-1],3),dtype=position.dtype,device=device)
    episodes.streaming=True
    journal=None
    if hasattr(item,'execution_order_root'):
        from .order_journal import Journal
        from dataclasses import asdict
        journal=Journal(item.execution_order_root,b,dict(data=item.identity,execution=asdict(execution)))
    journal_capacity=max(8192,block*u) if record_capacity is None else record_capacity
    if journal_capacity<block*u:raise ValueError('Journal must hold at least one graph of completed orders')
    records=OrderRecords(b,u,device,capacity=journal_capacity,sink=journal)
    ratios=plan.ratios
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
        offset=torch.tensor(offset,device=device,dtype=torch.int64)
        args=(index,total,position,orders,book,aggregate,opened,policy,p,ob,mem,signals,
              values,ids,columns,limits,ratios,ticker_state,bb,aa,vv,cap,execution,tail_mode,offset)
        fn=program(args) if use_cuda else financial_tick
        def tick():
            if not tail_mode:provide(index.clamp_max(total)-1,position,opened)
            closed,profits,entries,clock,complete,record=fn(*args)
            episodes.append_events(closed,profits,entries,clock)
            records.append(complete,record)
        if use_cuda:
            # Warmup/capture are stateful: snapshot every mutable replay buffer,
            # including route ownership and ledger counts, then restore exactly.
            buffers=[position,orders,book,aggregate,opened,ticker_state,records.counts,episodes.counts]
            if not tail_mode and hasattr(plan,'branch_index'):buffers.extend([plan.owner,policy,limits,columns])
            buffers.extend([ratios,plan.feedback])
            saved=[x.clone() for x in buffers]
            def restore():
                for target,value in zip(buffers,saved):target.copy_(value)
                index.fill_(1)
            stream=torch.cuda.Stream(device=device);stream.wait_stream(torch.cuda.current_stream(device))
            with torch.cuda.stream(stream),torch.inference_mode():
                clock=time.perf_counter();tick();tick();stream.synchronize()
                profile['compiler_warmup_seconds']=profile.get('compiler_warmup_seconds',0.)+time.perf_counter()-clock
                restore();graph=torch.cuda.CUDAGraph();clock=time.perf_counter()
                from .capture_lifecycle import defer_cyclic_collection
                with defer_cyclic_collection(),torch.cuda.graph(graph,stream=stream):
                    for _ in range(block):tick()
                restore()
                profile['graph_capture_seconds']=profile.get('graph_capture_seconds',0.)+time.perf_counter()-clock
            torch.cuda.current_stream(device).wait_stream(stream)
        chunk=block if tail_mode else max(block,(512//block)*block)
        def execute(count):
            # Checkpoint only live state/counters, not the large append-only
            # ledgers. If a journal page fills, replay smaller chunks exactly.
            mutable=[position,orders,book,aggregate,opened,ticker_state,index,episodes.counts,records.counts]
            if not tail_mode and hasattr(plan,'branch_index'):mutable.extend([plan.owner,policy,limits,columns])
            with gate.turn() if gate is not None else nullcontext():
                checkpoint=[x.clone() for x in mutable]
                for _ in range(count):
                    if use_cuda:graph.replay()
                    else:tick()
                if device.type=='cuda':torch.cuda.current_stream(device).synchronize()
            if episodes.fits() and records.drain():
                if not episodes.drain():raise RuntimeError('Position page changed after readiness check')
                return
            for target,value in zip(mutable,checkpoint):target.copy_(value)
            if count==1:raise RuntimeError('One graph exceeded its proven order-journal event bound')
            first=count//2;execute(first);execute(count-first)
        profiled=False
        for begin in range(0,total,chunk):
            count=len(range(begin,min(total,begin+chunk),block if use_cuda else 1))
            if use_cuda and not tail_mode and hasattr(item,'execution_diagnostic_root') and not profiled and begin>=total//2:
                from research.vectorized_backtest.v42.publication import write_json
                sampled=time.perf_counter()
                with torch.profiler.profile(activities=[torch.profiler.ProfilerActivity.CPU,torch.profiler.ProfilerActivity.CUDA]) as trace:
                    execute(count)
                kernels={}
                for event in trace.events():
                    if 'CUDA' not in str(event.device_type):continue
                    row=kernels.setdefault(event.name,dict(calls=0,microseconds=0.))
                    row['calls']+=1;row['microseconds']+=event.device_time_total
                write_json(item.execution_diagnostic_root/'gpu-kernels.json',dict(begin_clock=begin,clocks=count*block,kernels=kernels))
                profile['instrumented_chunk_wall_seconds']=time.perf_counter()-sampled;profiled=True
            else:execute(count)
            done=min(total,begin+chunk)
            if progress:progress(done,total)
            if tail_mode and not bool(((position[...,0]>1.e-12)|(orders[...,0]!=0)).any()):break
        # Recursive execute owns a cell referencing itself and the CUDA graph.
        # Break that cycle at the page boundary rather than waiting for GC.
        del execute

    try:
        item.execution_phase='Replay positions'
        run_page((prices,observed,membership,bids,asks,quote_valid,capacity))
        main_seconds=time.perf_counter()-started
        # Closed, idle candidates cannot change during liquidation. Compact the
        # candidate axis only: listing reductions and chronological prices retain
        # their exact original order. Use distinct inactive padding rows to keep
        # compiled layouts bounded to multiples of 32 without duplicate fills.
        pending=((position[...,0]>1.e-12)|(orders[...,0]!=0)).any(-1)[0]
        active=pending.nonzero().flatten()
        width=min(b,((len(active)+31)//32)*32)
        rows=torch.cat((active,(~pending).nonzero().flatten()[:width-len(active)]))
        full=(position,orders,aggregate,opened,ticker_state,episodes,records)
        full_book=book
        compacted=0<len(rows)<b
        if compacted:
            from .episodes import Episodes
            position=position.index_select(1,rows);orders=orders.index_select(1,rows)
            book=book.index_select(1,rows)
            aggregate=aggregate.index_select(1,rows);opened=opened.index_select(1,rows)
            ticker_state=ticker_state.index_select(1,rows)
            policy=policy.index_select(1,rows);limits=limits.index_select(1,rows)
            ratios=ratios.index_select(1,rows);signals=signals.index_select(1,rows)
            episodes=Episodes(1,len(rows),full[5].capacity,device,max_listings=u,trace=full[5].trace)
            episodes.owner=full[5].owner.index_select(1,rows)
            episodes.streaming=True;episodes.mapping=rows.cpu().numpy()
            records=OrderRecords(len(rows),u,device,capacity=journal_capacity,sink=journal)
            records.mapping=rows.cpu().numpy()
        profile['tail_active_candidates']=len(active)
        profile['tail_replay_candidates']=len(rows)
        def diagnostic(label):
            if not hasattr(item,'execution_diagnostic_root'):return
            from research.vectorized_backtest.v42.publication import write_json
            mask=(position[...,0]>1.e-12)|(orders[...,0]!=0)
            write_json(item.execution_diagnostic_root/(label+'.json'),dict(
                elapsed_seconds=time.perf_counter()-started,
                candidate_indices=rows.cpu().tolist() if compacted else list(range(b)),
                pending_indexes=mask.nonzero().cpu().tolist(),
                positions=position[mask].cpu().tolist(),orders=orders[mask].cpu().tolist(),
                quote_books=book[mask].cpu().tolist(),
                nonfinite_position_fields=int((~torch.isfinite(position)).sum()),
                nonfinite_order_fields=int((~torch.isfinite(orders)).sum())))
        diagnostic('decision-cutoff')
        for page in range(len(tail.receipt['pages'])):
            if not bool(((position[...,0]>1.e-12)|(orders[...,0]!=0)).any()):break
            host=future.result()
            item.execution_phase='Complete outstanding orders and liquidation'
            future=pool.submit(tail.load,page+1) if page+1<len(tail.receipt['pages']) else None
            def tensor(name,fill=0.):
                value=torch.from_numpy(host[name]).to(device)
                value=pad(value,(0,u-len(item.listing_ids)),value=fill)
                return torch.cat((value[:1],value),0)[:,None,None]
            p=tensor('mark',float('nan'));bb=tensor('bid');aa=tensor('ask')
            vv=tensor('quote_valid',False)
            cap=torch.stack((tensor('ask_size'),tensor('bid_size'),tensor('quote_us')),-1)
            ob=torch.isfinite(p)&(p>0);mem=torch.zeros_like(vv)
            offset=int(host['clocks'][0])-int(item.arrays['clocks'][0])-1
            run_page((p,ob,mem,bb,aa,vv,cap),True,offset)
            diagnostic('tail-page-'+str(page))
        require_complete(position,orders)
        if compacted:
            full_book.index_copy_(1,rows,book)
            for original,current in zip(full[:5],(position,orders,aggregate,opened,ticker_state)):
                original.index_copy_(1,rows,current)
            full[5].absorb(episodes);full[6].absorb(records)
            position,orders,aggregate,opened,ticker_state,episodes,records=full
        result=records.export()
        profile.update(chronological_replay_seconds=time.perf_counter()-started,
                       decision_window_seconds=main_seconds,execution_tail_seconds=time.perf_counter()-started-main_seconds)
        if journal is not None:profile['order_publication_backpressure_seconds']=journal.wait_seconds
        plan.last_replay_profile=profile
        # Each metric remains indexed by candidate, matching result-cache slicing.
        # Row schema: listing,kind,requested qty,filled qty,notional,fees,
        # submitted clock,first fill clock,completion clock.
        return dict(execution_orders=result)
    finally:
        pool.shutdown(wait=True,cancel_futures=True)
        if journal is not None:journal.close()
        if hasattr(plan,'branch_index'):del plan.pending_orders
