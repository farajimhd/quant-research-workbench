"""Stable listing-order receipt append without atomics or whole-buffer copies."""
import triton
import triton.language as tl


@triton.jit
def order_append_kernel(COMPLETE,RECORD,COUNTS,OUT,U:tl.constexpr,CAP:tl.constexpr,
           CS:tl.constexpr,RS:tl.constexpr,OS:tl.constexpr,BLOCK:tl.constexpr):
    row=tl.program_id(0);j=tl.arange(0,BLOCK)
    yes=tl.load(COMPLETE+row*CS[1]+j*CS[2],j<U,False)
    prefix=tl.cumsum(yes.to(tl.int32),0)-1
    old=tl.load(COUNTS+row);offset=old+prefix
    retain=yes&(offset<CAP)
    target=row*OS[0]+offset*OS[1]
    tl.store(OUT+target,j.to(tl.float64),retain)
    for field in tl.static_range(8):
        value=tl.load(RECORD+row*RS[1]+j*RS[2]+field*RS[3],j<U,0.)
        tl.store(OUT+target+field+1,value,retain)
    tl.store(COUNTS+row,old+tl.sum(yes.to(tl.int64),0))


def append(ledger,complete,record):
    order_append_kernel[(len(ledger.counts),)](complete,record,ledger.counts,ledger.data,
        ledger.u,ledger.capacity,complete.stride(),record.stride(),ledger.data.stride(),
        triton.next_power_of_2(ledger.u),num_warps=4)
