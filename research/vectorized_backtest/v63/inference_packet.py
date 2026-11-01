"""Copy one market-only packet row without intermediate index/gather tensors."""
import triton as tr
import triton.language as tl


@tr.jit
def consume_kernel(INDEX,SIGNAL,RATIO,OWNER,OUT_SIGNAL,OUT_RATIO,OUT_OWNER,
                   BRANCH,POLICIES,COLUMNS,OUT_POLICY,OUT_COLUMN,
                   N:tl.constexpr,U:tl.constexpr,PACKET:tl.constexpr,
                   ROUTED:tl.constexpr,BRANCH_STRIDE:tl.constexpr,BLOCK:tl.constexpr):
    i=tl.program_id(0)*BLOCK+tl.arange(0,BLOCK);live=i<N
    row=tl.load(INDEX)%PACKET;source=row*N+i
    tl.store(OUT_SIGNAL+i,tl.load(SIGNAL+source,live,0),live)
    for k in tl.static_range(3):
        tl.store(OUT_RATIO+i*3+k,tl.load(RATIO+source*3+k,live,0),live)
    if ROUTED:
        owner=tl.load(OWNER+source,live,0)
        leaf=tl.load(BRANCH+(i//U)*BRANCH_STRIDE+owner,live,0)
        tl.store(OUT_OWNER+i,owner,live)
        tl.store(OUT_COLUMN+i,tl.load(COLUMNS+leaf,live,0),live)
        for k in tl.static_range(11):
            tl.store(OUT_POLICY+i*11+k,tl.load(POLICIES+leaf*11+k,live,0),live)


def consume(pipeline,index):
    p=pipeline.plan;b,u=pipeline.signals.shape[1:];routed=pipeline.routed
    consume_kernel[(tr.cdiv(b*u,256),)](index,pipeline.active[0],pipeline.active[1],pipeline.active[4],
        pipeline.signals,p.ratios,p.owner if routed else None,
        p.branch_index if routed else None,p.policies if routed else None,p.leaf_columns if routed else None,
        pipeline.policy,pipeline.columns,b*u,u,pipeline.block,routed,
        p.branch_index.stride(0) if routed else 0,256,num_warps=4)
