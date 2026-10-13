"""Bounded dependency execution; workers never speculate past a failed parent.

Handlers own persistence/readback. A certified receipt is reused only after
the caller verifies its immutable inputs and outputs. No local data ledger.
"""
from __future__ import annotations

from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from dataclasses import dataclass
from threading import Event
from collections.abc import Callable, Mapping
from collections import deque,Counter
from heapq import heapify,heappush,heappop


@dataclass(frozen=True)
class Unit:
    key: str
    product: str
    parents: tuple[str, ...] = ()
    memory_bytes: int = 0

    def __post_init__(self):
        if not self.key or not self.product or len(set(self.parents)) != len(self.parents):
            raise ValueError("Invalid dependency unit")
        if type(self.memory_bytes) is not int or self.memory_bytes < 0:
            raise ValueError("Invalid unit memory reservation")


def validate(units: tuple[Unit, ...], memory_bytes: int):
    by_key={u.key:u for u in units}
    if len(by_key)!=len(units) or memory_bytes<=0:
        raise ValueError("Duplicate unit keys or invalid global budget")
    for u in units:
        if u.memory_bytes>memory_bytes or any(p not in by_key for p in u.parents):
            raise ValueError("Missing prerequisite or unit exceeds memory budget")
    children={k:[] for k in by_key};remaining={k:len(u.parents) for k,u in by_key.items()}
    for u in units:
        for parent in u.parents:children[parent].append(u.key)
    ready=deque(k for k,n in remaining.items() if not n);seen=0
    while ready:
        key=ready.popleft();seen+=1
        for child in children[key]:
            remaining[child]-=1
            if remaining[child]==0:ready.append(child)
    if seen!=len(units):raise ValueError('Execution dependency cycle')
    return by_key


def execute(units: tuple[Unit, ...], handlers: Mapping[str, Callable], *, workers=4,
            memory_bytes=16*1024**3, reuse: Callable | None=None,
            progress: Callable | None=None, stop: Event | None=None):
    """Drain failures, report blocked descendants and preserve verified results."""
    if type(workers) is not int or not 1<=workers<=32:
        raise ValueError("Invalid worker limit")
    by_key=validate(units,memory_bytes)
    if any(u.product not in handlers for u in units):
        raise ValueError("Required producer is unavailable; execution cannot start")
    stop=stop if stop is not None else Event()
    notify=progress or (lambda event:None)
    state={k:'queued' for k in by_key};results={};errors={};active={};reserved=0
    counts=Counter(queued=len(by_key));children={k:[] for k in by_key}
    remaining={k:len(u.parents) for k,u in by_key.items()}
    for u in units:
        for parent in u.parents:children[parent].append(u.key)
    order={key:index for index,key in enumerate(by_key)}
    ready=[(by_key[k].memory_bytes,order[k],k) for k,n in remaining.items() if not n];heapify(ready)
    bad={'failed','blocked','interrupted'}
    def report(key,status,reason=''):
        counts[state[key]]-=1;counts[status]+=1
        state[key]=status
        notify(dict(unit=key,product=by_key[key].product,status=status,reason=reason,
                    output_hash=results[key].get('output_hash') if isinstance(results.get(key),dict) else None,
                    counts={s:counts[s] for s in
                            ('queued','active','certified','reused','blocked','failed','interrupted')}))
    def finish(key,status,reason=''):
        # Iterative propagation also handles very long failed/reused chains.
        completed=deque([(key,status,reason)])
        while completed:
            key,status,reason=completed.popleft();report(key,status,reason)
            for child in children[key]:
                remaining[child]-=1
                if remaining[child]==0 and state[child]=='queued':
                    if any(state[p] in bad for p in by_key[child].parents):
                        completed.append((child,'blocked','Required parent did not certify'))
                    else:heappush(ready,(by_key[child].memory_bytes,order[child],child))
    with ThreadPoolExecutor(max_workers=workers,thread_name_prefix='certified-research') as pool:
        try:
            while counts['queued'] or counts['active']:
                if stop.is_set() and counts['queued']:
                    for key in by_key:
                        if state[key]=='queued':report(key,'interrupted','Operator stop requested')
                    ready.clear()
                # The smallest reservation establishes whether any ready unit
                # fits; no scan through millions of memory-blocked roots.
                while ready and len(active)<workers:
                    if ready[0][0]>memory_bytes-reserved:break
                    _,_,key=heappop(ready);u=by_key[key]
                    if state[key]!='queued':continue
                    parents={p:results[p] for p in u.parents}
                    try:
                        old=reuse(u,parents) if reuse is not None else None
                    except Exception as error:
                        errors[key]=error;finish(key,'failed','Existing receipt verification failed');continue
                    if old is not None:
                        results[key]=old;finish(key,'reused');continue
                    report(key,'active')
                    active[pool.submit(handlers[u.product],u,parents,stop)]=u
                    reserved+=u.memory_bytes
                if not active:continue
                done,_=wait(active,timeout=.25,return_when=FIRST_COMPLETED)
                for future in done:
                    u=active.pop(future);reserved-=u.memory_bytes
                    try:
                        value=future.result()
                        if value is None:raise ValueError('Producer returned no verified receipt')
                        results[u.key]=value;finish(u.key,'certified')
                    except Exception as error:
                        errors[u.key]=error;finish(u.key,'failed','Producer failed; inspect bounded error receipt')
        except BaseException:
            stop.set()
            for future in active:future.cancel()
            # Exiting the pool waits for running handlers to reconcile owned writes.
            raise
    return dict(status='failed' if errors else 'interrupted' if stop.is_set() else 'complete',
                units=state,results=results,errors=errors)
