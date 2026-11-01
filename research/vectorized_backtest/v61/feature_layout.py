"""One compact model layout; persistent genomes retain canonical feature IDs."""
import numpy as np
from .authority import ALLOWED
from .features import CATALOG
from .config import STATES

COLUMNS=tuple(map(int,ALLOWED))
WIDTH=len(COLUMNS)
LOOKUP={source:i for i,source in enumerate(COLUMNS)}

def column_sum(values,columns):
    """Preserve established per-column reduction rounding after compaction.

    This is arithmetic scratch, never a model input or a source-feature bank.
    Forbidden source values are not read. A future fused reducer can remove
    this scratch only after proving the same reduction tree and rounding.
    """
    import torch
    if values.shape[-1]==len(CATALOG):return values.sum(-2)
    if values.is_cuda and values.dtype==torch.float32 and values.shape[-2]<4096:
        # Match ATen's canonical strided reduction: >=512 outputs preserve the
        # block geometry; an odd row pitch forces scalar (not vectorized) loads.
        # Below 4096 rows there is no multi-CTA reduction for this layout.
        # Only the requested columns are returned. Unknown shapes use the oracle.
        width=max(512,values.shape[-1]);pitch=width|1
        shape=values.shape[:-1]+(width,)
        strides=[1]*len(shape);strides[-2]=pitch
        for i in range(len(shape)-3,-1,-1):strides[i]=strides[i+1]*shape[i+1]
        scratch=torch.empty_strided(shape,tuple(strides),device=values.device,dtype=values.dtype)
        scratch.zero_();scratch[...,:values.shape[-1]].copy_(values)
        return scratch.sum(-2)[...,:values.shape[-1]]
    scratch=torch.zeros((*values.shape[:-1],len(CATALOG)),device=values.device,dtype=values.dtype)
    return scratch.index_copy(-1,columns,values).sum(-2).index_select(-1,columns)

def required_columns(members):
    """Generation-wide dependency union, including every routing leaf."""
    from .strategy_mix import leaves
    from .genome import MARKET_INPUTS
    used={s%len(CATALOG) for m in members for leaf in leaves(m)
          for net in leaf.networks.values() for n in net.nodes
          for s in n.sources if 0<=s<MARKET_INPUTS}
    if not used.issubset(LOOKUP):raise ValueError('Forbidden population feature')
    return tuple(sorted(used))


def remap_sources(sources,edge_counts,columns=COLUMNS):
    from .genome import INPUTS,MARKET_INPUTS
    result=np.array(sources,copy=True)
    live=np.arange(result.shape[1])[None,:]<edge_counts[:,None]
    market=live&(result<MARKET_INPUTS)&(result>=0)
    lookup=np.full(len(CATALOG),-1,np.int32)
    width=len(columns)
    lookup[list(columns)]=np.arange(width)
    mapped=lookup[result[market]%len(CATALOG)]
    if (mapped<0).any():raise ValueError('Forbidden feature in packed model')
    result[market]=(result[market]//len(CATALOG))*width+mapped
    if (live&((result<0)|((sources>=MARKET_INPUTS)&(sources<INPUTS)))).any():
        raise ValueError('Recurrent/position source in packed model')
    nodes=live&(sources>=INPUTS)
    result[nodes]=sources[nodes]-INPUTS+3*width+len(STATES)
    return result
