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
    # Every requested column is completely overwritten before reduction.
    # Unrequested scratch columns are independent reductions and never read by
    # the model. Avoid zeroing and the out-of-place index_copy's full clone.
    # Retain canonical geometry: compact geometry changes multi-CTA rounding.
    scratch=torch.empty((*values.shape[:-1],len(CATALOG)),device=values.device,dtype=values.dtype)
    scratch.index_copy_(-1,columns,values)
    return scratch.sum(-2).index_select(-1,columns)


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
