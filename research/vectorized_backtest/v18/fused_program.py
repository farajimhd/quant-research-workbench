"""One CUDA instruction kernel per DAG row; exact existing rolling reductions."""
import torch
import triton
import triton.language as tl
from torch.nn import functional as F
from .program import TorchPrograms,Op


@triton.jit
def instruction(X,K,V,M,OPS,A,B,FI,W,C,ACTIVE,rows,candles,features,candidates,index,BLOCK:tl.constexpr,HAS_STD:tl.constexpr):
    candidate=tl.load(ACTIVE+tl.program_id(0));r=tl.program_id(1)*BLOCK+tl.arange(0,BLOCK);live=r<rows
    gene=index*candidates+candidate;op=tl.load(OPS+gene)
    output=(index*candidates+candidate)*rows+r
    value=tl.full((BLOCK,),0,tl.float32);known=tl.full((BLOCK,),False,tl.int1)
    if op==0:
        f=tl.load(FI+gene);value=tl.load(X+r*features+f,live,0);known=tl.load(K+r*features+f,live,0)
    elif op==1:
        value=tl.full((BLOCK,),1,tl.float32)*tl.load(C+gene);known=live
    else:
        ai=tl.load(A+gene);bi=tl.load(B+gene)
        ap=(ai*candidates+candidate)*rows+r;bp=(bi*candidates+candidate)*rows+r
        a=tl.load(V+ap,live,0);av=tl.load(M+ap,live,0)
        b=tl.load(V+bp,live,0);bv=tl.load(M+bp,live,0)
        known=av & bv
        if op==2:value=a+b
        elif op==3:value=a-b
        elif op==4:value=a*b
        elif op==5:
            nonzero=tl.abs(b)>1.e-12;known=known & nonzero
            value=tl.div_rn(a,tl.where(nonzero,b,1.))
        elif op==6:value=(a>b).to(tl.float32)
        elif op==7:value=(a>=b).to(tl.float32)
        elif op==8:value=(a<b).to(tl.float32)
        elif op==9:value=(a<=b).to(tl.float32)
        elif op==10:value=((a!=0)&(b!=0)).to(tl.float32)
        elif op==11:value=((a!=0)|(b!=0)).to(tl.float32)
        elif op==12:value=(a==0).to(tl.float32);known=av
        elif op==13 or op==17:
            window=tl.load(W+gene);past=live & ((r%candles)>=window)
            prior=tl.load(V+ap-window,past,0);prior_known=tl.load(M+ap-window,past,0)
            value=prior if op==13 else a-prior
            known=prior_known & past
            if op==17:known=known & av
        elif op==15 or op==16:
            # Extrema select existing float32 operands; unlike a mean there is
            # no summation order to change. Fuse validity and the bounded scan.
            window=tl.load(W+gene);known=live & ((r%candles)>=window-1)
            value=tl.full((BLOCK,),float('inf'),tl.float32) if op==15 else tl.full((BLOCK,),-float('inf'),tl.float32)
            for offset in range(window):
                past=live & ((r%candles)>=offset)
                prior=tl.load(V+ap-offset,past,0);mask=tl.load(M+ap-offset,past,0)
                known=known & mask & past
                value=tl.minimum(value,prior) if op==15 else tl.maximum(value,prior)
        elif HAS_STD and op==23:
            window=tl.load(W+gene);known=live & ((r%candles)>=window-1)
            mean=tl.full((BLOCK,),0,tl.float32);m2=tl.full((BLOCK,),0,tl.float32)
            for offset in range(window):
                past=live & ((r%candles)>=offset)
                prior=tl.load(V+ap-offset,past,0);mask=tl.load(M+ap-offset,past,0)
                known=known & mask & past
                delta=prior-mean;mean+=delta/(offset+1);m2+=delta*(prior-mean)
            value=tl.sqrt(tl.maximum(m2/window,0.))
        elif op==22:
            # Direct Boolean rates have exact integer cumulative sums. They
            # need no float32 prefix scan and can share the extrema scan kernel.
            window=tl.load(W+gene);known=live & ((r%candles)>=window-1)
            value=tl.full((BLOCK,),0,tl.float32)
            for offset in range(window):
                past=live & ((r%candles)>=offset)
                value+=tl.load(V+ap-offset,past,0)
                known=known & tl.load(M+ap-offset,past,0) & past
            value=value*tl.load(C+gene)
        elif op==18 or op==19:
            past=live & ((r%candles)>0)
            pa=tl.load(V+ap-1,past,0);pb=tl.load(V+bp-1,past,0)
            known=known & tl.load(M+ap-1,past,0) & tl.load(M+bp-1,past,0) & past
            value=((a>b)&(pa<=pb)).to(tl.float32) if op==18 else ((a<b)&(pa>=pb)).to(tl.float32)
        elif op==20:value=tl.abs(a);known=av
        elif op==21:value=(a!=0).to(tl.float32);known=av
        # MEAN is overwritten by the exact Torch cumulative reduction below.
    known=known & (tl.abs(value)<float('inf'))
    tl.store(V+output,tl.where(known,value,0.),live);tl.store(M+output,known,live)


class FusedPrograms(TorchPrograms):
    def __init__(self,programs,catalog,device,output_unit='bool',*,literal_catalog=None):
        super().__init__(programs,catalog,device,output_unit=output_unit,literal_catalog=literal_catalog)
        from .program import Node
        rows=[[p.nodes[i] if i<len(p.nodes) else Node(Op.CONSTANT) for p in programs] for i in range(self.width)]
        self.has_std=[any(n.op==Op.STD for n in row) for row in rows]
        # Do not change candidate ordering or moving-mean scan batch shapes.
        # Padding nodes cannot be outputs or operands of any actual program.
        self.active_rows=[torch.tensor([j for j,p in enumerate(programs) if i<len(p.nodes)],
                                       device=device,dtype=torch.int32) for i in range(self.width)]
        self.boolean_means=set()
        for i,row in enumerate(rows):
            for j,n in enumerate(row):
                if n.op==Op.MEAN and programs[j].nodes[n.a].op==Op.BOOL_RATIO:
                    self.boolean_means.add((i,j))
        # Preserve the original scan batch shape whenever a window group also
        # contains numeric means: that shape can affect CUDA prefix rounding.
        for i,row in enumerate(rows):
            for k in {n.window for n in row if n.op==Op.MEAN}:
                members=[j for j,n in enumerate(row) if n.op==Op.MEAN and n.window==k]
                if any((i,j) not in self.boolean_means for j in members):
                    self.boolean_means.difference_update((i,j) for j in members)
        self.genes=[torch.tensor([[getattr(n,key) if key not in ('a','b','feature') else max(0,getattr(n,key))
                                  for n in row] for row in rows],device=device,dtype=torch.float32 if key=='value' else torch.int32)
                    for key in ('op','a','b','feature','window','value')]
        for i,j in self.boolean_means:
            self.genes[0][i,j]=22;self.genes[5][i,j]=1./rows[i][j].window
        self.numeric_means={}
        for i,row in enumerate(rows):
            groups=[]
            for k in sorted({n.window for j,n in enumerate(row) if n.op==Op.MEAN and (i,j) not in self.boolean_means}):
                indices=torch.tensor([j for j,n in enumerate(row) if n.op==Op.MEAN and n.window==k and (i,j) not in self.boolean_means],device=device)
                groups.append((k,indices,self.rows[i][2].index_select(0,indices)))
            self.numeric_means[i]=groups

    def __call__(self,features,valid):
        if features.dtype!=torch.float32 or not features.is_contiguous() or not valid.is_contiguous():
            raise ValueError('Fused rules require contiguous float32 features and masks')
        b=len(self.programs);c=features.shape[-2];shape=(b,*features.shape[:-1]);rows=features.numel()//features.shape[-1]
        values=features.new_empty((self.width,*shape));masks=torch.empty_like(values,dtype=torch.bool)
        axis=torch.arange(c,device=features.device)[None]
        for i in range(self.width):
            instruction[(self.active_rows[i].numel(),triton.cdiv(rows,128))](features,valid,values,masks,*self.genes,self.active_rows[i],
                rows,c,features.shape[-1],b,i,BLOCK=128,HAS_STD=self.has_std[i],enable_fp_fusion=False)
            # Keep precisely the old cumsum and pooling order, including float32
            # rounding and missing-evidence propagation. Never approximate a mean.
            for k,indices,ai in self.numeric_means[i]:
                    source=values[ai,indices];known=masks[ai,indices]
                    safe=torch.where(known,source,0.)
                    sums=F.pad(safe.cumsum(-1),(1,0));counts=F.pad(known.to(features.dtype).cumsum(-1),(1,0))
                    starts=(torch.arange(c,device=features.device)+1-k).clamp_min(0)
                    mv=((counts[...,1:]-counts[...,starts])==k)&(axis>=k-1)
                    z=source if k==1 else (sums[...,1:]-sums[...,starts])/k
                    mv=mv&torch.isfinite(z)
                    values[i].index_copy_(0,indices,torch.where(mv,z,0.));masks[i].index_copy_(0,indices,mv)
        return values[self.outputs,self.batch_axis],masks[self.outputs,self.batch_axis]
