"""One CUDA read of gates, five small integer outputs per candidate."""
import torch


def count(gates):
    if gates.ndim!=3 or gates.dtype!=torch.uint8 or not gates.is_contiguous():
        raise ValueError('Contiguous candidate x clock x listing uint8 gates required')
    if gates.device.type!='cuda':
        return torch.stack([torch.count_nonzero(gates & (1<<bit),dim=(1,2)) for bit in range(5)],1)
    from .signal_counts_cuda import reduce
    return reduce(gates)
