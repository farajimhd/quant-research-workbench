"""Write unique Boolean rule outputs directly into candidate gate banks."""
import triton
import triton.language as tl


@triton.jit
def scatter(SIGNAL,GATES,TARGET,SOURCE,CLOCK,TICKER,FIRST,MEMBER,FOLLOW,
            cells,listing_count,time_count,clocks,listings,signal_b,
            signal_l,signal_t,prefix,
            BIT:tl.constexpr,SHIFT:tl.constexpr,SPARSE:tl.constexpr,BLOCK:tl.constexpr):
    row=tl.program_id(0)
    x=tl.program_id(1).to(tl.int64)*BLOCK+tl.arange(0,BLOCK).to(tl.int64)
    live=x<cells
    if SPARSE:
        li=tl.full((BLOCK,),0,tl.int64);ti=x
        clock=tl.load(CLOCK+x,live,0);ticker=tl.load(TICKER+x,live,0)
        eligible=live
    else:
        li=x//time_count;ti=x%time_count
        clock=tl.load(CLOCK+ti,live,0);ticker=tl.load(TICKER+li,live,0)
        eligible=live & (clock>=tl.load(FIRST+ticker,live,0))
        if BIT==0:
            eligible=eligible & tl.load(MEMBER+clock*listings+ticker,live,0) & tl.load(FOLLOW+clock*listings+ticker,live,0)
        elif BIT!=4:
            eligible=eligible & tl.load(FOLLOW+clock*listings+ticker,live,0)
    source=tl.load(SOURCE+row).to(tl.int64)
    signal=tl.load(SIGNAL+source*signal_b+li*signal_l+(prefix+ti)*signal_t,live,0) & eligible
    target=tl.load(TARGET+row).to(tl.int64)
    address=GATES+target*clocks*listings+clock*listings+ticker
    before=tl.load(address,live,0)
    tl.store(address,before | (signal.to(tl.int32)<<(BIT+SHIFT)),live)


def write(gates,signal,target,source,clock,ticker,*,bit,shift=0,sparse=False,
          prefix=0,first=None,membership=None,following=None):
    """Targets and query coordinates are unique within one sequential write.

    Branch writes execute on the same stream; they cannot race on packed bits.
    No numerical operation or eligibility decision is approximated here.
    """
    count=clock.numel();lanes=1 if sparse else ticker.numel();cells=count*lanes
    if not cells or not target.numel():return
    dummy=clock
    scatter[(target.numel(),triton.cdiv(cells,256))](signal,gates,target,source,clock,ticker,
        first if first is not None else dummy,membership if membership is not None else dummy,
        following if following is not None else dummy,cells,lanes,count,gates.shape[1],gates.shape[2],
        *signal.stride(),prefix,bit,shift,sparse,256,num_warps=4)
