"""Bounded dependency execution; workers never speculate past a failed parent.

Handlers own persistence/readback. A certified receipt is reused only after
the caller verifies its immutable inputs and outputs. No local data ledger.
"""
from __future__ import annotations

from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from dataclasses import dataclass
from threading import Event
from collections.abc import Callable, Mapping


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
    pending=set(by_key);seen=set()
    while pending:
        ready={k for k in pending if set(by_key[k].parents)<=seen}
        if not ready:raise ValueError("Execution dependency cycle")
        pending-=ready;seen|=ready
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
    def report(key,status,reason=''):
        state[key]=status
        notify(dict(unit=key,product=by_key[key].product,status=status,reason=reason,
                    counts={s:list(state.values()).count(s) for s in
                            ('queued','active','certified','reused','blocked','failed','interrupted')}))
    with ThreadPoolExecutor(max_workers=workers,thread_name_prefix='certified-research') as pool:
        try:
            while any(s in ('queued','active') for s in state.values()):
                for key,u in by_key.items():
                    if state[key]!='queued':continue
                    if any(state[p] in ('failed','blocked','interrupted') for p in u.parents):
                        report(key,'blocked','Required parent did not certify');continue
                    if stop.is_set():
                        report(key,'interrupted','Operator stop requested');continue
                    if any(state[p] not in ('certified','reused') for p in u.parents):continue
                    if len(active)>=workers or reserved+u.memory_bytes>memory_bytes:continue
                    parents={p:results[p] for p in u.parents}
                    try:
                        old=reuse(u,parents) if reuse is not None else None
                    except Exception as error:
                        errors[key]=error;report(key,'failed','Existing receipt verification failed');continue
                    if old is not None:
                        results[key]=old;report(key,'reused');continue
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
                        results[u.key]=value;report(u.key,'certified')
                    except Exception as error:
                        errors[u.key]=error;report(u.key,'failed','Producer failed; inspect bounded error receipt')
        except BaseException:
            stop.set()
            for future in active:future.cancel()
            # Exiting the pool waits for running handlers to reconcile owned writes.
            raise
    return dict(status='failed' if errors else 'interrupted' if stop.is_set() else 'complete',
                units=state,results=results,errors=errors)
