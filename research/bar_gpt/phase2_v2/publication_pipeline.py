"""Bounded parallel preparation, with strictly ordered publication consumption."""
from collections import deque
from concurrent.futures import ThreadPoolExecutor
import threading
import torch
from .data import unit_readers,plan_block
from .tokenizer import SessionTokens
from .device_pipeline import record_tensors


def publication_batches(units,split,teacher,teacher_sha,args,*,cursor=(0,0,0),
                        report=None,reconstruction=False,session_type=SessionTokens):
    workers=args.prepare_workers
    if not 1<=workers<=8:raise ValueError('Publication workers must be in [1,8]')
    cuda=torch.device(args.device).type=='cuda'
    if cuda and torch.cuda.mem_get_info(args.device)[0] < ((workers+1)*args.cache_mib+1024)*1024**2:
        raise MemoryError('Insufficient aggregate publication-cache headroom')
    # Reader verification reserves2x payload for mmap plus contiguous copies.
    # Include queued/active prepared publications in that SAME aggregate budget.
    io_workers=getattr(args,'io_workers',0)
    read_budget=args.prefetch_mib*(io_workers+1)//(io_workers+workers+1)
    if read_budget<1:raise ValueError('Publication prefetch budget is too small')
    inputs=unit_readers(units,split,cursor[0],workers=io_workers,prefetch_mib=read_budget)
    pool=ThreadPoolExecutor(max_workers=workers,thread_name_prefix='phase2-publication')
    pending=deque();local=threading.local();streams=[];stream_lock=threading.Lock()

    def prepare(row):
        ui,unit,reader=row;plans=[];session=None
        if cuda:
            if not hasattr(local,'stream'):
                local.stream=torch.cuda.Stream(device=args.device)
                with stream_lock:streams.append(local.stream)
            context=torch.cuda.stream(local.stream)
        else:
            from contextlib import nullcontext
            context=nullcontext()
        with context,torch.no_grad():
            for bi,(ordinal,s,liquid,selection) in enumerate(unit.blocks):
                if s!=split or (ui==cursor[0] and bi<cursor[1]):continue
                plan=plan_block(reader,ordinal)
                begin=cursor[2] if (ui,bi)==cursor[:2] else 0
                if not 0<=begin<=len(plan.origins):raise ValueError('Resume origin cursor out of bounds')
                plans.append((bi,plan,begin,liquid,selection))
                if begin==len(plan.origins):continue
                if session is None:
                    session=session_type(reader,teacher,teacher_sha,device=args.device,
                        precision=args.precision,memory_mib=args.cache_mib,token_batch=args.token_batch)
                session.prepare(plan)
            ready=None
            if cuda:ready=torch.cuda.Event();ready.record(local.stream)
        return ui,unit,session,plans,ready

    def submit():
        row=next(inputs,None)
        if row is not None:pending.append(pool.submit(prepare,row))

    try:
        for _ in range(workers):submit()
        while pending:
            ui,unit,session,plans,ready=pending.popleft().result()
            submit()
            if cuda:
                stream=torch.cuda.current_stream(args.device);stream.wait_event(ready)
                if session is not None:
                    record_tensors([session.pools,session.token_bank,session.reference_bank,
                                    session.clock_bank,session.time_cache],stream)
            for bi,plan,begin,liquid,selection in plans:
                if report:
                    report(dict(root=str(unit.root),session=str(unit.path),split=split,
                                liquid=liquid,selection=selection,**plan.report))
                for start in range(begin,len(plan.origins),args.batch_size):
                    stop=min(start+args.batch_size,len(plan.origins))
                    token,meta,target=session.batch(plan,start,stop)
                    row=(token,meta,target,(ui,bi,stop),plan.origins[start:stop],liquid)
                    yield row+(session.reconstruction(plan,start,stop),) if reconstruction else row
            del session
    finally:
        for future in pending:future.cancel()
        pool.shutdown(wait=True,cancel_futures=True)
        try:
            for stream in streams:stream.synchronize()
            # Even unconsumed failures must remain visible at STOP/early close.
            for future in pending:
                if not future.cancelled() and future.exception() is not None:raise future.exception()
        finally:inputs.close()
