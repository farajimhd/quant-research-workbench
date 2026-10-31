"""CPU-only deterministic network packing; no CUDA calls or random draws."""
import numpy as np
from itertools import chain
from .genome import STAGES,OPS
from .config import FAN_IN

def pack_networks(members):
    nets=[m.networks[s] for m in members for s in STAGES]
    counts=np.array([len(n.nodes) for n in nets],np.int32);offsets=np.cumsum(np.r_[0,counts[:-1]]).astype(np.int32)
    total_nodes=int(counts.sum());offsets_cpu=offsets;kernel_nodes=1<<(int(counts.max())-1).bit_length()
    sources=np.zeros((total_nodes,FAN_IN),np.int32);weights=np.zeros_like(sources,dtype=np.float32);bias=np.zeros(total_nodes,np.float32)
    edge_counts=np.zeros(total_nodes,np.int32)
    threshold=np.array([n.threshold for n in nets],np.float32)
    size_source=np.array([n.size_source if n.size_source>=0 else len(n.nodes)-1 for n in nets],np.int32)
    size_weight=np.array([n.size_weight for n in nets],np.float32);size_bias=np.array([n.size_bias for n in nets],np.float32)
    for net in nets:net.validate()
    nodes=[node for net in nets for node in net.nodes]
    edge_counts=np.fromiter((len(n.sources) for n in nodes),np.int32,count=total_nodes)
    bias=np.fromiter((n.bias for n in nodes),np.float32,count=total_nodes)
    rows=np.repeat(np.arange(total_nodes),edge_counts)
    edge_offsets=np.cumsum(np.r_[0,edge_counts[:-1]])
    columns=np.arange(len(rows))-np.repeat(edge_offsets,edge_counts)
    sources[rows,columns]=np.fromiter(chain.from_iterable(n.sources for n in nodes),np.int32,count=len(rows))
    weights[rows,columns]=np.fromiter(chain.from_iterable(n.weights for n in nodes),np.float32,count=len(rows))
    values=(sources,weights,bias,counts,offsets,threshold,size_source,size_weight,size_bias)
    operations={op:i for i,op in enumerate(OPS)}
    return dict(values=values,operations=np.fromiter((operations[n.op] for n in nodes),np.int32,count=total_nodes),
        edge_counts=edge_counts,total_nodes=total_nodes,kernel_nodes=kernel_nodes,offsets_cpu=offsets_cpu)


def select_packed(packed, selection):
    """Gather an exact ordered cohort without walking/validating its graphs again."""
    selection=np.asarray(selection,dtype=np.int64)
    stages=len(STAGES)
    nets=(selection[:,None]*stages+np.arange(stages)).reshape(-1)
    values=packed['values'];counts=values[3][nets]
    offsets=np.cumsum(np.r_[0,counts[:-1]]).astype(np.int32)
    total=int(counts.sum())
    # Each source index is network-local. Only packed row offsets change.
    rows=np.repeat(values[4][nets].astype(np.int64)-offsets,counts)+np.arange(total)
    return dict(values=(values[0][rows],values[1][rows],values[2][rows],counts,offsets,
                       *(v[nets] for v in values[5:])),
        operations=packed['operations'][rows],edge_counts=packed['edge_counts'][rows],
        total_nodes=total,kernel_nodes=1<<(int(counts.max())-1).bit_length(),offsets_cpu=offsets)


def concatenate_packed(first,second):
    values=[np.concatenate((a,b)) for a,b in zip(first['values'],second['values'])]
    counts=values[3];values[4]=np.cumsum(np.r_[0,counts[:-1]]).astype(np.int32)
    return dict(values=tuple(values),operations=np.concatenate((first['operations'],second['operations'])),
        edge_counts=np.concatenate((first['edge_counts'],second['edge_counts'])),total_nodes=int(counts.sum()),
        kernel_nodes=max(first['kernel_nodes'],second['kernel_nodes']),offsets_cpu=values[4])
