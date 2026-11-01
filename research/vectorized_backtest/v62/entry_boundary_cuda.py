"""Fused backend of authority.entry_stop; qualified against its tensor oracle."""
import triton
import triton.language as tl
from .authority import STOP_MARGIN,BOUNDED_SWING


@triton.jit
def entry_stop(ask,swing,fraction,offset,mode,
               MARGIN:tl.constexpr=STOP_MARGIN,BOUND:tl.constexpr=BOUNDED_SWING):
    fixed=ask*(1-fraction)
    preferred=tl.where((tl.abs(swing)<float('inf'))&(swing>0),swing,fixed)
    ceiling=tl.minimum(ask,ask*(1+offset))*(1-MARGIN)
    return tl.where(mode>=BOUND,tl.minimum(preferred,ceiling),tl.where(mode>0,swing,fixed))
