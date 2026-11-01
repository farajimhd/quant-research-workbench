"""Bounded GPU inference / native CPU execution pipeline for five-slot requests."""
import time
from concurrent.futures import ThreadPoolExecutor
import numpy as np
import torch
from .compact_execution import advance,state


def replay(item,members,plan,execution,progress=None):
    from .compact_inference import Inference
    from .order_tail import Tail
    from numba import set_num_threads
    began=time.perf_counter();b=len(members);u=len(item.listing_ids);total=item.clocks
    engine=Inference(plan,item)
    buffers=state(b,u)
    ledger=[[] for _ in range(b)];episodes=[[] for _ in range(b)]
    journal=None
    if hasattr(item,'execution_order_root'):
        from dataclasses import asdict
        from .order_journal import Journal
        journal=Journal(item.execution_order_root,b,dict(data=item.identity,execution=asdict(execution)))
    profile=dict(inference_position_feedback=False,inference_network_recurrence=False,
                 inference_listing_slots=5,model_base_features=len(engine.base.columns),
                 price_reference_count=len(engine.prices.names),inference_time_batch=engine.width,
                 preparation_seconds=time.perf_counter()-began,inference_seconds=0.,execution_seconds=0.,
                 transfer_seconds=0.,execution_wait_seconds=0.,inference_execution_overlap=True)
    quotes=np.stack([item.host_tensors[k].numpy() for k in
                     ('mark','bid','ask','quote_us','bid_size','ask_size','quote_valid')],-1)
    tail=Tail(item.execution_tail,item)
    # Finite execution-only pages already exist; never acquire or extend them.
    pool=ThreadPoolExecutor(max_workers=2,thread_name_prefix='v63-execution')
    tail_future=pool.submit(tail.load,0) if tail.receipt['pages'] else None
    future=None
    def execute(start,slots,packet,book):
        set_num_threads(4)
        started=time.perf_counter()
        result=advance(start,slots,*packet,book,*buffers,execution.entry_dollars,execution.add_dollars,
                       execution.fee_per_share,execution.minimum_order_fee,execution.cost_bps/10000.,total-1)
        return result,time.perf_counter()-started
    def collect():
        nonlocal future
        if future is None:return
        start=time.perf_counter();(records,closed),elapsed=future.result()
        profile['execution_wait_seconds']+=time.perf_counter()-start;profile['execution_seconds']+=elapsed
        if journal is not None:
            sizes=np.asarray([len(row) for row in records],np.int64)
            if sizes.sum():
                packed=np.concatenate([np.asarray(row,np.float64).reshape(-1,9) for row in records])
                journal.submit(np.arange(b),sizes,packed)
        for i in range(b):
            if journal is None:ledger[i].extend(records[i])
            episodes[i].extend(closed[i])
        future=None
    try:
        with torch.inference_mode():
            for begin in range(0,total,engine.width):
                end=min(total,begin+engine.width)
                indices=torch.arange(begin,end,device=item.device)
                start=time.perf_counter();packet=engine(indices)
                torch.cuda.synchronize(item.device)
                profile['inference_seconds']+=time.perf_counter()-start
                start=time.perf_counter();host=tuple(v.cpu().numpy() for v in packet)
                profile['transfer_seconds']+=time.perf_counter()-start
                collect()
                future=pool.submit(execute,begin,engine.slots_cpu[begin:end],host,quotes[begin:end])
                if progress:progress(begin,total)
        collect()
        profile['decision_seconds']=time.perf_counter()-began
        for page in range(len(tail.receipt['pages'])):
            if not np.any((buffers[0][:,:,0]>1e-12)|(buffers[1][:,:,0]!=0)):break
            book=tail_future.result()
            tail_future=pool.submit(tail.load,page+1) if page+1<len(tail.receipt['pages']) else None
            packed=np.stack([book[k] for k in ('mark','bid','ask','quote_us','bid_size','ask_size','quote_valid')],-1)
            start_clock=int(book['clocks'][0]-item.arrays['clocks'][0])
            for begin in range(0,len(packed),engine.width):
                size=min(engine.width,len(packed)-begin)
                slots=np.full((size,5),-1,np.int32)
                packet=(np.zeros((size,b,5),np.int16),np.zeros((size,b,5,3)),np.zeros((size,b,5,5)),
                        np.zeros((size,b,5),np.int64),np.zeros((size,b,5),bool))
                future=pool.submit(execute,start_clock+begin,slots,packet,packed[begin:begin+size]);collect()
                if not np.any((buffers[0][:,:,0]>1e-12)|(buffers[1][:,:,0]!=0)):break
        p,o,book,active,lookup,counts,stats,diag=buffers
        if not np.isfinite(p).all() or not np.isfinite(o).all():raise RuntimeError('Nonfinite execution state')
        if np.any((p[:,:,0]>1e-12)|(o[:,:,0]!=0)):
            raise RuntimeError('Existing quote coverage exhausted with outstanding positions/orders; no score published')
        metrics={}
        if journal is not None:
            ledger=journal.finish()
            profile['order_publication_backpressure_seconds']=journal.wait_seconds
        names={0:'net_pnl',1:'drawdown',2:'capital_dollar_seconds',3:'stop_risk_dollar_seconds',
               4:'filled_batches',5:'add_count',6:'reduce_count',8:'worst_position_pnl',9:'closed_positions',
               10:'winning_positions',11:'turnover',12:'maximum_position_risk',13:'maximum_stop_distance'}
        for column,name in names.items():metrics[name]=stats[:,column].tolist()
        metrics['net_pnl']=p[:,:,4].sum(1).tolist()
        metrics.update(terminal_valid=[True]*b,unliquidated_positions=[0]*b,inactivity_fraction=(stats[:,14]/np.maximum(1,stats[:,15])).tolist(),
                       risk_rejected_entries=[0]*b,risk_rejected_adds=[0]*b,ticker_drawdown=p[:,:,10].tolist(),
                       open_episode_listings=[[] for _ in range(b)],open_episode_realized=[[] for _ in range(b)],
                       ticker_blocked_requests=p[:,:,13].tolist(),execution_orders=ledger,cancelled_orders=diag[:,11].tolist(),blocked_requests=diag[:,1].tolist())
        for column,name in enumerate(('episode_profits','episode_listings','episode_branch','episode_entry_clocks','episode_exit_clocks')):
            metrics[name]=[[v[column] if column==0 else int(v[column]) for v in row] for row in episodes]
        for column,name in enumerate(('requests','blocked_requests','blocked_price','blocked_stop','blocked_limit','blocked_quote',
                                    'blocked_liquidity','blocked_inventory','partial_fills','rank_loss_exits','rank_exit_wait_seconds','cancellations','price_abstentions')):
            metrics['admission_'+name]=diag[:,column].tolist()
        profile.update(total_replay_seconds=time.perf_counter()-began,
                       average_execution_tickers_per_candidate=float(stats[:,17].sum()/max(1,stats[:,15].sum())),
                       full_union_tickers=u)
        plan.last_replay_profile=profile
        if progress:progress(total,total)
        return [metrics]
    finally:
        pool.shutdown(wait=True,cancel_futures=True)
        if journal is not None:journal.close()
