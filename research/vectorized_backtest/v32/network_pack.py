"""CPU-only deterministic network packing; no CUDA calls or random draws."""
import numpy as np
from .genome import STAGES,OPS,MARKET_INPUTS
from .features import CATALOG
from .config import FAN_IN

def pack_networks(members):
    columns=list(range(len(CATALOG)));lookup={s:i for i,s in enumerate(columns)};c=len(columns)
    nets=[m.networks[s] for m in members for s in STAGES]
    counts=np.array([len(n.nodes) for n in nets],np.int32);offsets=np.cumsum(np.r_[0,counts[:-1]]).astype(np.int32)
    total_nodes=int(counts.sum());offsets_cpu=offsets;kernel_nodes=1<<(int(counts.max())-1).bit_length()
    sources=np.zeros((total_nodes,FAN_IN),np.int32);weights=np.zeros_like(sources,dtype=np.float32);bias=np.zeros(total_nodes,np.float32)
    edge_counts=np.zeros(total_nodes,np.int32)
    threshold=np.array([n.threshold for n in nets],np.float32)
    size_source=np.array([n.size_source if n.size_source>=0 else len(n.nodes)-1 for n in nets],np.int32)
    size_weight=np.array([n.size_weight for n in nets],np.float32);size_bias=np.array([n.size_bias for n in nets],np.float32)
    for index,net in enumerate(nets):
        net.validate()
        for j,node in enumerate(net.nodes):
            row=int(offsets[index])+j;bias[row]=node.bias;edge_counts[row]=len(node.sources)
            for e,(source,w) in enumerate(zip(node.sources,node.weights)):
                sources[row,e]=source if source<0 else (source//len(CATALOG))*c+lookup[source%len(CATALOG)] if source<MARKET_INPUTS else 3*c+source-MARKET_INPUTS
                weights[row,e]=w
    values=(sources,weights,bias,counts,offsets,threshold,size_source,size_weight,size_bias)
    return dict(values=values,operations=np.array([OPS.index(n.op) for net in nets for n in net.nodes],np.int32),
        edge_counts=edge_counts,total_nodes=total_nodes,kernel_nodes=kernel_nodes,offsets_cpu=offsets_cpu)
