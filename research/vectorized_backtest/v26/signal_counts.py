"""One CUDA read of gates, five small integer outputs per candidate."""
import torch


def count(gates):
    if gates.ndim!=3 or gates.dtype not in (torch.uint8,torch.int16) or not gates.is_contiguous():
        raise ValueError('Contiguous candidate x clock x listing gate bank required')
    if gates.device.type!='cuda':
        if gates.dtype==torch.int16:gates=(gates&31)|((gates>>5)&31)
        return torch.stack([torch.count_nonzero(gates & (1<<bit),dim=(1,2)) for bit in range(5)],1)
    from .signal_counts_cuda import reduce
    return reduce(gates)
