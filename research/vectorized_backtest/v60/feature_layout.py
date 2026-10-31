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
    if values.shape[1]==len(CATALOG):return values.sum(0)
    scratch=torch.zeros((values.shape[0],len(CATALOG)),device=values.device,dtype=values.dtype)
    return scratch.index_copy(1,columns,values).sum(0).index_select(0,columns)

def remap_sources(sources,edge_counts):
    from .genome import INPUTS,MARKET_INPUTS
    result=np.array(sources,copy=True)
    live=np.arange(result.shape[1])[None,:]<edge_counts[:,None]
    market=live&(result<MARKET_INPUTS)&(result>=0)
    lookup=np.full(len(CATALOG),-1,np.int32)
    lookup[list(COLUMNS)]=np.arange(WIDTH)
    mapped=lookup[result[market]%len(CATALOG)]
    if (mapped<0).any():raise ValueError('Forbidden feature in packed model')
    result[market]=(result[market]//len(CATALOG))*WIDTH+mapped
    if (live&((result<0)|((sources>=MARKET_INPUTS)&(sources<INPUTS)))).any():
        raise ValueError('Recurrent/position source in packed model')
    nodes=live&(sources>=INPUTS)
    result[nodes]=sources[nodes]-INPUTS+3*WIDTH+len(STATES)
    return result
