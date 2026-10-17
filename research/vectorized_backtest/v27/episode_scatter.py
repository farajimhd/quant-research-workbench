"""One stable listing-prefix ledger write, including clocks, per CUDA tick."""
import triton
import triton.language as tl


@triton.jit
def append_kernel(CLOSED,PNL,ENTRY,EXIT,PROFITS,LISTINGS,COUNTS,ENTRIES,EXITS,
                  U:tl.constexpr,CAPACITY:tl.constexpr,WIDTH:tl.constexpr,
                  CS:tl.constexpr,PS:tl.constexpr,ES:tl.constexpr,OUT:tl.constexpr,
                  B:tl.constexpr,TRACE:tl.constexpr,BLOCK:tl.constexpr):
    row=tl.program_id(0);session=row//B;candidate=row%B;j=tl.arange(0,BLOCK)
    closed=tl.load(CLOSED+session*CS[0]+candidate*CS[1]+j*CS[2],j<U,False)
    prefix=tl.cumsum(closed.to(tl.int32),0)-1
    old=tl.load(COUNTS+row);index=old+prefix;retain=closed&(index<CAPACITY)
    profit=tl.load(PNL+session*PS[0]+candidate*PS[1]+j*PS[2],j<U,0.)
    target=session*OUT[0]+candidate*OUT[1]+index*OUT[2]
    tl.store(PROFITS+target,profit,retain);tl.store(LISTINGS+target,j,retain)
    if TRACE:
        entry=tl.load(ENTRY+session*ES[0]+candidate*ES[1]+j*ES[2],j<U,0).to(tl.int64)
        exit=tl.load(EXIT).to(tl.int64)
        tl.store(ENTRIES+target,entry,retain);tl.store(EXITS+target,exit,retain)
    # Count every event even on overflow; export then fails closed.
    tl.store(COUNTS+row,old+tl.sum(closed.to(tl.int64),0))


def append(episodes,closed,profits,entry,exit):
    s,b,u=closed.shape
    append_kernel[(s*b,)](closed,profits,entry if episodes.trace else profits,
        exit if episodes.trace else profits,episodes.profits,episodes.listings,episodes.counts,
        episodes.entry_clocks if episodes.trace else episodes.listings,
        episodes.exit_clocks if episodes.trace else episodes.listings,
        u,episodes.capacity,episodes.profits.shape[-1],closed.stride(),profits.stride(),
        entry.stride() if episodes.trace else profits.stride(),episodes.profits.stride(),b,episodes.trace,
        triton.next_power_of_2(u),num_warps=4)
