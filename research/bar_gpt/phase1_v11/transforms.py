"""Information-preserving market coordinates; references from visible data only.

Input logs are log(price), log1p(activity), plus the canonical nine time fields.
For a partial OHLC, fill only absent fields from observed values to construct
coordinates. Original source masks remain the reconstruction authority.
"""
import torch
from functools import lru_cache

PRICES=tuple(i for b in (0,6,12) for i in range(b,b+4))
ACTIVITY=tuple(i for b in (0,6,12) for i in (b+4,b+5))
LEVELS=(0,4,5,6,10,11,12,16,17)
REF_PRICES=(0,3,6)
REF_ACTIVITY=(1,2,4,5,7,8)


@lru_cache(maxsize=8)
def order_relation(paired,device):
    n=8 if paired else 4
    relation=[[i==j for j in range(n)] for i in range(n)]
    for b in range(0,n,4):
        for low,high in ((2,0),(2,3),(0,1),(3,1)):relation[b+high][b+low]=True
    if paired:
        for j in range(4):relation[j+4][j]=True
    for k in range(n):
        for i in range(n):
            for j in range(n):relation[i][j]=relation[i][j] or (relation[i][k] and relation[k][j])
    return torch.tensor(relation,device=device,dtype=torch.bool)


def ordered_fill(values,valid,paired=False):
    # Immutable device constant is initialized during warmup, before graph capture.
    relation=order_relation(paired,values.device)
    bounds=torch.where(valid.unsqueeze(-2)&relation,values.unsqueeze(-2),float('-inf')).amax(-1)
    fallback=torch.where(valid,values,float('inf')).amin(-1,keepdim=True)
    fallback=torch.where(valid.any(-1,keepdim=True),fallback,0)
    return torch.where(valid,values,torch.where(torch.isfinite(bounds),bounds,fallback))


def coordinates(logs,valid):
    trade=ordered_fill(logs[...,:4],valid[...,:4])
    quote=ordered_fill(torch.cat((logs[...,6:10],logs[...,12:16]),-1),torch.cat((valid[...,6:10],valid[...,12:16]),-1),True)
    cols=[];masks=[]
    for b in (0,6,12):
        filled=trade if b==0 else quote[...,0:4] if b==6 else quote[...,4:8]
        of,hf,lf,cf=filled.unbind(-1);any_price=valid[...,b:b+4].any(-1)
        cols.extend((cf,of-cf,hf-torch.maximum(of,cf),torch.minimum(of,cf)-lf,logs[...,b+4],logs[...,b+5]))
        masks.extend((any_price,any_price,any_price,any_price,valid[...,b+4],valid[...,b+5]))
    return torch.stack(cols,-1),torch.stack(masks,-1)


def median(values,valid,fallback):
    # Conventional average of the two central observations, valid zeros retained.
    count=valid.sum(1);sorted_values=torch.where(valid,values,float('inf')).sort(1).values
    lo=((count-1).clamp_min(0)//2).unsqueeze(1);hi=(count//2).unsqueeze(1)
    result=.5*(sorted_values.gather(1,lo).squeeze(1)+sorted_values.gather(1,hi).squeeze(1))
    return torch.where(count>0,result,fallback),count>0


def references(logs,valid,fallback):
    # s references use actually observed closes, not imputed closes.
    ix=(3,4,5,9,10,11,15,16,17)
    return median(logs[...,ix],valid[...,ix],fallback)


def center(code,reference):
    # Shape d/u/l do not depend on the window references.
    columns=[code[...,j]-reference[:,None,LEVELS.index(j)] if j in LEVELS else code[...,j] for j in range(18)]
    return torch.stack(columns,-1)


def decode(code,reference,constrain=True,pair_quotes=True):
    columns=[]
    for f,b in enumerate((0,6,12)):
        s=reference[:,None,f*3]+code[...,b];d=code[...,b+1]
        u=code[...,b+2];l=code[...,b+3]
        if constrain:u=u.abs();l=l.abs()
        o=s+d;c=s;h=torch.maximum(o,c)+u;low=torch.minimum(o,c)-l
        q=reference[:,None,f*3+1]+code[...,b+4];n=reference[:,None,f*3+2]+code[...,b+5]
        if constrain:q=q.abs();n=n.abs()
        columns.extend((o,h,low,c,q,n))
    result=torch.stack(columns,-1)
    if pair_quotes:
        # Projection of predictions only, exact identity on ordered source quotes.
        bid=result[...,6:10];ask=result[...,12:16]
        result=torch.cat((result[...,:6],torch.minimum(bid,ask),result[...,10:12],torch.maximum(bid,ask),result[...,16:]),-1)
    return result


def inverse(logs):
    return torch.stack([logs[...,j].double().exp() if j in PRICES else logs[...,j].double().expm1() for j in range(18)],-1)

