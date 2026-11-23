"""Adapt certified Phase1 windows without mixing their bars in the encoder."""
from ..phase1_v7.full_data import gpu_inputs


def forward_batch(model, batch):
    """Prepare canonical logs/time metadata and independently reconstruct each bar.

The loader may deliver (windows,64,27). The model treats both leading axes as
independent bar examples; the length64 dimension is NEVER an attention axis.
Raw targets remain in batch.raw, and original validity remains in batch.valid.
"""
    values, valid, _ = gpu_inputs(batch)
    return model(values, valid), values, valid
